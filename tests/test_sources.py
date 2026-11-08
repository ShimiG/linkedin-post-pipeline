import random

from poster import sources

CFG = {"news": {"lookback_days": 7, "min_points": 80, "queries": ["AI", "LLM"], "max_candidates": 8},
       "fact_subjects": ["Git", "UTF-8"]}


def test_hn_candidates_dedupes_filters_and_sorts(monkeypatch):
    hits = {
        "AI": [{"objectID": "1", "title": "A", "url": "https://a.com/x", "points": 100, "created_at_i": 1762600000},
               {"objectID": "2", "title": "Repo", "url": "https://github.com/x/y", "points": 900, "created_at_i": 1762600000},
               {"objectID": "3", "title": "Ask HN", "url": None, "points": 500, "created_at_i": 1762600000}],
        "LLM": [{"objectID": "1", "title": "A", "url": "https://a.com/x", "points": 100, "created_at_i": 1762600000},
                {"objectID": "4", "title": "B", "url": "https://www.b.org/y", "points": 300, "created_at_i": 1762600000},
                {"objectID": "5", "title": "Used", "url": "https://c.com", "points": 999, "created_at_i": 1762600000}],
    }
    monkeypatch.setattr(sources, "get_json", lambda url, **kw: {"hits": hits["AI" if "query=AI" in url else "LLM"]})
    out = sources.hn_candidates(CFG, used_ids={"5"}, now=1762700000)
    assert [c["hn_id"] for c in out] == ["4", "1"]
    assert out[0]["source"] == "b.org"


def test_article_extractor():
    html = """<html><head><meta name="description" content="Short summary."><script>var x=1</script></head>
    <body><nav><p>Menu menu menu menu menu menu menu menu menu menu menu menu menu</p></nav>
    <p>This paragraph is long enough to be kept because it has more than sixty characters.</p>
    <p>tiny</p><footer><p>Footer footer footer footer footer footer footer footer footer footer</p></footer></body></html>"""
    p = sources._TextExtractor()
    p.feed(html)
    assert p.description == "Short summary."
    assert p.paras == ["This paragraph is long enough to be kept because it has more than sixty characters."]


def test_pick_fact_skips_used_and_missing(monkeypatch):
    calls = []

    def fake_intro(title):
        calls.append(title)
        return {"title": title, "extract": "x" * 300, "url": f"https://en.wikipedia.org/wiki/{title}"}

    monkeypatch.setattr(sources, "wiki_intro", fake_intro)
    topic, news = sources.pick_fact(CFG, used_titles={"Git"}, rng=random.Random(1))
    assert calls == ["UTF-8"] and topic["wiki_subject"] == "UTF-8" and news[0]["source"] == "wikipedia.org"


def test_blocked_sites_filtered(monkeypatch):
    hits = [{"objectID": "9", "title": "X", "url": "https://www.spam.io/a", "points": 500, "created_at_i": 1762600000}]
    monkeypatch.setattr(sources, "get_json", lambda url, **kw: {"hits": hits})
    assert sources.hn_candidates(CFG, set(), now=1762700000, blocked_sites={"spam.io"}) == []


def test_blocked_fact_never_recycled(monkeypatch):
    monkeypatch.setattr(sources, "wiki_intro", lambda t: {"title": t, "extract": "x" * 300, "url": "u"})
    topic, _ = sources.pick_fact(CFG, used_titles={"Git", "UTF-8"}, rng=random.Random(1), blocked={"Git"})
    assert topic["wiki_subject"] == "UTF-8"  # all used -> recycle, but never the blocked one
