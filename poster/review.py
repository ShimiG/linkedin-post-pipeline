"""Runs when the repo owner comments on a `draft` issue: /approve [anyway], /reject, /rewrite <instruction>.

Inputs come from the workflow as env vars (never interpolated into shell): ISSUE_NUMBER, COMMENT_BODY.
"""
import os
import sys
from datetime import date

from . import compose, examples, gh, linkedin, sources
from .config import load_config, load_profile, today
from .draft import author_for

LINKEDIN_MAX = 3000
HELP = "Commands: **`/approve`** publishes the text between the markers (Hebrew first, then English), **`/reject`** drops this post, **`/block`** (or **`/block site`**) drops it and never uses that source again, **`/rewrite <what to change>`** asks the AI for a new version, **`/example`** saves it as a writing example."


def parse_command(text):
    first = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not first.startswith("/"):
        return None, ""
    cmd, _, arg = first.partition(" ")
    return cmd.lower(), arg.strip()


def off_date(cfg, topic):
    """The date an "On this day" draft was written for, when that is not today's month and day; else ''."""
    if topic.get("kind") != "onthisday" or not topic.get("otd_date"):
        return ""
    try:
        d = date.fromisoformat(topic["otd_date"])
    except ValueError:
        return ""
    t = today(cfg)
    return "" if (d.month, d.day) == (t.month, t.day) else f"{d.day} {d.strftime('%B')}"


def approve(cfg, issue, arg=""):
    n, labels = issue["number"], {l["name"] for l in issue.get("labels", [])}
    if "published" in labels:
        return gh.comment(n, "Already published, nothing to do.")
    written_for = off_date(cfg, gh.parse_meta(issue.get("body")).get("topic", {}))
    if written_for and arg.lower() != "anyway":
        return gh.comment(n, f":calendar: This is an \"On this day\" post written for {written_for}, and that is not today. "
                          "Nothing was posted. Edit the opening so it is still true and comment **`/approve anyway`**, or **`/reject`**.")
    he, en = gh.parse_parts(issue.get("body"))
    post = compose.combine(cfg, he, en)
    if not post:
        return gh.comment(n, ":x: I can't find the post. Keep the `<!-- post-…:start -->` / `<!-- post-…:end -->` markers around the text.")
    if len(post) > LINKEDIN_MAX:
        return gh.comment(n, f":x: The post is {len(post)} characters; LinkedIn allows {LINKEDIN_MAX}. Shorten it and `/approve` again.")
    meta = gh.parse_meta(issue.get("body"))
    src = meta.get("topic", {}).get("source_url")
    mode = source_link_mode(cfg) if src else "off"
    link_line = f"Source: {src}"

    if cfg["publish_mode"] != "linkedin":
        gh.comment(n, "**Simulated** (publish_mode is `simulated`), nothing was posted. This is what would go out:\n\n"
                   + "\n".join("> " + l for l in post.splitlines())
                   + (f"\n\nFirst comment ({'by the bot' if mode == 'auto' else 'you add it'}): {link_line}" if mode != "off" else "")
                   + "\n\nSet the repo variable `PUBLISH_MODE` to `linkedin` to post for real.")
        gh.add_labels(n, ["simulated"])
        return gh.close(n)
    try:
        urn, url, comment_error = linkedin.publish(post, link_line if mode == "auto" else None)
    except linkedin.LinkedInError as e:
        return gh.comment(n, f":x: Not published: {e}\n\nFix it and comment `/approve` again.")
    msg = f":rocket: Published: {url or urn}"
    if mode == "manual" or (mode == "auto" and comment_error):
        why = f"The bot could not add it ({comment_error}). " if comment_error else ""
        msg += (f"\n\n:link: **Add the source as the first comment** on [your post]({url}). {why}Paste this:\n\n"
                f"```\n{link_line}\n```")
    gh.comment(n, msg)
    gh.add_labels(n, ["published"])
    gh.close(n)


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
    gh.update_issue(n, body=gh.replace_parts(issue["body"], d.post_text_he, d.post_text))
    notes = "".join(f"\n- {w}" for w in d.warnings)
    translated = " and translated" if d.post_text_he else ""
    gh.comment(n, f"Rewritten{translated} ({d.words} words, `{d.model}`). The issue text above now holds the new version."
               + (f"\n\n**Review notes:**{notes}" if notes else "") + "\n\n" + HELP)


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
    else:
        gh.comment(number, f"Unknown command `{cmd}`. {HELP}")


if __name__ == "__main__":
    sys.exit(main())
