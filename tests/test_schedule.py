from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from poster import gh, linkedin, review, scheduled
from poster.compose import Draft
from poster.http import HttpError

CFG = {"publish_mode": "linkedin", "timezone": "Asia/Jerusalem", "post": {"source_link": "manual"},
       "bilingual": {"enabled": True, "separator": "• • •"}}
TZ = ZoneInfo("Asia/Jerusalem")
FUTURE = datetime.now(TZ) + timedelta(days=3)
FUTURE_ARG = FUTURE.strftime("%d/%m/%Y %H:%M")


@pytest.fixture
def board(monkeypatch):
    log = []
    for name in ("comment", "add_labels", "remove_label", "close", "update_issue", "set_workflow"):
        monkeypatch.setattr(gh, name, lambda *a, _n=name, **k: log.append((_n, a, k)))
    monkeypatch.setattr(linkedin, "publish", lambda *a, **k: pytest.fail("must not publish"))
    return log


def names(board):
    return [e[0] for e in board]


def issue(labels=("draft",), when=None, number=7, kind="news", en="Post body."):
    topic = {"kind": kind, "title": "T", "category": "ai_news", "angle": "", "source_url": "https://ex.com/a"}
    if kind == "onthisday":
        topic["otd_date"] = "2026-11-10"
    body = gh.render_draft(topic, [], Draft(en, model="m", post_text_he="גוף הפוסט.", translation_model="m"))
    return {"number": number, "body": gh.with_publish_at(body, when), "labels": [{"name": l} for l in labels], "state": "open"}


# --------------------------------------------------------------------------- the marker in the issue body
def test_publish_at_roundtrip_and_replace():
    a, b = datetime(2026, 12, 25, 9, 30, tzinfo=TZ), datetime(2026, 12, 26, 18, 0, tzinfo=TZ)
    body = gh.with_publish_at("text\n<!-- meta: {} -->", a)
    assert gh.publish_at(body) == a and gh.parse_meta(body) == {}
    body = gh.with_publish_at(body, b)
    assert gh.publish_at(body) == b and body.count("publish-at") == 1
    assert gh.with_publish_at(body, None) == "text\n<!-- meta: {} -->"
    assert gh.publish_at("no marker") is None and gh.publish_at("<!-- publish-at: tomorrow -->") is None


def test_rewrite_keeps_the_schedule():
    when = datetime(2026, 12, 25, 9, 30, tzinfo=TZ)
    assert gh.publish_at(gh.replace_parts(issue(when=when)["body"], "חדש.", "New.")) == when


# --------------------------------------------------------------------------- /schedule
def test_parse_when_reads_local_time():
    when, anyway = review.parse_when(CFG, "25/12/2026 09:30")
    assert when == datetime(2026, 12, 25, 7, 30, tzinfo=timezone.utc) and not anyway   # Israel is UTC+2 in December
    assert review.parse_when(CFG, "5/1/2027 7:05 anyway") == (datetime(2027, 1, 5, 7, 5, tzinfo=TZ), True)
    for bad in ("", "tomorrow 9am", "2026-12-25 09:30", "12/25/2026 09:30", "31/02/2027 10:00", "25/12/2026 25:00", "25/12/2026"):
        with pytest.raises(ValueError):
            review.parse_when(CFG, bad)


def test_schedule_stores_the_time_and_switches_the_publisher_on(board):
    review.schedule(CFG, issue(), FUTURE_ARG)
    assert names(board) == ["update_issue", "add_labels", "set_workflow", "comment"]
    assert gh.publish_at(board[0][2]["body"]) == FUTURE.replace(second=0, microsecond=0)
    assert board[1][1] == (7, ["scheduled"]) and board[2][1] == ("scheduled.yml", True)
    text = board[3][1][1]
    assert FUTURE.strftime("%H:%M") in text and "(Asia/Jerusalem)" in text and "/unschedule" in text and "UTC" not in text


def test_schedule_in_utc_says_so(board):
    review.schedule({**CFG, "timezone": "UTC"}, issue(), "25/12/2099 09:30")
    assert "repo variable `TIMEZONE`" in board[-1][1][1]


@pytest.mark.parametrize("arg, expect", [("soon", "/schedule dd/mm/yyyy HH:MM"), ("", "/schedule dd/mm/yyyy HH:MM"),
                                         ("01/01/2020 09:00", "already passed")])
def test_schedule_refuses_bad_or_past_times(board, arg, expect):
    review.schedule(CFG, issue(), arg)
    assert names(board) == ["comment"] and expect in board[0][1][1]


def test_schedule_checks_the_post_now(board):
    review.schedule(CFG, issue(en="x" * 3100), FUTURE_ARG)
    assert names(board) == ["comment"] and "LinkedIn allows 3000" in board[0][1][1]


def test_schedule_warns_when_the_publisher_cannot_be_switched_on(board, monkeypatch):
    def denied(*a):
        raise HttpError(403, "Resource not accessible by integration", "u")
    monkeypatch.setattr(gh, "set_workflow", denied)
    review.schedule(CFG, issue(), FUTURE_ARG)
    assert names(board) == ["update_issue", "add_labels", "comment"] and "Enable it in the Actions tab" in board[-1][1][1]


