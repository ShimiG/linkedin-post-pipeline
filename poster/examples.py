"""Writing examples: posts you like, saved as files in examples/, shown to the model as style references.

Saving:
  - open an issue labelled `example` (title: why you like it, body: the post text) -> saved, issue closed
  - comment `/example` on one of your draft issues -> its English text is saved

    ISSUE_NUMBER=12 python -m poster.examples   # what the workflow runs for an `example` issue
"""
import os
import random
import re
from datetime import date

from . import gh
from .config import ROOT

DIR = ROOT / "examples"


def slug(text, max_len=40):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "example"


def render(text, *, why, source, issue_number, saved=None):
    why = re.sub(r"\s+", " ", why or "").strip()
    head = ["---", f"saved: {(saved or date.today()).isoformat()}", f"source: {source}", f"issue: {issue_number}"]
    if why:
        head.append(f"why: {why}")
    return "\n".join(head + ["---", "", text.strip(), ""])


def body_of(file_text):
    """Post text without the front matter."""
    t = file_text.replace("\r\n", "\n")
    if t.startswith("---\n") and "\n---\n" in t[4:]:
        t = t[4:].split("\n---\n", 1)[1]
    return t.strip()


def save(text, *, why, source, issue_number):
    path = f"examples/{date.today().isoformat()}-{slug(why or text[:60])}-{issue_number}.md"
    gh.put_file(path, render(text, why=why, source=source, issue_number=issue_number), f"Save writing example from #{issue_number}")
    return path


def save_from_issue(issue):
    n = issue["number"]
    text = (issue.get("body") or "").strip()
    if len(text) < 40:
        return gh.comment(n, ":x: Paste the post text into the issue body (the title is for why you like it), then add the `example` label again.")
    path = save(text, why=issue.get("title", ""), source="saved", issue_number=n)
    gh.comment(n, f"Saved as a writing example: `{path}`. Drafts use saved examples for style only, never their wording or facts.")
    gh.close(n)


def load(cfg, rng=random):
    """Up to examples.per_draft saved examples, each trimmed to examples.max_chars."""
    ex = cfg.get("examples") or {}
    k, cap = int(ex.get("per_draft", 3)), int(ex.get("max_chars", 1500))
    files = sorted(f for f in DIR.glob("*.md") if f.name.lower() != "readme.md") if DIR.exists() else []
    texts = [body_of(f.read_text())[:cap] for f in files]
    texts = [t for t in texts if t]
    return rng.sample(texts, min(k, len(texts))) if k > 0 else []


def main():
    issue = gh.call("GET", f"/issues/{int(os.environ['ISSUE_NUMBER'])}")
    if "example" not in {l["name"] for l in issue.get("labels", [])} or issue.get("state") != "open":
        print("not an open example issue (or already saved), ignoring")  # opened+labeled can fire twice
        return
    save_from_issue(issue)


if __name__ == "__main__":
    main()
