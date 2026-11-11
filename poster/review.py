"""Runs when the repo owner comments on a `draft` issue: /approve [hebrew|english|both] [anyway],
/schedule [hebrew|english|both] <dd/mm/yyyy HH:MM | next>, /unschedule [version], /reject, /rewrite <instruction>, /image [card|ai|none].

With bilingual.publish: separate, each language is its own LinkedIn post: the draft holds both, and /approve or
/schedule names the one to post. The issue stays open until both are out (or you close it).

Inputs come from the workflow as env vars (never interpolated into shell): ISSUE_NUMBER, COMMENT_BODY.
"""
import os
import re
import sys
from datetime import date, datetime

from . import compose, examples, gh, images, linkedin, sources, timing
from .config import load_config, load_profile, today, tz
from .http import HttpError
from .draft import author_for

LINKEDIN_MAX = 3000
WHEN_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})[ ,]+(\d{1,2}):(\d{2})(?:\s+(anyway))?$", re.I)
SCHEDULE_CANCELLED = "The schedule is cancelled. Fix it, then comment `/approve` to publish now or `/schedule` again."
HELP = ("Commands: **`/approve hebrew`** or **`/approve english`** publishes that version now (**`/approve both`**: one post, Hebrew first), "
        "**`/schedule hebrew next`** publishes it at its next good time slot, **`/schedule english dd/mm/yyyy HH:MM`** at a time you pick "
        "(**`/unschedule`** cancels), **`/reject`** drops this post, **`/block`** (or **`/block site`**) drops it and never uses that source again, "
        "**`/rewrite <what to change>`** asks the AI for a new version, **`/image`** makes a new image (**`/image card`**, **`/image ai`**, "
        "**`/image none`**), **`/example`** saves it as a writing example.")
REPLY_TIP = ("\n\n:speech_balloon: **Stay for the first hour:** reply to comments as they come in. Early replies are what LinkedIn "
             "weighs most when it decides who else sees the post.")
WORD = {"he": "hebrew", "en": "english", "both": "both"}


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


def parse_args(arg):
    """'english 25/12/2026 09:30 anyway' -> ("en", True, "25/12/2026 09:30"). The version and "anyway" may come anywhere."""
    lang, anyway, rest = None, False, []
    for t in (arg or "").split():
        low = t.lower()
        if lang is None and low in compose.LANGS:
            lang = compose.LANGS[low]
        elif low == "anyway":
            anyway = True
        else:
            rest.append(t)
    return lang, anyway, " ".join(rest)


def versions(issue):
    """The versions this draft has: {"he", "en"}, or {"en"} when there is no translation."""
    he, en = gh.parse_parts(issue.get("body"))
    return {"he", "en"} if he else ({"en"} if en else set())


def done(issue):
    """The versions already posted. A draft published before versions existed counts as both."""
    labels = {l["name"] for l in issue.get("labels", [])}
    out = {v for v in ("he", "en") if f"published-{v}" in labels}
    return out or ({"he", "en"} if "published" in labels else set())


def remaining(issue):
    return versions(issue) - done(issue)


def resolve_version(cfg, issue, lang, cmd="/approve"):
    """(version, '') or (None, what to tell the user). Asks which version when both exist and none was named."""
    have = versions(issue)
    if lang in ("he", "en") and lang not in have:
        return None, f"This draft has no {compose.LANG_NAME[lang]} version. Use **`{cmd} english`**."
    if lang:
        return (lang if have != {"en"} or lang != "both" else "en"), ""
    if have == {"en"}:
        return "en", ""
    if not compose.separate(cfg):
        return "both", ""
    return None, (f"Which version? **`{cmd} hebrew`** or **`{cmd} english`**. Each goes out as its own post, so you can post them "
                  f"on different days (**`{cmd} both`** puts them together in one post, Hebrew first).")


def command_word(cfg, issue, lang):
    """The version as written after a command (" hebrew"), or "" when the draft has only one way to be posted."""
    return "" if versions(issue) == {"en"} or (lang == "both" and not compose.separate(cfg)) else f" {WORD[lang]}"


def already(issue, lang):
    """True when `lang` (or, for "both", either version) has been posted."""
    d = done(issue)
    return bool(d) if lang == "both" else lang in d


