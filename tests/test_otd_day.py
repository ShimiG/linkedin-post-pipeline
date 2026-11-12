""""On this day" drafts are written for the next free posting day, not for the day the draft is made."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from poster import compose, draft, gh, linkedin, review, timing
from poster.compose import Draft

TZ = ZoneInfo("Asia/Jerusalem")
CFG = {"publish_mode": "linkedin", "timezone": "Asia/Jerusalem", "post": {"source_link": "manual"},
       "bilingual": {"enabled": True, "publish": "separate", "english_marker": "*English below", "separator": "• • •"}}
THU, FRI, SUN, MON, TUE = (date(2026, 11, d) for d in (12, 13, 15, 16, 17))


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def test_open_day_is_a_later_posting_day_with_nothing_on_it():
    assert timing.open_day(CFG, THU, langs=("he",)) == SUN                  # Friday and Saturday have no slot
    assert timing.open_day(CFG, THU, langs=("he", "en")) == MON             # both versions: English starts on Monday
    assert timing.open_day(CFG, SUN, langs=("he", "en")) == MON             # never the draft's own day
    assert timing.open_day(CFG, SUN, taken={MON}, langs=("he", "en")) == TUE
    every = {date(2026, 11, d) for d in range(13, 31)} | {date(2026, 12, d) for d in range(1, 5)}
    assert timing.open_day(CFG, THU, taken=every, langs=("he",)) == SUN     # all taken: the first posting day anyway


def test_slot_on_a_given_day():
    assert timing.slot_on(CFG, "en", MON, at("2026-11-12T07:00")) == at("2026-11-16T09:30")
    assert timing.slot_on(CFG, "en", SUN, at("2026-11-12T07:00")) == at("2026-11-15T08:30")  # no English slot: the Hebrew one
    assert timing.slot_on(CFG, "he", MON, at("2026-11-16T12:00")) is None                     # that day's slot is over


def body_with(topic, labels_extra=""):
    return f"text\n<!-- meta: {json.dumps({'kind': topic['kind'], 'topic': topic, 'news': []})} -->{labels_extra}"


def test_draft_picks_the_day_from_busy_days(monkeypatch):
    monkeypatch.setattr(draft, "today", lambda cfg: SUN)
    other = {"kind": "fact", "wiki_subject": "Git"}
    scheduled = {"labels": [{"name": "draft"}, {"name": "scheduled"}], "state": "open", "closed_at": None,
                 "body": gh.with_publish_at(body_with(other), at("2026-11-16T09:30"), "en")}
    h = draft.History([scheduled], 7, at("2026-11-15T07:00"))
    assert draft.otd_day(CFG, h) == TUE                                      # Monday already has a post scheduled
    published = {"labels": [{"name": "draft"}, {"name": "published"}], "state": "closed",
                 "closed_at": "2026-11-17T07:40:00Z", "body": body_with(other)}
    assert draft.otd_day(CFG, draft.History([scheduled, published], 7, at("2026-11-15T07:00"))) == date(2026, 11, 18)
    combined = {**CFG, "bilingual": {**CFG["bilingual"], "publish": "combined"}}
    monkeypatch.setattr(draft, "today", lambda cfg: THU)
    assert draft.otd_day(combined, draft.History([], 7, at("2026-11-12T07:00"))) == SUN  # one post: the Hebrew days


def test_post_is_written_for_its_day():
    topic = {"kind": "onthisday", "title": "T", "category": "on_this_day", "otd_date": "2026-11-16"}
    assert compose.post_day(CFG, topic) == MON
    assert compose.payload(CFG, {}, topic, [])["today"] == "2026-11-16"


# --------------------------------------------------------------------------- review commands
@pytest.fixture
def board(monkeypatch):
    log = []
    for name in ("comment", "add_labels", "remove_label", "close", "update_issue", "set_workflow"):
        monkeypatch.setattr(gh, name, lambda *a, _n=name, **k: log.append((_n, a, k)))
    return log


def otd_issue():
    topic = {"kind": "onthisday", "title": "On this day in 1983: Windows", "category": "on_this_day", "angle": "",
             "otd_id": "11-16/1983/Windows", "otd_date": "2026-11-16"}
    body = gh.render_draft(topic, [], Draft("On this day in 1983.", model="m", post_text_he="היום ב-1983.", translation_model="m"),
                           separate=True)
    return {"number": 9, "body": body, "labels": [{"name": "draft"}], "state": "open"}


def frozen(monkeypatch, now):
    clock = type("D", (datetime,), {"now": staticmethod(lambda tz=None: now)})
    monkeypatch.setattr(review, "datetime", clock)
    monkeypatch.setattr(review, "today", lambda cfg: now.date())


def test_schedule_next_uses_the_posts_day_for_both_versions(board, monkeypatch):
    frozen(monkeypatch, at("2026-11-12T08:00"))
    i = otd_issue()
    i["body"] = gh.with_published(i["body"], "he", at("2026-11-16T08:35"))  # same day as the English: allowed here
    review.schedule(CFG, i, "english next")
    body = next(e for e in board if e[0] == "update_issue")[2]["body"]
    assert gh.schedules(body) == {"en": at("2026-11-16T09:30")}


def test_schedule_next_after_the_day_asks(board, monkeypatch):
    frozen(monkeypatch, at("2026-11-17T08:00"))
    review.schedule(CFG, otd_issue(), "hebrew next")
    assert "posting time is over" in board[0][1][1] and "update_issue" not in [e[0] for e in board]


def test_approve_before_the_day_points_to_schedule_next(board, monkeypatch):
    frozen(monkeypatch, at("2026-11-12T09:00"))
    monkeypatch.setattr(linkedin, "publish", lambda *a, **k: pytest.fail("published early"))
    assert not review.approve(CFG, otd_issue(), "hebrew")
    msg = board[0][1][1]
    assert "written for 16 November" in msg and "/schedule hebrew next" in msg and "Monday 16 November 2026, 08:30" in msg


def test_approve_on_the_day_offers_the_other_version_the_same_day(board, monkeypatch):
    frozen(monkeypatch, at("2026-11-16T08:40"))
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: ("urn:li:share:1", "https://li/1", None))
    assert review.approve(CFG, otd_issue(), "hebrew")
    msg = board[0][1][1]
    assert "/schedule english next" in msg and "Monday 16 November 2026, 09:30" in msg and "same day" in msg
