import json
from datetime import date, datetime, timezone

import pytest

from poster import gh, guardrail, linkedin, review, sources
from poster.compose import Draft
from poster.draft import History, kind_order

DAY = date(2026, 11, 10)
CFG = {"on_this_day": {"sections": ["selected", "events", "births", "deaths"], "min_score": 1, "min_years_ago": 1,
                       "max_candidates": 5,
                       "keywords": ["computer", "software", "computer scientist", "Microsoft", "AI", "operating system"]}}


def page(title, description="", extract=""):
    return {"title": title.replace(" ", "_"), "normalizedtitle": title, "description": description, "extract": extract,
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")}}}


WINDOWS = {"text": "Microsoft introduces Windows 1.0, a graphical operating system shell.", "year": 1983,
           "pages": [page("Bill Gates", "American businessman", "Bill Gates is an American businessman. " * 20),
                     page("Windows 1.0", "Operating system by Microsoft", "Windows 1.0 is a graphical shell.")]}
BATTLE = {"text": "A fleet sails into the harbour and the battle begins.", "year": 1444,
          "pages": [page("Battle of Varna", "1444 battle")]}
FEED = {
    "selected": [WINDOWS],
    "events": [BATTLE, WINDOWS,
               {"text": "The AI Act is discussed again.", "year": 2026, "pages": []},          # this year: not history
               {"text": "A river floods the old town.", "year": 1970, "pages": [page("Flood", "Natural disaster")]}],
    "births": [{"text": "Jane Roe, American computer scientist", "year": 1950,
                "pages": [page("Jane Roe", "American computer scientist")]},
               {"text": "John Doe, English footballer", "year": 1960, "pages": [page("John Doe", "English footballer")]}],
    "deaths": [{"text": "Ai Weiwei's teacher, a Chinese painter", "year": 1990, "pages": [page("Painter", "Chinese painter")]}],
    "holidays": [{"text": "World Computer Day", "pages": []}],
}


# --------------------------------------------------------------------------- relevance
def test_keyword_hits_whole_words_plurals_and_caps():
    kws = CFG["on_this_day"]["keywords"]
    assert sources.keyword_hits(kws, "Two computers and some SOFTWARE") == {"computer", "software"}
    assert sources.keyword_hits(kws, "A supercomputer in Aix, said Ai Weiwei") == set()   # no substrings, AI is exact
    assert sources.keyword_hits(kws, "AI research at Microsoft.") == {"ai", "microsoft"}


def test_candidates_keep_only_relevant_history_and_rank_it():
    out = sources.onthisday_candidates(CFG, DAY, feed=FEED)
    assert [c["otd_id"] for c in out] == ["11-10/1983/Windows 1.0", "11-10/1950/Jane Roe"]   # selected+events deduped
    win, born = out
    assert win["years_ago"] == 43 and win["page"]["title"] == "Windows 1.0"   # the article the keywords point at
    assert born["text"] == "Jane Roe, American computer scientist was born" and born["section"] == "births"


def test_used_events_are_skipped():
    out = sources.onthisday_candidates(CFG, DAY, used_ids={"11-10/1983/Windows 1.0"}, feed=FEED)
    assert [c["otd_id"] for c in out] == ["11-10/1950/Jane Roe"]


def test_no_relevant_event_gives_none(monkeypatch):
    monkeypatch.setattr(sources, "onthisday_feed", lambda day: {"events": [BATTLE]})
    assert sources.pick_onthisday(CFG, set(), DAY) is None


def test_feed_falls_back_to_second_host(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("down")
        return {"events": []}

    monkeypatch.setattr(sources, "get_json", fake)
    assert sources.onthisday_feed(DAY) == {"events": []}
    assert calls[0].endswith("/onthisday/all/11/10") and "api.wikimedia.org" in calls[1]


# --------------------------------------------------------------------------- the picked topic
BACKGROUND = ("Windows 1.0 is the first major release of Microsoft Windows, a family of graphical operating systems. "
              "It was announced in 1983 and shipped two years later, running on top of MS-DOS as a 16-bit shell. ") * 2


@pytest.fixture
def picked(monkeypatch):
    monkeypatch.setattr(sources, "onthisday_feed", lambda day: FEED)
    monkeypatch.setattr(sources, "wiki_intro", lambda t: {"title": t, "extract": BACKGROUND, "url": "https://en.wikipedia.org/wiki/Windows_1.0"})
    return sources.pick_onthisday(CFG, set(), DAY)


def test_pick_builds_topic_and_source_item(picked):
    topic, (item,) = picked
    assert topic["kind"] == "onthisday" and topic["category"] == "on_this_day"
    assert topic["otd_id"] == "11-10/1983/Windows 1.0" and topic["otd_date"] == "2026-11-10"
    assert topic["title"].startswith("On this day in 1983: Microsoft introduces Windows 1.0")
    assert topic["source_url"] == "https://en.wikipedia.org/wiki/Windows_1.0"
    assert item["date"] == "1983-11-10" and item["years_ago"] == 43 and item["source"] == "wikipedia.org"
    assert item["snippet"].startswith("On 10 November 1983: Microsoft introduces")
    assert "\nWindows 1.0: Windows 1.0 is the first major release" in item["snippet"]      # the main article, in full
    assert "\nBill Gates: Bill Gates is an American businessman." in item["snippet"]        # the other one, its opening only
    assert len(item["snippet"]) < 1500 and item["snippet"].endswith("…")


def test_feed_extract_is_used_when_the_article_cannot_be_read(monkeypatch):
    monkeypatch.setattr(sources, "onthisday_feed", lambda day: {"births": [
        {"text": "Jane Roe, American computer scientist", "year": 1950,
         "pages": [page("Jane Roe", "American computer scientist", "Jane Roe wrote an early compiler. " * 8)]}]})
    monkeypatch.setattr(sources, "wiki_intro", lambda t: (_ for _ in ()).throw(OSError("down")))
    topic, (item,) = sources.pick_onthisday(CFG, set(), DAY)
    assert "Jane Roe: Jane Roe wrote an early compiler." in item["snippet"]
    assert topic["source_url"] == "https://en.wikipedia.org/wiki/Jane_Roe"


def test_thin_event_falls_through_to_the_next(monkeypatch):
    bare = {"text": "Microsoft ships new software.", "year": 1983, "pages": [page("Microsoft", "Software company")]}
    monkeypatch.setattr(sources, "onthisday_feed", lambda day: {"events": [bare], "births": FEED["births"]})
    monkeypatch.setattr(sources, "wiki_intro", lambda t: None if t == "Microsoft" else {"title": t, "extract": BACKGROUND, "url": "u"})
    topic, _ = sources.pick_onthisday(CFG, set(), DAY)
    assert topic["otd_id"] == "11-10/1950/Jane Roe"


def test_guardrail_allows_the_year_and_the_gap_but_no_other_number(picked):
    topic, news = picked
    kw = dict(author={"name": "Dev"}, topic=topic, news_items=news, today=DAY, max_chars=1300, max_hashtags=3)
    ok = guardrail.check("On this day in 1983, Microsoft introduced Windows 1.0.\n\n43 years ago today, a 16-bit shell on MS-DOS.", **kw)
    assert ok.ok
    bad = guardrail.check("On this day in 1983, Microsoft introduced Windows 1.0, and sold 500,000 copies.", **kw)
    assert bad.reason == "fact_check" and bad.offending == ["500,000"]


def test_draft_issue_roundtrip(picked):
    topic, news = picked
    body = gh.render_draft(topic, news, Draft("On this day in 1983.", model="m"), ["tied to 10 November"])
    assert body.startswith("**On this day**") and "/block site" not in body
    meta = gh.parse_meta(body)
    assert meta["topic"]["otd_id"] == topic["otd_id"] and meta["news"][0]["years_ago"] == 43


# --------------------------------------------------------------------------- history and rotation
def test_history_skips_drafted_events_and_tracks_the_kind():
    t = {"kind": "onthisday", "otd_id": "11-10/1983/Windows 1.0", "otd_date": "2026-11-10"}
    body = f"text\n<!-- meta: {json.dumps({'kind': 'onthisday', 'topic': t, 'news': []})} -->"
    old_reject = {"labels": [{"name": "draft"}, {"name": "rejected"}], "body": body, "closed_at": "2026-01-01T00:00:00Z"}
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    h = History([{"labels": [{"name": "draft"}, {"name": "published"}], "body": body, "closed_at": None}], 7, now)
    assert h.skip_otd == {"11-10/1983/Windows 1.0"} and h.last_kind == "onthisday"
    assert History([old_reject], 7, now).skip_otd == set()


def test_rotation_includes_onthisday_and_a_forced_kind_runs_first():
    cfg = {"rotation": ["news", "fact", "onthisday"]}
    assert kind_order(cfg, "fact", "auto") == ["onthisday", "news", "fact"]
    assert kind_order(cfg, "onthisday", "auto") == ["news", "fact", "onthisday"]
    assert kind_order({"rotation": ["news", "fact"]}, "news", "onthisday") == ["onthisday", "news", "fact"]


# --------------------------------------------------------------------------- approving on another day
RCFG = {"publish_mode": "simulated", "timezone": "UTC", "post": {"source_link": "manual"}, "bilingual": {"enabled": False}}


@pytest.fixture
def board(monkeypatch):
    log = []
    for name in ("comment", "add_labels", "close"):
        monkeypatch.setattr(gh, name, lambda *a, _n=name, **k: log.append((_n, a, k)))
    monkeypatch.setattr(linkedin, "publish", lambda *a, **k: pytest.fail("must not publish"))
    return log


def otd_issue():
    topic = {"kind": "onthisday", "title": "On this day in 1983: X", "category": "on_this_day", "angle": "",
             "otd_id": "11-10/1983/X", "otd_date": "2026-11-10", "source_url": "https://en.wikipedia.org/wiki/X"}
    body = gh.render_draft(topic, [], Draft("On this day in 1983.", model="m"))
    return {"number": 9, "body": body, "labels": [{"name": "draft"}], "state": "open"}


def test_approve_on_the_day_goes_through(board, monkeypatch):
    monkeypatch.setattr(review, "today", lambda cfg: date(2026, 11, 10))
    review.approve(RCFG, otd_issue())
    assert [e[0] for e in board] == ["comment", "add_labels", "close"] and "Simulated" in board[0][1][1]


def test_approve_on_another_day_asks_first(board, monkeypatch):
    monkeypatch.setattr(review, "today", lambda cfg: date(2026, 11, 11))
    review.approve(RCFG, otd_issue())
    assert [e[0] for e in board] == ["comment"]
    assert "10 November" in board[0][1][1] and "/approve anyway" in board[0][1][1]
    board.clear()
    review.approve(RCFG, otd_issue(), "anyway")
    assert [e[0] for e in board] == ["comment", "add_labels", "close"]
