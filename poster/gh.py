"""GitHub issues as the review board and the system of record (no database, no commits from the bot)."""
import json
import os
import re
import subprocess
import urllib.parse

from .http import HttpError, request

API = "https://api.github.com"
POST_START, POST_END = "<!-- post:start -->", "<!-- post:end -->"
META_RE = re.compile(r"<!-- meta: (\{.*?\}) -->", re.S)
LABELS = {
    "draft": ("0e8a16", "A post draft waiting for /approve, /reject or /rewrite"),
    "topic": ("1d76db", "A post idea you queued; drafted before automatic topics"),
    "published": ("5319e7", "Posted to LinkedIn"),
    "simulated": ("c5def5", "Approved in simulated mode, nothing was posted"),
    "rejected": ("b60205", "Rejected, never posted"),
    "token-expiry": ("fbca04", "LinkedIn token needs renewing"),
}


def repo():
    r = os.environ.get("GITHUB_REPOSITORY")
    if r:
        return r
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(\.git)?$", url)
    if not m:
        raise SystemExit("set GITHUB_REPOSITORY=owner/name")
    return m.group(1)


def _token():
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not t:
        t = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    if not t:
        raise SystemExit("no GitHub token: set GITHUB_TOKEN or log in with gh")
    return t


def call(method, path, body=None):
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    return request(method, f"{API}/repos/{repo()}{path}", headers=headers, body=body)[2]


def ensure_labels():
    existing = {l["name"] for l in call("GET", "/labels?per_page=100")}
    for name, (color, desc) in LABELS.items():
        if name not in existing:
            try:
                call("POST", "/labels", {"name": name, "color": color, "description": desc})
            except HttpError as e:
                if e.status != 422:  # 422 = already exists (race)
                    raise


def issues(label, state="all"):
    out, page = [], 1
    while True:
        batch = call("GET", f"/issues?labels={urllib.parse.quote(label)}&state={state}&per_page=100&page={page}&sort=created&direction=desc")
        out += [i for i in batch if "pull_request" not in i]
        if len(batch) < 100:
            return out
        page += 1


def create_issue(title, body, labels):
    return call("POST", "/issues", {"title": title[:250], "body": body, "labels": labels})


def update_issue(number, **fields):
    return call("PATCH", f"/issues/{number}", fields)


def comment(number, body):
    return call("POST", f"/issues/{number}/comments", {"body": body})


def add_labels(number, labels):
    return call("POST", f"/issues/{number}/labels", {"labels": labels})


def remove_label(number, label):
    try:
        call("DELETE", f"/issues/{number}/labels/{urllib.parse.quote(label)}")
    except HttpError as e:
        if e.status != 404:
            raise


def close(number, reason="completed"):
    return update_issue(number, state="closed", state_reason=reason)


# --------------------------------------------------------------------------- issue body <-> data
def parse_meta(body):
    m = META_RE.search(body or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def parse_post(body):
    body = body or ""
    if POST_START not in body or POST_END not in body:
        return ""
    return body.split(POST_START, 1)[1].split(POST_END, 1)[0].strip().replace("\r\n", "\n")


def render_draft(topic, news_items, draft, extra_warnings=()):
    kind_label = {"news": "AI news", "fact": "Coding fact", "queued": "Your topic"}[topic["kind"]]
    src = topic.get("source_url")
    head = [f"**{kind_label}**", f"model `{draft.model}` (attempt {draft.attempt})", f"{draft.words} words", f"{len(draft.post_text)} chars"]
    lines = [" · ".join(head), ""]
    if src:
        lines += [f"**Source:** [{topic['title']}]({src})" + (f" · [HN discussion]({topic['discussion_url']})" if topic.get("discussion_url") else ""), ""]
    lines += ["The post (edit the text between the markers to change it by hand):", "", POST_START, draft.post_text, POST_END, ""]
    warnings = list(draft.warnings) + list(extra_warnings)
    if warnings:
        lines += ["> [!WARNING]", "> **Review notes**"] + [f"> - {w}" for w in warnings] + [""]
    if draft.facts_used:
        lines += ["<details><summary>Facts used</summary>", ""] + [f"- {f}" for f in draft.facts_used] + ["", "</details>", ""]
    if src:
        lines += [f"**First comment** (posted under the post): Source: {src}", ""]
    lines += ["---", "Reply with **`/approve`** to publish, **`/reject`** to drop it, or **`/rewrite <what to change>`** for an AI rewrite.", ""]
    meta = {"kind": topic["kind"], "topic": topic, "news": news_items}
    lines.append(f"<!-- meta: {json.dumps(meta, ensure_ascii=False).replace('-->', '--&gt;')} -->")
    return "\n".join(lines)


def replace_post(body, new_post):
    a, rest = body.split(POST_START, 1)
    _, b = rest.split(POST_END, 1)
    return f"{a}{POST_START}\n{new_post}\n{POST_END}{b}"