def test_on_this_day_post_is_scheduled_only_for_its_day(board):
    review.schedule(CFG, issue(kind="onthisday"), "11/11/2099 09:00")
    assert names(board) == ["comment"] and "/schedule 11/11/2099 09:00 anyway" in board[0][1][1]
    board.clear()
    review.schedule(CFG, issue(kind="onthisday"), "11/11/2099 09:00 anyway")
    assert "add_labels" in names(board)
    board.clear()
    review.schedule(CFG, issue(kind="onthisday"), "10/11/2099 09:00")
    assert "add_labels" in names(board)


def test_unschedule(board):
    review.unschedule(issue())
    assert names(board) == ["comment"] and "not scheduled" in board[0][1][1]
    board.clear()
    review.unschedule(issue(("draft", "scheduled"), FUTURE))
    assert names(board) == ["remove_label", "update_issue", "comment"]
    assert board[0][1] == (7, "scheduled") and gh.publish_at(board[1][2]["body"]) is None


def test_approve_now_takes_the_draft_off_the_schedule(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: ("urn:li:share:1", "https://li/1", None))
    assert review.approve(CFG, issue(("draft", "scheduled"), FUTURE))
    assert names(board) == ["remove_label", "update_issue", "comment", "add_labels", "close"]


# --------------------------------------------------------------------------- the scheduled run
NOW = datetime(2026, 12, 25, 8, 0, tzinfo=timezone.utc)
DUE, LATER = NOW - timedelta(minutes=3), NOW + timedelta(hours=2)


def test_run_publishes_what_is_due_and_leaves_the_rest(board, monkeypatch):
    sent = []
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.append(text) or ("urn:li:share:1", "https://li/1", None))
    waiting = [issue(("draft", "scheduled"), DUE, 1), issue(("draft", "scheduled"), LATER, 2), issue(("scheduled",), DUE, 3)]
    monkeypatch.setattr(gh, "issues", lambda label, state="all": waiting)
    assert scheduled.run(CFG, NOW) == (1, 0)
    assert sent == ["גוף הפוסט.\n\n• • •\n\nPost body."]
    assert {e[1][0] for e in board} == {1}                                   # only the due draft was touched
    assert names(board) == ["remove_label", "update_issue", "comment", "add_labels", "close"]   # off the schedule first
    assert board[2][1][1].startswith(":alarm_clock: Scheduled time reached. :rocket: Published")
    assert board[3][1] == (1, ["published"])


def test_run_skips_the_on_this_day_date_check(board, monkeypatch):
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: ("urn:li:share:1", "https://li/1", None))
    monkeypatch.setattr(review, "today", lambda cfg: date(2026, 12, 25))     # not 10 November: /schedule already asked
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"), DUE, kind="onthisday")])
    assert scheduled.run(CFG, NOW) == (0, 0) and "close" in names(board)


def test_failed_publish_cancels_the_schedule_and_is_not_retried(board, monkeypatch):
    def boom(*a, **k):
        raise linkedin.LinkedInError("token expired")
    monkeypatch.setattr(linkedin, "publish", boom)
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"), DUE)])
    assert scheduled.run(CFG, NOW) == (0, 0)
    assert names(board) == ["remove_label", "update_issue", "comment"]
    assert "token expired" in board[2][1][1] and "The schedule is cancelled" in board[2][1][1]


def test_missing_time_is_reported_not_published(board, monkeypatch):
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"))])
    assert scheduled.run(CFG, NOW) == (0, 0)
    assert names(board) == ["remove_label", "comment"] and "Nothing was posted" in board[1][1][1]


def test_one_broken_draft_does_not_block_the_next(board, monkeypatch):
    sent = []
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.append(text) or ("urn:li:share:1", "https://li/1", None))
    real = gh.remove_label

    def flaky(number, label):
        if number == 1:
            raise HttpError(502, "bad gateway", "u")
        return real(number, label)
    monkeypatch.setattr(gh, "remove_label", flaky)
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"), DUE, 1), issue(("draft", "scheduled"), DUE, 2)])
    assert scheduled.run(CFG, NOW) == (0, 1)
    assert len(sent) == 1 and {e[1][0] for e in board} == {2}                # #1 was never posted: it could not be claimed


def test_simulated_mode_only_shows_the_post(board, monkeypatch):
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"), DUE)])
    assert scheduled.run({**CFG, "publish_mode": "simulated"}, NOW) == (0, 0)
    assert names(board) == ["remove_label", "update_issue", "comment", "add_labels", "close"] and "**Simulated**" in board[2][1][1]


def test_switches_itself_off_only_when_nothing_is_waiting(board, monkeypatch):
    monkeypatch.setattr(scheduled, "load_config", lambda: CFG)
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [])
    assert scheduled.main() == 0 and board == [("set_workflow", ("scheduled.yml", False), {})]
    board.clear()
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"), datetime.now(timezone.utc) + timedelta(days=1))])
    assert scheduled.main() == 0 and board == []


def test_a_schedule_arriving_during_switch_off_switches_it_back_on(board, monkeypatch):
    monkeypatch.setattr(gh, "issues", lambda label, state="all": [issue(("draft", "scheduled"), LATER)])
    scheduled.switch_off()
    assert [e[1] for e in board] == [("scheduled.yml", False), ("scheduled.yml", True)]
