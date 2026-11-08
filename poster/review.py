"""Runs when the repo owner comments on a `draft` issue: /approve, /reject, /rewrite <instruction>.

Inputs come from the workflow as env vars (never interpolated into shell): ISSUE_NUMBER, COMMENT_BODY.
"""
import os
import sys

from . import compose, gh, linkedin
from .config import load_config, load_profile
from .draft import author_for

LINKEDIN_MAX = 3000
HELP = "Commands: **`/approve`** publishes the text between the markers (Hebrew first, then English), **`/reject`** drops it, **`/rewrite <what to change>`** asks the AI for a new version."


def parse_command(text):
    first = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not first.startswith("/"):
        return None, ""
    cmd, _, arg = first.partition(" ")
    return cmd.lower(), arg.strip()


def approve(cfg, issue):
    n, labels = issue["number"], {l["name"] for l in issue.get("labels", [])}
    if "published" in labels:
        return gh.comment(n, "Already published, nothing to do.")
    he, en = gh.parse_parts(issue.get("body"))
    post = compose.combine(cfg, he, en)
    if not post:
        return gh.comment(n, ":x: I can't find the post. Keep the `<!-- post-…:start -->` / `<!-- post-…:end -->` markers around the text.")
    if len(post) > LINKEDIN_MAX:
        return gh.comment(n, f":x: The post is {len(post)} characters; LinkedIn allows {LINKEDIN_MAX}. Shorten it and `/approve` again.")
    meta = gh.parse_meta(issue.get("body"))
    src = meta.get("topic", {}).get("source_url")
    first_comment = f"Source: {src}" if src and cfg["post"].get("source_link_as_comment", True) else None

    if cfg["publish_mode"] != "linkedin":
        gh.comment(n, "**Simulated** (publish_mode is `simulated`), nothing was posted. This is what would go out:\n\n"
                   + "\n".join("> " + l for l in post.splitlines()) + (f"\n\nFirst comment: {first_comment}" if first_comment else "")
                   + "\n\nSet the repo variable `PUBLISH_MODE` to `linkedin` to post for real.")
        gh.add_labels(n, ["simulated"])
        return gh.close(n)
    try:
        urn, url, comment_error = linkedin.publish(post, first_comment)
    except linkedin.LinkedInError as e:
        return gh.comment(n, f":x: Not published: {e}\n\nFix it and comment `/approve` again.")
    msg = f":rocket: Published: {url or urn}"
    if comment_error:
        msg += f"\n\n:warning: The source comment could not be added ({comment_error}). Add it by hand if you want it: {src}"
    gh.comment(n, msg)
    gh.add_labels(n, ["published"])
    gh.close(n)


def reject(issue):
    gh.add_labels(issue["number"], ["rejected"])
    gh.comment(issue["number"], "Rejected. Nothing was posted, and this source will not be picked again.")
    gh.close(issue["number"], "not_planned")


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
    if "draft" not in {l["name"] for l in issue.get("labels", [])} or issue.get("state") != "open":
        print("not an open draft issue, ignoring")
        return
    if cmd == "/approve":
        approve(cfg, issue)
    elif cmd == "/reject":
        reject(issue)
    elif cmd == "/rewrite":
        rewrite(cfg, issue, arg)
    else:
        gh.comment(number, f"Unknown command `{cmd}`. {HELP}")


if __name__ == "__main__":
    sys.exit(main())
