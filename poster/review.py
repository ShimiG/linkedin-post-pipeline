"""Runs when the repo owner comments on a `draft` issue: /approve [anyway], /schedule <when>, /unschedule, /reject, /rewrite <instruction>, /image [card|ai|none].

Inputs come from the workflow as env vars (never interpolated into shell): ISSUE_NUMBER, COMMENT_BODY.
"""
import os
import re
import sys
from datetime import date, datetime

from . import compose, examples, gh, images, linkedin, sources
from .config import load_config, load_profile, today, tz
from .http import HttpError
from .draft import author_for

LINKEDIN_MAX = 3000
WHEN_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})[ ,]+(\d{1,2}):(\d{2})(?:\s+(anyway))?$", re.I)
SCHEDULE_CANCELLED = "The schedule is cancelled. Fix it, then comment `/approve` to publish now or `/schedule dd/mm/yyyy HH:MM` again."
HELP = "Commands: **`/approve`** publishes the text between the markers (Hebrew first, then English), **`/schedule dd/mm/yyyy HH:MM`** publishes it at that time (**`/unschedule`** cancels), **`/reject`** drops this post, **`/block`** (or **`/block site`**) drops it and never uses that source again, **`/rewrite <what to change>`** asks the AI for a new version, **`/image`** makes a new image (**`/image card`**, **`/image ai`**, **`/image none`**), **`/example`** saves it as a writing example."


def parse_command(text):
    first = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not first.startswith("/"):
        return None, ""
    cmd, _, arg = first.partition(" ")
    return cmd.lower(), arg.strip()


def off_date(cfg, topic, on=None):
    """The date an "On this day" draft was written for, when that is not the month and day of `on` (today); else ''."""
    if topic.get("kind") != "onthisday" or not topic.get("otd_date"):
        return ""
    try:
        d = date.fromisoformat(topic["otd_date"])
    except ValueError:
        return ""
    t = on or today(cfg)
    return "" if (d.month, d.day) == (t.month, t.day) else f"{d.day} {d.strftime('%B')}"


def post_of(cfg, issue):
    """(text to publish, '') or ('', what is wrong with it)."""
    post = compose.combine(cfg, *gh.parse_parts(issue.get("body")))
    if not post:
        return "", ":x: I can't find the post. Keep the `<!-- post-…:start -->` / `<!-- post-…:end -->` markers around the text."
    if len(post) > LINKEDIN_MAX:
        return "", f":x: The post is {len(post)} characters; LinkedIn allows {LINKEDIN_MAX}. Shorten it and try again."
    return post, ""


def approve(cfg, issue, arg="", scheduled=False):
    """Publish the draft. `scheduled` is the delayed approval run by poster.scheduled. True when the draft is done."""
    n, labels = issue["number"], {l["name"] for l in issue.get("labels", [])}
    retry = SCHEDULE_CANCELLED if scheduled else "Fix it and comment `/approve` again."

    def stop(msg):
        gh.comment(n, msg)
        return False

    if "published" in labels:
        return stop("Already published, nothing to do.")
    topic = gh.parse_meta(issue.get("body")).get("topic", {})
    written_for = off_date(cfg, topic)
    if written_for and arg.lower() != "anyway" and not scheduled:  # a scheduled post was checked against its date by /schedule
        return stop(f":calendar: This is an \"On this day\" post written for {written_for}, and that is not today. "
                    "Nothing was posted. Edit the opening so it is still true and comment **`/approve anyway`**, or **`/reject`**.")
    post, problem = post_of(cfg, issue)
    if problem:
        return stop(f"{problem}\n\n{retry}" if scheduled else problem)
    src = topic.get("source_url")
    mode = source_link_mode(cfg) if src else "off"
    link_line = f"Source: {src}"
    if "scheduled" in labels and not scheduled:
        clear_schedule(issue)  # approved early: nothing left for the scheduled run
    lead = ":alarm_clock: Scheduled time reached. " if scheduled else ""
    img = gh.parse_image(issue.get("body"))
    has_image = img.get("kind") in ("card", "ai") and img.get("path")

    if cfg["publish_mode"] != "linkedin":
        gh.comment(n, lead + "**Simulated** (publish_mode is `simulated`), nothing was posted. This is what would go out:\n\n"
                   + "\n".join("> " + l for l in post.splitlines())
                   + (f"\n\nWith this image:\n\n<img src=\"{gh.file_url(img['path'], img['branch'])}\" width=\"300\" alt=\"post image\">" if has_image else "")
                   + (f"\n\nFirst comment ({'by the bot' if mode == 'auto' else 'you add it'}): {link_line}" if mode != "off" else "")
                   + "\n\nSet the repo variable `PUBLISH_MODE` to `linkedin` to post for real.")
        gh.add_labels(n, ["simulated"])
        gh.close(n)
        return True
    data = None
    if has_image:
        try:
            data = gh.raw_file(img["path"], img["branch"])
        except (HttpError, OSError) as e:
            return stop(f":x: Not published: I could not read the image ({str(e)[:150]}). Comment **`/image`** for a new one "
                        f"or **`/image none`** to post without an image.\n\n{retry}")
    try:
        urn, url, comment_error = linkedin.publish(post, link_line if mode == "auto" else None, image=data, alt=img.get("alt", ""))
    except linkedin.LinkedInError as e:
        return stop(f":x: Not published: {e}\n\n{retry}")
    msg = f"{lead}:rocket: Published: {url or urn}"
    if mode == "manual" or (mode == "auto" and comment_error):
        why = f"The bot could not add it ({comment_error}). " if comment_error else ""
        msg += (f"\n\n:link: **Add the source as the first comment** on [your post]({url}). {why}Paste this:\n\n"
                f"```\n{link_line}\n```")
    gh.comment(n, msg)
    gh.add_labels(n, ["published"])
    gh.close(n)
    return True


