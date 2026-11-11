"""Scheduled job: pick a topic, draft a post, open a `draft` issue for review.

    python -m poster.draft                 # what the schedule runs
    DRAFT_KIND=fact python -m poster.draft # force a kind: auto | news | fact | onthisday
    DRY_RUN=1 python -m poster.draft       # print the issue instead of creating it
"""
import os
import sys
from datetime import date, datetime, timezone

from . import compose, gh, images, sources
from .config import load_config, load_profile, today

DRY = os.environ.get("DRY_RUN") == "1"
KINDS = ("news", "fact", "onthisday")   # the automatic kinds config.yml -> rotation may list


class History:
    """What earlier drafts mean for this run, read back from the draft issues themselves.

    - open, published or simulated drafts: their story/article is skipped (no duplicates, no reposts)
    - rejected: skipped only for review.reject_cooldown_days, then eligible again
    - blocked: the story/article is skipped forever; blocked-site: every story from that website is
    """

    def __init__(self, issues, cooldown_days, now):
        self.skip_hn, self.skip_wiki, self.skip_otd, self.blocked_wiki, self.blocked_sites, kinds = set(), set(), set(), set(), set(), []
        self.image_kinds = []  # newest first; drafts from before images have none and are left out
        for i in issues:
            labels = {l["name"] for l in i.get("labels", [])}
            img = gh.parse_image(i.get("body"))
            if img:
                self.image_kinds.append(img.get("kind") or "none")
            t = gh.parse_meta(i.get("body")).get("topic", {})
            if t.get("kind") in KINDS:
                kinds.append(t["kind"])  # newest first
            if "blocked-site" in labels and t.get("source_url"):
                self.blocked_sites.add(sources.host(t["source_url"]))
            if "blocked" in labels or "blocked-site" in labels:
                skip = True
                if t.get("wiki_subject"):
                    self.blocked_wiki.add(t["wiki_subject"])
            elif "rejected" in labels:
                closed = i.get("closed_at")
                skip = bool(closed) and (now - datetime.fromisoformat(closed.replace("Z", "+00:00"))).days < cooldown_days
            else:
                skip = True
            if skip and t.get("hn_id"):
                self.skip_hn.add(str(t["hn_id"]))
            if skip and t.get("wiki_subject"):
                self.skip_wiki.add(t["wiki_subject"])
            if skip and t.get("otd_id"):
                self.skip_otd.add(t["otd_id"])
        self.last_kind = kinds[0] if kinds else None


def history(cfg):
    days = int((cfg.get("review") or {}).get("reject_cooldown_days", 7))
    return History(gh.issues("draft"), days, datetime.now(timezone.utc))


def next_queued():
    waiting = gh.issues("topic", state="open")
    if not waiting:
        return None
    i = waiting[-1]  # oldest first: a queue
    return {"kind": "queued", "title": i["title"].strip(), "category": "personal", "angle": (i.get("body") or "").strip(),
            "topic_issue": i["number"]}


def kind_order(cfg, last, forced):
    rot = cfg["rotation"]
    if forced in KINDS:  # a forced kind runs first even when it is not in the rotation
        return [forced] + [k for k in rot if k != forced]
    start = (rot.index(last) + 1) % len(rot) if last in rot else 0
    return rot[start:] + rot[:start]


def pick(cfg, h, kind):
    if kind == "news":
        return sources.pick_news(cfg, h.skip_hn, h.blocked_sites)
    if kind == "onthisday":
        return sources.pick_onthisday(cfg, h.skip_otd, today(cfg))
    if kind == "fact":
        return sources.pick_fact(cfg, h.skip_wiki, blocked=h.blocked_wiki)
    print(f"unknown kind {kind!r} in rotation, skipping")
    return None


def author_for(cfg, profile, kind):
    if kind == "queued":
        return profile
    keys = cfg.get("general_post_profile_keys") or ["name", "headline"]
    return {k: v for k, v in profile.items() if k in keys}


