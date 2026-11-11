import random
from datetime import datetime, timedelta, timezone

import pytest

from poster import draft, gh, images, sources
from poster.compose import Draft
from poster.http import HttpError

README = """# My Tool ![badge](https://img.shields.io/x.svg)

A small tool that does one thing
well, across two lines.

<!-- hidden note -->

## Features

- Reads [the docs](https://example.com/docs) for you
- Uses `sqlite` for **storage**

| Path | What |
| ---- | ---- |
| `core` | the engine |

## Installation

```bash
npm install
```

Run `npm start`.

## License

MIT
"""


def test_readme_text_keeps_what_it_is_and_drops_setup():
    t = sources.readme_text(README)
    assert "A small tool that does one thing well, across two lines." in t
    assert "Features:" in t and "- Reads the docs for you" in t and "Uses sqlite for storage" in t
    assert "core - the engine" in t
    for gone in ("badge", "https://", "npm install", "npm start", "MIT", "hidden note", "Installation", "```"):
        assert gone not in t


def test_readme_title_and_chunks():
    assert sources.readme_title(README, "fallback") == "My Tool"
    assert sources.readme_title("no heading", "fallback") == "fallback"
    parts = sources.chunks("\n\n".join(["word " * 100] * 10), size=600, limit=3)
    assert len(parts) == 3 and all(len(p) <= 600 for p in parts)


def fake_github(repos):
    """repos: name -> (private, readme) ; anything else 404s."""
    def get(path, raw=False):
        full = path.split("/repos/", 1)[1]
        name = full.split("/")[1]
        if name not in repos:
            raise HttpError(404, "Not Found", path)
        private, md = repos[name]
        if path.endswith("/languages"):
            return {"Python": 10, "JavaScript": 5}
        if path.endswith("/readme"):
            return md
        return {"name": name, "full_name": f"me/{name}", "private": private, "description": f"{name} description",
                "html_url": f"https://github.com/me/{name}", "pushed_at": "2026-11-01T10:00:00Z"}
    return get


def test_project_topic_public_and_private(monkeypatch):
    monkeypatch.setattr(sources, "_github", fake_github({"pub": (False, README), "priv": (True, README)}))
    topic, items = sources.project_topic("me/pub")
    assert topic["kind"] == "project" and topic["category"] == "project" and topic["repo"] == "me/pub"
    assert topic["title"] == "My project: My Tool" and topic["source_url"] == "https://github.com/me/pub"
    assert items[0]["source"] == "my GitHub repository" and "Python, JavaScript" in items[0]["snippet"]
    assert all(len(i["snippet"]) <= 1500 for i in items) and len(items) >= 2
    assert sources.project_topic("me/priv")[0]["source_url"] is None


def test_project_without_readme_or_description_is_refused(monkeypatch):
    monkeypatch.setattr(sources, "_github", lambda path, raw=False: (_ for _ in ()).throw(HttpError(404, "", path))
                        if path.endswith("/readme") else ({} if path.endswith("/languages") else {"name": "x", "description": ""}))
    with pytest.raises(sources.ProjectError):
        sources.project_topic("me/x")


CFG = {"projects": {"repos": ["a", "b", "c"], "cooldown_days": 60}}


def test_manual_choice_any_repo(monkeypatch):
    monkeypatch.setattr(sources, "_github", fake_github({"other": (False, README)}))
    assert sources.pick_project(CFG, "me", "other")[0]["repo"] == "me/other"
    assert sources.pick_project(CFG, "me", "me/other/")[0]["repo"] == "me/other"


def test_manual_choice_unreadable_says_what_to_do(monkeypatch):
    monkeypatch.setattr(sources, "_github", fake_github({}))
    with pytest.raises(sources.ProjectError, match="PROJECTS_TOKEN"):
        sources.pick_project(CFG, "me", "secret-repo")


def test_random_choice_skips_blocked_and_prefers_fresh(monkeypatch):
    monkeypatch.setattr(sources, "_github", fake_github({n: (False, README) for n in "abc"}))
    for seed in range(10):
        got = sources.pick_project(CFG, "me", "", recent={"me/b"}, blocked={"me/a"}, rng=random.Random(seed))[0]["repo"]
        assert got == "me/c"
    # every candidate drafted recently: still picks one, never the blocked one
    got = sources.pick_project(CFG, "me", "", recent={"me/b", "me/c"}, blocked={"me/a"}, rng=random.Random(1))[0]["repo"]
    assert got in ("me/b", "me/c")


def test_random_choice_with_nothing_left(monkeypatch):
    with pytest.raises(sources.ProjectError, match="projects.repos"):
        sources.pick_project({"projects": {"repos": ["a"]}}, "me", "", blocked={"me/a"})


def test_random_choice_reports_unreadable_repos(monkeypatch):
    monkeypatch.setattr(sources, "_github", fake_github({}))
    with pytest.raises(sources.ProjectError, match="none of the listed repos"):
        sources.pick_project(CFG, "me", "")


# --------------------------------------------------------------------------- never automatic
def test_project_is_never_picked_by_the_rotation(monkeypatch):
    monkeypatch.setattr(sources, "pick_project", lambda *a, **k: pytest.fail("project picked automatically"))
    assert draft.pick({"rotation": ["news", "project"]}, None, "project") is None
    assert "project" not in draft.KINDS and "project" in draft.MANUAL_KINDS
    assert draft.kind_order({"rotation": ["news", "fact"]}, "news", "auto") == ["fact", "news"]


def issue(repo, labels=("draft",), days_ago=0):
    topic = {"kind": "project", "title": "My project: X", "category": "project", "angle": "", "repo": repo}
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"body": gh.render_draft(topic, [], Draft("Text.", model="m")), "labels": [{"name": l} for l in labels],
            "created_at": created, "closed_at": created}


def test_history_tracks_project_repos():
    now = datetime.now(timezone.utc)
    h = draft.History([issue("me/a", days_ago=5), issue("me/b", days_ago=90), issue("me/c", ("draft", "blocked"), 200)], 7, now)
    assert h.blocked_repos == {"me/c"}
    assert draft.recent_repos({"projects": {"cooldown_days": 60}}, h, now) == {"me/a"}
    assert h.last_kind is None  # project drafts do not move the automatic rotation


def test_project_posts_see_the_whole_profile_and_get_their_own_label():
    profile = {"name": "N", "headline": "H", "side_project_x": "X"}
    assert draft.author_for({"general_post_profile_keys": ["name"]}, profile, "project") == profile
    assert images.LABELS["project"][1] == "My project"
    assert "**Your project**" in issue("me/a")["body"]