# --------------------------------------------------------------------------- /schedule: a delayed /approve
def parse_when(cfg, arg):
    """'dd/mm/yyyy HH:MM [anyway]' in the configured timezone -> (aware datetime, anyway). ValueError when it is not that."""
    m = WHEN_RE.match(arg.strip())
    if not m:
        raise ValueError("format")
    d, mo, y, h, mi = (int(g) for g in m.groups()[:5])
    return datetime(y, mo, d, h, mi, tzinfo=tz(cfg)), bool(m.group(6))  # raises ValueError for 31/02 or 25:00


def when_text(cfg, when):
    local = when.astimezone(tz(cfg))
    return f"{local.strftime('%A')} {local.day} {local.strftime('%B %Y, %H:%M')} ({cfg.get('timezone') or 'UTC'})"


def clear_schedule(issue):
    gh.remove_label(issue["number"], "scheduled")  # first: this is what the scheduled run looks for
    if gh.publish_at(issue.get("body")):
        gh.update_issue(issue["number"], body=gh.with_publish_at(issue["body"], None))


def schedule(cfg, issue, arg):
    n, labels = issue["number"], {l["name"] for l in issue.get("labels", [])}
    zone = cfg.get("timezone") or "UTC"
    if "published" in labels:
        return gh.comment(n, "Already published, nothing to do.")
    try:
        when, anyway = parse_when(cfg, arg)
    except ValueError:
        return gh.comment(n, f"Tell me when: **`/schedule dd/mm/yyyy HH:MM`**, 24-hour clock, {zone} time. For example `/schedule 25/12/2026 09:30`.")
    current = datetime.now(tz(cfg))
    if when <= current:
        return gh.comment(n, f"{when_text(cfg, when)} has already passed (it is now {current.strftime('%d/%m/%Y %H:%M')} {zone}). "
                          "Pick a later time, or comment **`/approve`** to publish now.")
    post, problem = post_of(cfg, issue)
    if problem:
        return gh.comment(n, problem)
    written_for = off_date(cfg, gh.parse_meta(issue.get("body")).get("topic", {}), on=when.date())
    if written_for and not anyway:
        return gh.comment(n, f":calendar: This is an \"On this day\" post written for {written_for}, and you asked for another day. Nothing was scheduled. "
                          f"Schedule it for that day, or edit the opening so it is still true and comment **`/schedule {arg.strip()} anyway`**.")
    gh.update_issue(n, body=gh.with_publish_at(issue.get("body"), when))
    gh.add_labels(n, ["scheduled"])
    msg = (f":alarm_clock: Scheduled for **{when_text(cfg, when)}**. It is published by the first check after that time (one runs every 15 minutes), "
           "exactly as the text between the markers reads then, so you can still edit it.\n\n"
           "**`/unschedule`** cancels, **`/schedule`** with a new time moves it, **`/approve`** publishes now.")
    if zone == "UTC":
        msg += "\n\nThe time was read as UTC. To schedule in your own time, set the repo variable `TIMEZONE` (for example `Asia/Jerusalem`) and `/schedule` again."
    try:
        gh.set_workflow(gh.SCHEDULED_WORKFLOW, True)
    except HttpError as e:
        msg += (f"\n\n:warning: I could not switch on the *Publish scheduled posts* workflow ({e.status}). "
                "Enable it in the Actions tab, or this post will not go out.")
    gh.comment(n, msg)


