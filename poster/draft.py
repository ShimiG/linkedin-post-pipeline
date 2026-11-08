"""Scheduled job: pick a topic, draft a post, open a `draft` issue for review.

    python -m poster.draft                 # what the schedule runs
    DRAFT_KIND=fact python -m poster.draft # force a kind: auto | news | fact
    DRY_RUN=1 python -m poster.draft       # print the issue instead of creating it
"""
import os
import sys
from datetime import date

from . import compose, gh, sources
from .config import load_config, load_profile, today

DRY = os.environ.get("DRY_RUN") == "1"


def history():
    """What has been drafted before, read back from the draft issues themselves."""
    used_hn, used_wiki, kinds = set(), set(), []
    for i in gh.issues("draft"):
        t = gh.parse_meta(i.get("body")).get("topic", {})
        if t.get("hn_id"):
            used_hn.add(str(t["hn_id"]))
        if t.get("wiki_subject"):
            used_wiki.add(t["wiki_subject"])
        if t.get("kind") in ("news", "fact"):
            kinds.append(t["kind"])  # newest first
    return used_hn, used_wiki, (kinds[0] if kinds else None)


def next_queued():
    waiting = gh.issues("topic", state="open")
    if not waiting:
        return None
    i = waiting[-1]  # oldest first: a queue
    return {"kind": "queued", "title": i["title"].strip(), "category": "personal", "angle": (i.get("body") or "").strip(),
            "topic_issue": i["number"]}


def kind_order(cfg, last, forced):
    rot = cfg["rotation"]
    if forced in rot:
        return [forced] + [k for k in rot if k != forced]
    start = (rot.index(last) + 1) % len(rot) if last in rot else 0
    return rot[start:] + rot[:start]


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


def main():
    cfg, profile = load_config(), load_profile()
    if not profile:
        print("warning: no profile (PROFILE_YML secret or profile.yml); posts will not sound like you")
    if not DRY:
        gh.ensure_labels()
    used_hn, used_wiki, last_kind = history()
    forced = os.environ.get("DRAFT_KIND", "auto").strip()

    picked, queued = None, next_queued() if forced in ("auto", "") else None
    if queued:
        picked = (queued, [])
    else:
        for kind in kind_order(cfg, last_kind, forced):
            picked = sources.pick_news(cfg, used_hn) if kind == "news" else sources.pick_fact(cfg, used_wiki)
            if picked:
                break
            print(f"no usable {kind} source this run, trying the next kind")
    if not picked:
        sys.exit("no topic could be sourced (no queued topic, no readable news, no Wikipedia article)")
    topic, news = picked
    print(f"topic: [{topic['kind']}] {topic['title']}")

    extra = token_warnings(cfg)
    if topic["kind"] == "queued" and not profile:
        extra.append("Your topic was drafted without a profile, so the model only had the title and notes.")
    try:
        draft = compose.write(cfg, author_for(cfg, profile, topic["kind"]), topic, news)
    except RuntimeError as e:
        if topic["kind"] == "queued" and not DRY:
            gh.comment(topic["topic_issue"], f":warning: Could not draft this topic: {e}. It stays queued; edit the notes or try again.")
        sys.exit(str(e))

    body = gh.render_draft(topic, news, draft, extra)
    title = f"Post draft: {topic['title']}"
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
