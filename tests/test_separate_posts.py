"""Hebrew and English as separate posts, right-to-left marks, posting times, translatorese and the hook length."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from poster import compose, gh, guardrail, linkedin, review, scheduled, sources, timing
from poster.compose import RLM, Draft

TZ = ZoneInfo("Asia/Jerusalem")
TODAY = date(2026, 11, 11)
CFG = {"publish_mode": "linkedin", "timezone": "Asia/Jerusalem", "post": {"source_link": "manual"},
       "bilingual": {"enabled": True, "publish": "separate", "english_marker": "*English below", "separator": "• • •"}}
HE = "Claude מקצר זמן מיגרציה\n\nהמודל עבר ל-Kubernetes.\n\nמה אתם חושבים?"
EN = "Claude cuts migration time.\n\nThe model moved to Kubernetes.\n\nWhat do you think? #AI"


# --------------------------------------------------------------------------- right-to-left marks
def test_rtl_marks_fix_lines_that_open_or_close_in_english():
    out = compose.rtl_marks(HE).split("\n")
    assert out[0] == RLM + "Claude מקצר זמן מיגרציה"
    assert out[2] == "המודל עבר ל-Kubernetes" + RLM + "."
    assert out[4] == "מה אתם חושבים?"  # already right to left: untouched
    assert compose.rtl_marks("*English below\n#AI #LLM") == "*English below\n#AI #LLM"  # no Hebrew: untouched
    assert compose.rtl_marks(compose.rtl_marks(HE)) == compose.rtl_marks(HE)  # safe to apply twice


def test_versions_are_published_on_their_own():
    he = "כותרת\n*English below\n\nגוף."
    assert compose.version(CFG, he, EN, "he") == "כותרת\n\nגוף."
    assert compose.version(CFG, he, EN, "en") == EN
    assert compose.version(CFG, he, EN, "both") == f"{he}\n\n• • •\n\n{EN}"
    assert compose.hebrew_part(CFG, "כותרת", "גוף.") == "כותרת\n\nגוף."  # separate: no "*English below" line
    combined = {**CFG, "bilingual": {**CFG["bilingual"], "publish": "combined"}}
    assert compose.hebrew_part(combined, "כותרת", "גוף.") == "כותרת\n*English below\n\nגוף."


# --------------------------------------------------------------------------- translation and hook checks
def test_translatorese_and_slash_gender_are_sent_back():
    res = guardrail.check_translation("בסופו של יום, זה עובד.", "At the end of the day, it works.", today=TODAY)
    assert not res.ok and res.reason == "translatorese" and "בסופו של יום" in res.offending
    res = guardrail.check_translation("נסה/י את זה ותגיד/י מה יצא.", "Try it and tell me.", today=TODAY)
    assert not res.ok and res.offending == ["נסה/י", "ותגיד/י"]
    assert "plural" in guardrail.translation_retry_note(res)
    assert guardrail.check_translation("קלט/פלט עובד מצוין.", "I/O works well.", today=TODAY).ok


def test_formal_words_only_add_a_note():
    assert guardrail.check_translation("עשינו את זה על מנת לחסוך זמן.", "We did it to save time.", today=TODAY).ok
    assert guardrail.formal_words("על מנת לחסוך, וכמו כן") == ["formal wording, consider: על מנת (better: כדי), כמו כן (better: וגם)"]
    assert guardrail.formal_words("כדי לחסוך") == []


def test_first_line_must_fit_before_see_more():
    long = "A" + " word" * 40 + ".\n\nSecond paragraph."
    kw = dict(author={}, topic={"kind": "fact"}, news_items=[], today=TODAY, max_chars=1300, max_hashtags=3)
    res = guardrail.check(long, hook_max=140, **kw)
    assert not res.ok and res.reason == "hook_too_long" and "120 characters" in guardrail.retry_note(res)
    assert guardrail.check(long, **kw).ok  # off when not configured
    assert guardrail.check("Short hook.\n\n" + "word " * 40, hook_max=140, **kw).ok


# --------------------------------------------------------------------------- AI news stays on topic
def test_news_titles_must_really_be_about_ai():
    words = ["AI", "LLM", "Claude", "GPT", "ChatGPT", "machine learning"]
    assert not sources.on_topic("Foldable Flight: The Best Paper Airplanes", words)
    assert not sources.on_topic("Airbnb cuts jobs", words)
    for t in ("Claude Fable 5.2", "GPT-5.5 is out", "Why LLMs fail", "The AI's new trick", "Machine Learning at scale"):
        assert sources.on_topic(t, words), t


# --------------------------------------------------------------------------- posting times
def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def test_quiet_hours_are_the_israeli_weekend(monkeypatch):
    monkeypatch.undo()  # conftest turns the quiet check off
    assert not timing.quiet(CFG, at("2026-11-13T11:59"))  # Friday morning
    assert timing.quiet(CFG, at("2026-11-13T12:00")) and timing.quiet(CFG, at("2026-11-14T23:00"))
    assert not timing.quiet(CFG, at("2026-11-15T08:00"))  # Sunday


def test_next_slot():
    assert timing.next_slot(CFG, "he", at("2026-11-13T13:00")) == at("2026-11-15T08:30")  # Friday -> Sunday morning
    assert timing.next_slot(CFG, "en", at("2026-11-13T13:00")) == at("2026-11-16T09:30")  # English waits for Monday
    assert timing.next_slot(CFG, "he", at("2026-11-11T09:02")) == at("2026-11-11T09:15")  # inside the slot: soon
    assert timing.next_slot(CFG, "he", at("2026-11-11T10:25")) == at("2026-11-12T08:30")  # too late today
    assert timing.next_slot(CFG, "he", at("2026-11-11T07:00"), avoid_days={at("2026-11-11T00:00").date()}) == at("2026-11-12T08:30")


# --------------------------------------------------------------------------- /approve hebrew | english
@pytest.fixture
def board(monkeypatch):
    log = []
    for name in ("comment", "add_labels", "remove_label", "close", "update_issue", "set_workflow"):
        monkeypatch.setattr(gh, name, lambda *a, _n=name, **k: log.append((_n, a, k)))
    return log


@pytest.fixture
def posted(monkeypatch):
    sent = []
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.append(text) or ("urn:li:share:1", "https://li/1", None))
    return sent


def issue(labels=("draft",), he=HE):
    topic = {"kind": "news", "title": "T", "category": "ai_news", "angle": "", "source_url": "https://ex.com/a"}
    body = gh.render_draft(topic, [], Draft(EN, model="m", post_text_he=he, translation_model="m"), separate=True)
    return {"number": 7, "body": body, "labels": [{"name": l} for l in labels], "state": "open"}


def test_draft_issue_offers_each_version():
    body = issue()["body"]
    assert "Hebrew post (**`/approve hebrew`**)" in body and "English post (**`/approve english`**)" in body
    assert "/schedule hebrew next" in body and gh.parse_parts(body) == (HE, EN)


def test_approve_without_a_version_asks(board, posted):
    assert not review.approve(CFG, issue())
    assert posted == [] and "/approve hebrew" in board[0][1][1] and "/approve english" in board[0][1][1]


def test_approve_english_then_hebrew(board, posted):
    first = issue()
    assert review.approve(CFG, first, "english")
    assert posted == [EN]
    assert ("add_labels", (7, ["published", "published-en"]), {}) in board
    assert "close" not in [e[0] for e in board]  # open for the Hebrew version
    msg = board[0][1][1]
    assert "Published the English version" in msg and "/schedule hebrew next" in msg and "first hour" in msg
    assert "en" in gh.published_times(first["body"])

    board.clear()
    assert review.approve(CFG, first, "english") is False and "Already published" in board[0][1][1]
    board.clear()
    assert review.approve(CFG, first, "hebrew")
    assert posted[1] == compose.rtl_marks(HE) and posted[1].startswith(RLM + "Claude")
    assert ("close", (7,), {}) in board and "Still to post" not in board[0][1][1]


def test_approve_both_is_one_post_and_english_only_drafts_need_no_version(board, posted):
    review.approve(CFG, issue(), "both")
    assert posted == [compose.version(CFG, HE, EN, "both")] and ("close", (7,), {}) in board
    board.clear()
    review.approve(CFG, issue(he=""))
    assert posted[1] == EN and ("close", (7,), {}) in board
    board.clear()
    review.approve(CFG, issue(he=""), "hebrew")
    assert "no Hebrew version" in board[0][1][1]


def test_weekend_approve_asks_first(board, posted, monkeypatch):
    monkeypatch.setattr(timing, "quiet", lambda cfg, at: True)
    assert not review.approve(CFG, issue(), "hebrew")
    assert posted == [] and "/schedule hebrew next" in board[0][1][1] and "/approve hebrew anyway" in board[0][1][1]
    assert review.approve(CFG, issue(), "hebrew anyway") and len(posted) == 1


# --------------------------------------------------------------------------- /schedule per version
def test_schedules_per_version_roundtrip():
    a, b = at("2026-12-25T09:30"), at("2026-12-27T08:30")
    body = gh.with_publish_at(gh.with_publish_at("text", a, "en"), b, "he")
    assert gh.schedules(body) == {"en": a, "he": b} and gh.publish_at(body) == a
    body = gh.with_publish_at(body, None, "en")
    assert gh.schedules(body) == {"he": b}
    assert gh.schedules("x\n<!-- publish-at: 2026-12-25T09:30+02:00 -->") == {"both": a}  # older drafts


def test_schedule_next_keeps_the_versions_apart(board, monkeypatch):
    now = at("2026-11-15T09:00")  # Sunday, inside the Hebrew slot
    monkeypatch.setattr(review, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: now)}))
    i = issue()
    i["body"] = gh.with_published(i["body"], "he", now)
    review.schedule(CFG, i, "english next")
    body = next(e for e in board if e[0] == "update_issue")[2]["body"]
    assert gh.schedules(body) == {"en": at("2026-11-16T09:30")}  # Monday: not the day the Hebrew went out
    assert "English version is scheduled" in board[-1][1][1]

    board.clear()
    review.schedule(CFG, issue(), "next")
    assert "Which version?" in board[0][1][1] and "/schedule hebrew" in board[0][1][1]


def test_scheduled_run_posts_only_the_due_version(board, posted, monkeypatch):
    now = datetime.now(TZ)
    i = issue(("draft", "scheduled"))
    i["body"] = gh.with_publish_at(gh.with_publish_at(i["body"], now - timedelta(minutes=5), "he"), now + timedelta(days=1), "en")
    monkeypatch.setattr(gh, "issues", lambda *a, **k: [i])
    assert scheduled.run(CFG, now) == (1, 0)
    assert posted == [compose.rtl_marks(HE)]
    assert gh.schedules(i["body"]).keys() == {"en"} and ("remove_label", (7, "scheduled"), {}) not in board
