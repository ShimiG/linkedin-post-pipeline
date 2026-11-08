"""GitHub issues as the review board and the system of record (no database, no commits from the bot)."""
import base64
import json
import os
import re
import subprocess
import urllib.parse

from .http import HttpError, request

API = "https://api.github.com"
POST_START, POST_END = "<!-- post:start -->", "<!-- post:end -->"          # single-language drafts (older issues)
HE_START, HE_END = "<!-- post-he:start -->", "<!-- post-he:end -->"       # translation, published first
EN_START, EN_END = "<!-- post-en:start -->", "<!-- post-en:end -->"
SECTION_START, SECTION_END = "<!-- post-section:start -->", "<!-- post-section:end -->"  # wraps the blocks + headings
META_RE = re.compile(r"<!-- meta: (\{.*?\}) -->", re.S)
LABELS = {
    "draft": ("0e8a16", "A post draft waiting for /approve, /reject or /rewrite"),
    "topic": ("1d76db", "A post idea you queued; drafted before automatic topics"),
    "published": ("5319e7", "Posted to LinkedIn"),
    "simulated": ("c5def5", "Approved in simulated mode, nothing was posted"),
    "rejected": ("b60205", "Rejected, never posted; the source may be drafted again after the cooldown"),
    "blocked": ("000000", "This story or Wikipedia article is never drafted again (/unblock to undo)"),
    "blocked-site": ("3f3f3f", "Stories from this website are never drafted again (/unblock to undo)"),
    "token-expiry": ("fbca04", "LinkedIn token needs renewing"),
    "example": ("d4c5f9", "A post you like: saved to examples/ as a writing example"),
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


def put_file(path, text, message, branch=None):
    """Create a file on the default branch through the Contents API (no checkout or git push needed)."""
    body = {"message": message, "content": base64.b64encode(text.encode()).decode()}
    if branch:
        body["branch"] = branch
    return call("PUT", f"/contents/{urllib.parse.quote(path)}", body)


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


def _between(body, start, end):
    if start not in body or end not in body:
        return None
    return body.split(start, 1)[1].split(end, 1)[0].strip().replace("\r\n", "\n")


def parse_parts(body):
    """(translation, english) from a draft issue. Older single-block issues return ("", text)."""
    body = body or ""
    en = _between(body, EN_START, EN_END)
    if en is not None:
        return _between(body, HE_START, HE_END) or "", en
    return "", _between(body, POST_START, POST_END) or ""


def post_section(he, en):
    """The editable post area of an issue: Hebrew block (if any) then English block, wrapped so it can be swapped whole."""
    if he:
        inner = (f"_Part 1, exactly as posted (headline, \"English below\" line, Hebrew text):_\n\n{HE_START}\n{he}\n{HE_END}\n\n"
                 f"_Part 2, posted after a separator line:_\n\n{EN_START}\n{en}\n{EN_END}")
    else:
        inner = f"{EN_START}\n{en}\n{EN_END}"
    return f"{SECTION_START}\n{inner}\n{SECTION_END}"


def render_draft(topic, news_items, draft, extra_warnings=()):
    kind_label = {"news": "AI news", "fact": "Coding fact", "queued": "Your topic"}[topic["kind"]]
    src = topic.get("source_url")
    head = [f"**{kind_label}**", f"model `{draft.model}` (attempt {draft.attempt})", f"{draft.words} words (English)"]
    if draft.post_text_he:
        head.append(f"translated by `{draft.translation_model}`")
    lines = [" · ".join(head), ""]
    if src:
        lines += [f"**Source:** [{topic['title']}]({src})" + (f" · [HN discussion]({topic['discussion_url']})" if topic.get("discussion_url") else ""), ""]
    lines += ["The post. The notes in _italics_ are only for you; everything between the markers is posted. Edit it by hand if you like:", "",
              post_section(draft.post_text_he, draft.post_text), ""]
    warnings = list(draft.warnings) + list(extra_warnings)
    if warnings:
        lines += ["> [!WARNING]", "> **Review notes**"] + [f"> - {w}" for w in warnings] + [""]
    if draft.facts_used:
        lines += ["<details><summary>Facts used</summary>", ""] + [f"- {f}" for f in draft.facts_used] + ["", "</details>", ""]
    if src:
        lines += [f"**First comment** (posted under the post): Source: {src}", ""]
    lines += ["---", "Reply with **`/approve`** to publish, **`/reject`** to drop this post, **`/block`** to drop it and never use this source again"
              + (" (**`/block site`** for the whole website)" if topic["kind"] == "news" else "")
              + ", or **`/rewrite <what to change>`** for an AI rewrite"
              + (" (the English is rewritten, then translated again)." if draft.post_text_he else "."), ""]
    meta = {"kind": topic["kind"], "topic": topic, "news": news_items}
    lines.append(f"<!-- meta: {json.dumps(meta, ensure_ascii=False).replace('-->', '--&gt;')} -->")
    return "\n".join(lines)


def replace_parts(body, he, en):
    """Swap in new text, keeping everything else. Converts an older single-block issue to the new layout."""
    for start, end in ((SECTION_START, SECTION_END), (POST_START, POST_END)):
        if start in body and end in body:
            a, rest = body.split(start, 1)
            _, b = rest.split(end, 1)
            return f"{a}{post_section(he, en)}{b}"
    raise ValueError("no post markers in the issue body")
