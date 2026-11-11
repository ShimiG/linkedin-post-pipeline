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
    monkeypatch.setattr(linkedin, "publish", lambda *a, **k: pytest.fail("must not publish in simulated mode"))
    review.approve(CFG, issue())
    names = [e[0] for e in board]
    assert names == ["comment", "add_labels", "close"]
    assert "Simulated" in board[0][1][1] and "Source: https://ex.com/a" in board[0][1][1]
    assert board[1][1][1] == ["simulated"]


def test_approve_live_publishes_with_source_comment(board, monkeypatch):
    sent = {}
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.update(text=text, fc=fc) or ("urn:li:share:1", "https://li/1", None))
    review.approve({**CFG, "publish_mode": "linkedin"}, issue())
    assert sent == {"text": "גוף הפוסט.\n\n• • •\n\nPost body.", "fc": "Source: https://ex.com/a"}
    assert ("add_labels", (7, ["published"]), {}) in board


def test_approve_refuses_double_publish(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda *a, **k: pytest.fail("published twice"))
    review.approve({**CFG, "publish_mode": "linkedin"}, issue(("draft", "published")))
    assert board[0][1][1].startswith("Already published")


def test_approve_linkedin_error_keeps_issue_open(board, monkeypatch):
    def boom(*a, **k):
        raise linkedin.LinkedInError("token expired")
    monkeypatch.setattr(linkedin, "publish", boom)
    review.approve({**CFG, "publish_mode": "linkedin"}, issue())
    assert [e[0] for e in board] == ["comment"] and "token expired" in board[0][1][1]


def test_reject_does_not_block(board):
    review.reject({**CFG, "review": {"reject_cooldown_days": 7}}, issue())
    assert ("close", (7, "not_planned"), {}) in board
    assert ("add_labels", (7, ["rejected"]), {}) in board
    text = board[1][1][1]
    assert "after 7 days" in text and "/block" in text


def test_block_story_and_site(board, monkeypatch):
    review.block(issue(), "")
    assert ("add_labels", (7, ["blocked"]), {}) in board and ("close", (7, "not_planned"), {}) in board
    board.clear()
    review.block(issue(), "site")
    assert ("add_labels", (7, ["blocked", "blocked-site"]), {}) in board
    assert "ex.com" in [e for e in board if e[0] == "comment"][0][1][1]


def test_block_on_closed_issue_does_not_close_again(board):
    i = issue(); i["state"] = "closed"
    review.block(i, "")
    assert not [e for e in board if e[0] == "close"]


def test_block_site_refused_for_facts(board):
    i = issue()
    i["body"] = i["body"].replace('"kind": "news"', '"kind": "fact"')
    review.block(i, "site")
    assert [e[0] for e in board] == ["comment"] and "only works on news" in board[0][1][1]


def test_unblock(board, monkeypatch):
    removed = []
    monkeypatch.setattr(gh, "remove_label", lambda n, l: removed.append(l))
    review.unblock(issue(("draft", "blocked", "blocked-site")))
    assert removed == ["blocked", "blocked-site"]


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


LIVE = {**CFG, "publish_mode": "linkedin"}


def live_with(mode):
    return {**LIVE, "post": {"source_link": mode}}


def test_manual_mode_never_calls_comment_api_and_reminds(board, monkeypatch):
    sent = {}
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.update(fc=fc) or ("urn:li:share:1", "https://li/1", None))
    review.approve(live_with("manual"), issue())
    assert sent["fc"] is None
    msg = [e for e in board if e[0] == "comment"][0][1][1]
    assert "Add the source as the first comment" in msg and "Source: https://ex.com/a" in msg and "https://li/1" in msg


def test_auto_mode_falls_back_to_reminder_on_403(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: ("urn:li:share:1", "https://li/1", "LinkedIn refused (403)"))
    review.approve(live_with("auto"), issue())
    msg = [e for e in board if e[0] == "comment"][0][1][1]
    assert "could not add it" in msg and "Source: https://ex.com/a" in msg


def test_auto_mode_success_needs_no_reminder(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: ("urn:li:share:1", "https://li/1", None))
    review.approve(live_with("auto"), issue())
    assert "first comment" not in [e for e in board if e[0] == "comment"][0][1][1]


def test_off_mode(board, monkeypatch):
    sent = {}
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.update(fc=fc) or ("urn:li:share:1", "https://li/1", None))
    review.approve(live_with("off"), issue())
    assert sent["fc"] is None and "first comment" not in [e for e in board if e[0] == "comment"][0][1][1]


def test_source_link_mode_defaults_and_legacy_key():
    assert review.source_link_mode({"post": {}}) == "manual"
    assert review.source_link_mode({"post": {"source_link_as_comment": True}}) == "auto"
    assert review.source_link_mode({"post": {"source_link_as_comment": False}}) == "off"
    assert review.source_link_mode({"post": {"source_link": "MANUAL"}}) == "manual"