def unschedule(issue):
    n = issue["number"]
    if "scheduled" not in {l["name"] for l in issue.get("labels", [])} and not gh.publish_at(issue.get("body")):
        return gh.comment(n, "This draft is not scheduled.")
    clear_schedule(issue)
    gh.comment(n, "Schedule cancelled. The draft is still open: **`/approve`** publishes it now, **`/schedule dd/mm/yyyy HH:MM`** sets a new time.")


def source_link_mode(cfg):
    post = cfg.get("post") or {}
    mode = str(post.get("source_link") or "").strip().lower()
    if mode in ("manual", "auto", "off"):
        return mode
    if "source_link_as_comment" in post:  # older config key
        return "auto" if post["source_link_as_comment"] else "off"
    return "manual"


def reject(cfg, issue):
    days = int((cfg.get("review") or {}).get("reject_cooldown_days", 7))
    gh.add_labels(issue["number"], ["rejected"])
    again = f"after {days} days" if days else "on a later run"
    gh.comment(issue["number"], f"Rejected. Nothing was posted. This story or article may be drafted again {again}. "
               "To never use it again comment **`/block`**, or **`/block site`** to drop every story from that website.")
    gh.close(issue["number"], "not_planned")


def block(issue, arg):
    n = issue["number"]
    topic = gh.parse_meta(issue.get("body")).get("topic", {})
    labels = ["blocked"]
    if arg == "site":
        if topic.get("kind") != "news" or not topic.get("source_url"):
            return gh.comment(n, "`/block site` only works on news drafts (the other posts all come from Wikipedia). Use **`/block`** to block just this article or event.")
        labels.append("blocked-site")
    elif arg:
        return gh.comment(n, "Use **`/block`** for this story or article, or **`/block site`** for its whole website.")
    gh.add_labels(n, labels)
    what = f"every story from **{sources.host(topic['source_url'])}**" if arg == "site" else "this story/article"
    gh.comment(n, f"Blocked: {what} will never be drafted again. Comment **`/unblock`** to undo.")
    if issue.get("state") == "open":
        gh.close(n, "not_planned")


def save_example(issue, why):
    """/example on a draft: keep its English text as a writing example (yours, so `source: own`)."""
    n = issue["number"]
    _, en = gh.parse_parts(issue.get("body"))
    if not en:
        return gh.comment(n, ":x: No English text found in this draft to save.")
    labels = {l["name"] for l in issue.get("labels", [])}
    path = examples.save(en, why=why or ("my published post" if "published" in labels else issue.get("title", "")),
                         source="own", issue_number=n)
    gh.comment(n, f"Saved as a writing example: `{path}`.")


def unblock(issue):
    for label in ("blocked", "blocked-site"):
        gh.remove_label(issue["number"], label)
    gh.comment(issue["number"], "Unblocked: this source can be drafted again.")


