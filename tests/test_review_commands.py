import pytest

from poster import compose, gh, linkedin, review
from poster.compose import Draft

CFG = {"publish_mode": "simulated", "post": {"source_link_as_comment": True}, "general_post_profile_keys": ["name"],
       "bilingual": {"enabled": True, "separator": "• • •"}}


@pytest.fixture
def board(monkeypatch):
    log = []
    for name in ("comment", "add_labels", "close", "update_issue"):
        monkeypatch.setattr(gh, name, lambda *a, _n=name, **k: log.append((_n, a, k)))
    return log


def issue(labels=("draft",)):
    topic = {"kind": "news", "title": "T", "category": "ai_news", "angle": "", "source_url": "https://ex.com/a"}
    body = gh.render_draft(topic, [{"title": "T", "snippet": "s", "url": "https://ex.com/a"}],
                           Draft("Post body.", model="m", post_text_he="גוף הפוסט.", translation_model="m"))
    return {"number": 7, "body": body, "labels": [{"name": l} for l in labels], "state": "open"}


def test_approve_simulated_posts_nothing(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda *a: pytest.fail("must not publish in simulated mode"))
    review.approve(CFG, issue())
    names = [e[0] for e in board]
    assert names == ["comment", "add_labels", "close"]
    assert "Simulated" in board[0][1][1] and "Source: https://ex.com/a" in board[0][1][1]
    assert board[1][1][1] == ["simulated"]


def test_approve_live_publishes_with_source_comment(board, monkeypatch):
    sent = {}
    monkeypatch.setattr(linkedin, "publish", lambda text, fc: sent.update(text=text, fc=fc) or ("urn:li:share:1", "https://li/1", None))
    review.approve({**CFG, "publish_mode": "linkedin"}, issue())
    assert sent == {"text": "גוף הפוסט.\n\n• • •\n\nPost body.", "fc": "Source: https://ex.com/a"}
    assert ("add_labels", (7, ["published"]), {}) in board


def test_approve_refuses_double_publish(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda *a: pytest.fail("published twice"))
    review.approve({**CFG, "publish_mode": "linkedin"}, issue(("draft", "published")))
    assert board[0][1][1].startswith("Already published")


def test_approve_linkedin_error_keeps_issue_open(board, monkeypatch):
    def boom(*a):
        raise linkedin.LinkedInError("token expired")
    monkeypatch.setattr(linkedin, "publish", boom)
    review.approve({**CFG, "publish_mode": "linkedin"}, issue())
    assert [e[0] for e in board] == ["comment"] and "token expired" in board[0][1][1]


def test_reject(board):
    review.reject(issue())
    assert ("close", (7, "not_planned"), {}) in board


def test_rewrite_updates_only_the_post(board, monkeypatch):
    monkeypatch.setattr(review, "load_profile", lambda: {"name": "A"})
    seen = {}
    monkeypatch.setattr(compose, "write", lambda *a, **k: seen.update(k) or Draft("New version.", model="m", warnings=["note"], post_text_he="גרסה חדשה."))
    i = issue()
    review.rewrite(CFG, i, "shorter")
    upd = [e for e in board if e[0] == "update_issue"][0]
    new_body = upd[2]["body"]
    assert gh.parse_parts(new_body) == ("גרסה חדשה.", "New version.") and gh.parse_meta(new_body) == gh.parse_meta(i["body"])
    assert seen["current"] == "Post body."  # the English is what gets rewritten