def token_warnings(cfg):
    if cfg["publish_mode"] != "linkedin":
        return []
    raw = os.environ.get("LINKEDIN_TOKEN_EXPIRES", "").strip()
    if not raw:
        return ["Repo variable LINKEDIN_TOKEN_EXPIRES is not set, so token expiry cannot be tracked."]
    try:
        left = (date.fromisoformat(raw) - today(cfg)).days
    except ValueError:
        return [f"LINKEDIN_TOKEN_EXPIRES={raw!r} is not a YYYY-MM-DD date."]
    if left > cfg["linkedin"]["token_warn_days"]:
        return []
    msg = f"The LinkedIn token expires in {left} days ({raw})." if left >= 0 else f"The LinkedIn token expired on {raw}; /approve will fail until it is renewed."
    if not DRY and not gh.issues("token-expiry", state="open"):
        gh.create_issue("Renew the LinkedIn access token", msg + "\n\nGenerate a new token (see README, *LinkedIn token*), update the "
                        "`LINKEDIN_ACCESS_TOKEN` secret and the `LINKEDIN_TOKEN_EXPIRES` variable, then close this issue.", ["token-expiry"])
    return [msg]


def picture(cfg, h, topic, draft, profile, title, notes):
    """Make and store the draft's image. Never stops the draft: a failure means no image and a review note."""
    if not (cfg.get("images") or {}).get("enabled", True):
        return None
    want = images.choose(cfg, h.image_kinds)
    try:
        pic = images.make(cfg, want, topic, draft.post_text_he, draft.post_text, profile)
        notes.extend(pic.notes)
        if DRY:
            path = os.path.join(os.environ.get("RUNNER_TEMP") or "/tmp", f"post-image.{pic.ext}")
            with open(path, "wb") as f:
                f.write(pic.data)
            print(f"image ({pic.kind}) saved to {path}" + (f"; prompt: {pic.prompt}" if pic.prompt else ""))
            return {"kind": pic.kind, "path": path, "branch": "local", "alt": pic.alt, "prompt": pic.prompt}
        return images.store(cfg, pic, title)
    except Exception as e:  # noqa: BLE001 - an image problem must never cost the draft
        notes.append(f"No image: {type(e).__name__}: {str(e)[:200]}. Comment `/image` to try again.")
        return {"kind": "none"}


def main():
    cfg, profile = load_config(), load_profile()
    if not profile:
        print("warning: no profile (PROFILE_YML secret or profile.yml); posts will not sound like you")
    if not DRY:
        gh.ensure_labels()
    h = history(cfg)
    forced = os.environ.get("DRAFT_KIND", "auto").strip()

    picked, queued = None, next_queued() if forced in ("auto", "") else None
    if queued:
        picked = (queued, [])
    else:
        for kind in kind_order(cfg, h.last_kind, forced):
            picked = pick(cfg, h, kind)
            if picked:
                break
            print(f"no usable {kind} source this run, trying the next kind")
    if not picked:
        sys.exit("no topic could be sourced (no queued topic, no readable news, no Wikipedia article, no relevant event on this date)")
    topic, news = picked
    print(f"topic: [{topic['kind']}] {topic['title']}")

    extra = token_warnings(cfg)
    if topic["kind"] == "onthisday":
        d = date.fromisoformat(topic["otd_date"])
        extra.append(f"This post is tied to {d.day} {d.strftime('%B')}. Approve it that day; after it, `/approve` asks you to confirm.")
    if topic["kind"] == "queued" and not profile:
        extra.append("Your topic was drafted without a profile, so the model only had the title and notes.")
    try:
        draft = compose.write(cfg, author_for(cfg, profile, topic["kind"]), topic, news)
    except RuntimeError as e:
        if topic["kind"] == "queued" and not DRY:
            gh.comment(topic["topic_issue"], f":warning: Could not draft this topic: {e}. It stays queued; edit the notes or try again.")
        sys.exit(str(e))

    title = f"Post draft: {topic['title']}"
    image = picture(cfg, h, topic, draft, profile, topic["title"], extra)
    body = gh.render_draft(topic, news, draft, extra, image=image)
    if DRY:
        print("=" * 72, "\n" + title + "\n", body, sep="\n")
        return
    issue = gh.create_issue(title, body, ["draft"])
    print(f"opened #{issue['number']}: {issue['html_url']}")
    if topic["kind"] == "queued":
        gh.comment(topic["topic_issue"], f"Drafted in #{issue['number']}.")
        gh.close(topic["topic_issue"])


if __name__ == "__main__":
    main()