def rewrite(cfg, issue, instruction):
    n = issue["number"]
    if not instruction:
        return gh.comment(n, "Tell me what to change, e.g. `/rewrite shorter, and end with a question about testing`.")
    meta, (_, current) = gh.parse_meta(issue.get("body")), gh.parse_parts(issue.get("body"))
    topic = meta.get("topic")
    if not topic or not current:
        return gh.comment(n, ":x: This issue is missing its English draft or hidden metadata, so I can't rewrite it.")
    try:
        d = compose.write(cfg, author_for(cfg, load_profile(), topic["kind"]), topic, meta.get("news", []),
                          instruction=instruction, current=current)
    except RuntimeError as e:
        return gh.comment(n, f":x: Rewrite failed: {e}. The draft is unchanged.")
    body = gh.replace_parts(issue["body"], d.post_text_he, d.post_text)
    warnings = list(d.warnings)
    if gh.parse_image(body).get("kind") == "card":  # the card shows the headline and hook, so it is redrawn
        try:
            pic = images.make(cfg, "card", topic, d.post_text_he, d.post_text, load_profile())
            body = gh.replace_image(body, images.store(cfg, pic, title_of(issue)))
        except Exception as e:  # noqa: BLE001 - the rewrite itself succeeded
            warnings.append(f"The card was not redrawn ({type(e).__name__}); comment `/image card` to try again.")
    gh.update_issue(n, body=body)
    notes = "".join(f"\n- {w}" for w in warnings)
    translated = " and translated" if d.post_text_he else ""
    gh.comment(n, f"Rewritten{translated} ({d.words} words, `{d.model}`). The issue text above now holds the new version."
               + (f"\n\n**Review notes:**{notes}" if notes else "") + "\n\n" + HELP)


def title_of(issue):
    return re.sub(r"^Post draft:\s*", "", issue.get("title") or "").strip() or "post"


def image(cfg, issue, arg):
    """/image [card|ai|none]: a new image of the same kind (a card if there was none), or switch kind."""
    n, labels = issue["number"], {l["name"] for l in issue.get("labels", [])}
    arg = arg.lower().strip()
    if "published" in labels:
        return gh.comment(n, "Already published, the image can't change now.")
    if arg not in ("", "card", "ai", "none"):
        return gh.comment(n, "Use **`/image`** for a new image, **`/image card`**, **`/image ai`** or **`/image none`**.")
    body = issue.get("body") or ""
    current = gh.parse_image(body).get("kind")
    if arg == "none":
        info, notes = {"kind": "none"}, []
    else:
        want = arg or (current if current in ("card", "ai") else "card")
        topic = gh.parse_meta(body).get("topic") or {"kind": "news", "title": title_of(issue)}
        he, en = gh.parse_parts(body)
        if not en:
            return gh.comment(n, ":x: No English text found in this draft, so I can't make an image for it.")
        try:
            pic = images.make(cfg, want, topic, he, en, load_profile())
            info, notes = images.store(cfg, pic, title_of(issue)), pic.notes
        except Exception as e:  # noqa: BLE001
            return gh.comment(n, f":x: No new image: {type(e).__name__}: {str(e)[:200]}. The draft is unchanged.")
    try:
        new_body = gh.replace_image(body, info)
    except ValueError:
        return gh.comment(n, ":x: I can't find the post markers in this issue, so I can't attach an image.")
    gh.update_issue(n, body=new_body)
    said = {"none": "Image removed: the post goes out as text only.", "card": "New card above.", "ai": "New AI illustration above."}[info["kind"]]
    gh.comment(n, said + "".join(f"\n- {x}" for x in notes))


def main():
    cfg = load_config()
    number = int(os.environ["ISSUE_NUMBER"])
    cmd, arg = parse_command(os.environ.get("COMMENT_BODY", ""))
    if not cmd:
        print("not a command, ignoring")
        return
    issue = gh.call("GET", f"/issues/{number}")
    if "draft" not in {l["name"] for l in issue.get("labels", [])}:
        print("not a draft issue, ignoring")
        return
    if issue.get("state") != "open" and cmd not in ("/block", "/unblock", "/example"):
        print("closed draft: only /block, /unblock and /example apply, ignoring")
        return
    if cmd == "/approve":
        approve(cfg, issue, arg)
    elif cmd == "/schedule":
        schedule(cfg, issue, arg)
    elif cmd == "/unschedule":
        unschedule(issue)
    elif cmd == "/reject":
        reject(cfg, issue)
    elif cmd == "/block":
        block(issue, arg.lower())
    elif cmd == "/unblock":
        unblock(issue)
    elif cmd == "/example":
        save_example(issue, arg)
    elif cmd == "/rewrite":
        rewrite(cfg, issue, arg)
    elif cmd == "/image":
        image(cfg, issue, arg)
    else:
        gh.comment(number, f"Unknown command `{cmd}`. {HELP}")


if __name__ == "__main__":
    sys.exit(main())
