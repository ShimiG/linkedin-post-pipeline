import json
from datetime import datetime, timezone

from poster.draft import History

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def draft_issue(labels, topic, closed_at=None):
    body = f"text\n<!-- meta: {json.dumps({'kind': topic['kind'], 'topic': topic, 'news': []})} -->"
    return {"labels": [{"name": l} for l in ["draft", *labels]], "body": body, "closed_at": closed_at}


NEWS = lambda i, site="a.com": {"kind": "news", "hn_id": str(i), "source_url": f"https://www.{site}/x{i}"}
FACT = lambda name: {"kind": "fact", "wiki_subject": name, "source_url": f"https://en.wikipedia.org/wiki/{name}"}


def test_open_and_published_are_skipped():
    h = History([draft_issue([], NEWS(1)), draft_issue(["published"], FACT("Git"))], 7, NOW)
    assert h.skip_hn == {"1"} and h.skip_wiki == {"Git"} and not h.blocked_sites


def test_rejected_only_during_cooldown():
    recent = draft_issue(["rejected"], NEWS(1), "2026-11-18T10:00:00Z")
    old = draft_issue(["rejected"], NEWS(2), "2026-11-01T10:00:00Z")
    assert History([recent, old], 7, NOW).skip_hn == {"1"}
    assert History([recent, old], 0, NOW).skip_hn == set()


def test_blocked_story_site_and_article():
    issues = [draft_issue(["rejected", "blocked"], NEWS(1), "2026-01-01T00:00:00Z"),
              draft_issue(["blocked", "blocked-site"], NEWS(2, "spam.io")),
              draft_issue(["blocked"], FACT("COBOL"))]
    h = History(issues, 7, NOW)
    assert h.skip_hn == {"1", "2"} and h.blocked_sites == {"spam.io"}
    assert h.blocked_wiki == {"COBOL"} and "COBOL" in h.skip_wiki


def test_last_kind_is_newest():
    assert History([draft_issue([], FACT("Git")), draft_issue([], NEWS(1))], 7, NOW).last_kind == "fact"
