from poster import gh
from poster.compose import Draft


def make_body():
    topic = {"kind": "news", "title": "Model X", "category": "ai_news", "angle": "", "hn_id": "123",
             "source_url": "https://example.com/x", "discussion_url": "https://news.ycombinator.com/item?id=123"}
    news = [{"title": "Model X", "source": "example.com", "snippet": "text --> with arrow", "url": "https://example.com/x"}]
    d = Draft("Hook line.\n\nBody here.", facts_used=["x <- news"], warnings=["thin source"], model="m")
    return gh.render_draft(topic, news, d, ["token expires soon"])


def test_roundtrip_post_and_meta():
    body = make_body()
    assert gh.parse_post(body) == "Hook line.\n\nBody here."
    meta = gh.parse_meta(body)
    assert meta["topic"]["hn_id"] == "123"
    assert meta["news"][0]["snippet"] == "text --&gt; with arrow"  # cannot close the HTML comment early


def test_human_edit_with_crlf_is_read():
    body = make_body().replace("Body here.", "Edited by me.").replace("\n", "\r\n")
    assert gh.parse_post(body) == "Hook line.\n\nEdited by me."


def test_replace_post_keeps_rest():
    body = make_body()
    new = gh.replace_post(body, "New text.")
    assert gh.parse_post(new) == "New text."
    assert gh.parse_meta(new) == gh.parse_meta(body)
    assert "thin source" in new and "token expires soon" in new


def test_missing_markers():
    assert gh.parse_post("no markers") == ""
    assert gh.parse_meta("nothing") == {}
