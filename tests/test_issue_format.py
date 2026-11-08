from poster import gh
from poster.compose import Draft, combine

CFG = {"bilingual": {"enabled": True, "separator": "• • •"}}
HE = "שורת פתיחה.\n\nגוף הפוסט."
EN = "Hook line.\n\nBody here.\n\n#AI"


def make_body(he=HE):
    topic = {"kind": "news", "title": "Model X", "category": "ai_news", "angle": "", "hn_id": "123",
             "source_url": "https://example.com/x", "discussion_url": "https://news.ycombinator.com/item?id=123"}
    news = [{"title": "Model X", "source": "example.com", "snippet": "text --> with arrow", "url": "https://example.com/x"}]
    d = Draft(EN, facts_used=["x <- news"], warnings=["thin source"], model="m", post_text_he=he, translation_model="t")
    return gh.render_draft(topic, news, d, ["token expires soon"])


def test_roundtrip_both_languages_and_meta():
    body = make_body()
    assert gh.parse_parts(body) == (HE, EN)
    meta = gh.parse_meta(body)
    assert meta["topic"]["hn_id"] == "123"
    assert meta["news"][0]["snippet"] == "text --&gt; with arrow"  # cannot close the HTML comment early


def test_hebrew_block_comes_first():
    body = make_body()
    assert body.index(gh.HE_START) < body.index(gh.EN_START)


def test_english_only_when_no_translation():
    body = make_body(he="")
    assert gh.HE_START not in body and gh.parse_parts(body) == ("", EN)


def test_human_edit_with_crlf_is_read():
    body = make_body().replace("גוף הפוסט.", "ערכתי.").replace("\n", "\r\n")
    assert gh.parse_parts(body) == ("שורת פתיחה.\n\nערכתי.", EN)


def test_replace_parts_keeps_rest_and_can_drop_translation():
    body = make_body()
    new = gh.replace_parts(body, "חדש.", "New text.")
    assert gh.parse_parts(new) == ("חדש.", "New text.")
    assert gh.parse_meta(new) == gh.parse_meta(body)
    assert "thin source" in new and "token expires soon" in new
    assert new.count(gh.SECTION_START) == 1 and new.count("**עברית**") == 1
    assert gh.parse_parts(gh.replace_parts(new, "", "Only English.")) == ("", "Only English.")


def test_older_single_block_issue_still_works():
    old = f"intro\n\n{gh.POST_START}\nOld post.\n{gh.POST_END}\n\nrest"
    assert gh.parse_parts(old) == ("", "Old post.")
    upgraded = gh.replace_parts(old, "עברית.", "English.")
    assert gh.parse_parts(upgraded) == ("עברית.", "English.") and upgraded.startswith("intro") and upgraded.endswith("rest")


def test_combine_order_and_separator():
    assert combine(CFG, HE, EN) == f"{HE}\n\n• • •\n\n{EN}"
    assert combine(CFG, "", EN) == EN
    assert combine({"bilingual": {}}, HE, EN) == f"{HE}\n\n{EN}"


def test_missing_markers():
    assert gh.parse_parts("no markers") == ("", "")
    assert gh.parse_meta("nothing") == {}