def post_of(cfg, issue, lang="both"):
    """(text to publish, '') or ('', what is wrong with it)."""
    post = compose.version(cfg, *gh.parse_parts(issue.get("body")), lang)
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

    wanted, anyway, _ = parse_args(arg)
    lang, problem = resolve_version(cfg, issue, wanted)
    if not lang:
        return stop(problem)
    word = command_word(cfg, issue, lang)
    if already(issue, lang):
        what = "" if lang == "both" or versions(issue) == {"en"} else f" The {compose.LANG_NAME[lang]} version is out."
        return stop(f"Already published, nothing to do.{what}")
    topic = gh.parse_meta(issue.get("body")).get("topic", {})
    written_for = off_date(cfg, topic)
    if written_for and not anyway and not scheduled:  # a scheduled post was checked against its date by /schedule
        return stop(f":calendar: This is an \"On this day\" post written for {written_for}, and that is not today. "
                    f"Nothing was posted. Edit the opening so it is still true and comment **`/approve{word} anyway`**, or **`/reject`**.")
    now = datetime.now(tz(cfg))
    if not scheduled and not anyway and timing.quiet(cfg, now):
        slot = timing.next_slot(cfg, lang, now)
        nxt = f"**`/schedule{word} next`** posts it on {when_text(cfg, slot)}" if slot else "**`/schedule`** sets a time"
        return stop(f":zzz: It's the weekend in Israel, when few people read LinkedIn. Nothing was posted. {nxt}, "
                    f"or **`/approve{word} anyway`** posts it now.")
    post, problem = post_of(cfg, issue, lang)
    if problem:
        return stop(f"{problem}\n\n{retry}" if scheduled else problem)
    src = topic.get("source_url")
    mode = source_link_mode(cfg) if src else "off"
    link_line = f"Source: {src}"
    if "scheduled" in labels and not scheduled:
        clear_schedule(issue, None if lang == "both" else lang)  # approved early: nothing left for the scheduled run
    lead = ":alarm_clock: Scheduled time reached. " if scheduled else ""
    img = gh.parse_image(issue.get("body"))
    has_image = img.get("kind") in ("card", "ai") and img.get("path")

    if cfg["publish_mode"] != "linkedin":
        gh.comment(n, lead + "**Simulated** (publish_mode is `simulated`), nothing was posted. This is what would go out"
                   + ("" if word == "" else f" ({compose.LANG_NAME[lang]})") + ":\n\n"
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
    posted = {"he", "en"} if lang == "both" else {lang}
    named = f"the {compose.LANG_NAME[lang]} version" if word else ""
    msg = f"{lead}:rocket: Published{' ' + named if named else ''}: {url or urn}"
    if mode == "manual" or (mode == "auto" and comment_error):
        why = f"The bot could not add it ({comment_error}). " if comment_error else ""
        msg += (f"\n\n:link: **Add the source as the first comment** on [your post]({url}). {why}Paste this:\n\n"
                f"```\n{link_line}\n```")
    msg += REPLY_TIP
    left = sorted(remaining(issue) - posted)
    if left:
        other = left[0]
        slot = timing.next_slot(cfg, other, now, avoid_days={now.date()})
        when = f" on {when_text(cfg, slot)}" if slot else ""
        msg += (f"\n\n**Still to post:** the {compose.LANG_NAME[other]} version. **`/schedule {WORD[other]} next`** posts it{when} "
                f"(another day reaches more people than both on the same day), **`/approve {WORD[other]}`** posts it now. "
                "If you don't want it, close this issue.")
    gh.comment(n, msg)
    new_labels = ["published"] + ([] if lang == "both" else [f"published-{lang}"])
    gh.add_labels(n, new_labels)
    issue["labels"] = issue.get("labels", []) + [{"name": l} for l in new_labels]
    if left:  # stays open for the other version; /schedule next keeps that one off this day
        issue["body"] = gh.with_published(issue.get("body") or "", lang, now)
        gh.update_issue(n, body=issue["body"])
    else:
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


def clear_schedule(issue, lang=None):
    """Remove the schedule of `lang` (every schedule when None). The label goes when nothing is left scheduled."""
    body = issue.get("body") or ""
    new = gh.with_publish_at(body, None, lang)
    if not gh.schedules(new):
        gh.remove_label(issue["number"], "scheduled")  # first: this is what the scheduled run looks for
    if new != body:
        gh.update_issue(issue["number"], body=new)
        issue["body"] = new
    return new


def schedule(cfg, issue, arg):
    n = issue["number"]
    zone = cfg.get("timezone") or "UTC"
    wanted, anyway, rest = parse_args(arg)
    lang, problem = resolve_version(cfg, issue, wanted, "/schedule")
    if not lang:
        return gh.comment(n, problem)
    word = command_word(cfg, issue, lang)
    if already(issue, lang):
        return gh.comment(n, "Already published, nothing to do.")
    current = datetime.now(tz(cfg))
    if rest.lower() == "next":
        body = issue.get("body") or ""
        others = [w for v, w in {**gh.published_times(body), **gh.schedules(body)}.items() if v not in (lang, "both")]
        when = timing.next_slot(cfg, lang, current, avoid_days={w.astimezone(tz(cfg)).date() for w in others})
        if not when:
            return gh.comment(n, "I found no good time slot in the next two weeks (see `posting` in config.yml). Give a time: "
                                 f"**`/schedule{word} dd/mm/yyyy HH:MM`**.")
    else:
        try:
            when, a2 = parse_when(cfg, rest)
        except ValueError:
            return gh.comment(n, f"Tell me when: **`/schedule{word} dd/mm/yyyy HH:MM`**, 24-hour clock, {zone} time "
                                 f"(for example `/schedule{word} 25/12/2026 09:30`), or **`/schedule{word} next`** for the next good time slot.")
        anyway = anyway or a2
    if when <= current:
        return gh.comment(n, f"{when_text(cfg, when)} has already passed (it is now {current.strftime('%d/%m/%Y %H:%M')} {zone}). "
                          "Pick a later time, or comment **`/approve`** to publish now.")
    post, problem = post_of(cfg, issue, lang)
    if problem:
        return gh.comment(n, problem)
    written_for = off_date(cfg, gh.parse_meta(issue.get("body")).get("topic", {}), on=when.date())
    if written_for and not anyway:
        return gh.comment(n, f":calendar: This is an \"On this day\" post written for {written_for}, and you asked for another day. Nothing was scheduled. "
                          f"Schedule it for that day, or edit the opening so it is still true and comment **`/schedule {arg.strip()} anyway`**.")
    gh.update_issue(n, body=gh.with_publish_at(issue.get("body"), when, lang))
    gh.add_labels(n, ["scheduled"])
    what = f"The {compose.LANG_NAME[lang]} version is scheduled" if word else "Scheduled"
    msg = (f":alarm_clock: {what} for **{when_text(cfg, when)}**. It is published by the first check after that time (one runs every 15 minutes), "
           "exactly as the text between the markers reads then, so you can still edit it.\n\n"
           f"**`/unschedule{word}`** cancels, **`/schedule{word}`** with a new time moves it, **`/approve{word}`** publishes now.")
    if timing.quiet(cfg, when.astimezone(tz(cfg))) and not anyway:
        msg += f"\n\n:zzz: That is during the weekend, when few people read LinkedIn. **`/schedule{word} next`** picks a better time."
    if zone == "UTC":
        msg += "\n\nThe time was read as UTC. To schedule in your own time, set the repo variable `TIMEZONE` (for example `Asia/Jerusalem`) and `/schedule` again."
    try:
        gh.set_workflow(gh.SCHEDULED_WORKFLOW, True)
    except HttpError as e:
        msg += (f"\n\n:warning: I could not switch on the *Publish scheduled posts* workflow ({e.status}). "
                "Enable it in the Actions tab, or this post will not go out.")
    gh.comment(n, msg)


def unschedule(issue, arg=""):
    n = issue["number"]
    lang, _, _ = parse_args(arg)
    if lang == "both":
        lang = None
    has = gh.publish_at(issue.get("body"), lang)
    if not has and (lang or "scheduled" not in {l["name"] for l in issue.get("labels", [])}):
        return gh.comment(n, f"The {compose.LANG_NAME[lang]} version is not scheduled." if lang else "This draft is not scheduled.")
    clear_schedule(issue, lang)
    what = f"The {compose.LANG_NAME[lang]} schedule is cancelled" if lang else "Schedule cancelled"
    gh.comment(n, f"{what}. The draft is still open: **`/approve`** publishes now, **`/schedule`** sets a new time.")


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
            return gh.comment(n, "`/block site` only works on news drafts. Use **`/block`** to block just this article, event or project.")
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
    body = gh.replace_parts(issue["body"], d.post_text_he, d.post_text, compose.separate(cfg))
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
    if "published" in labels and not remaining(issue):
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
        unschedule(issue, arg)
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
