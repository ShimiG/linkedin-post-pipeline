from datetime import date

from poster import guardrail

TODAY = date(2026, 11, 9)
NEWS = [{"title": "Lab ships a 1.1 million token context model", "source": "example.com", "date": "2026-11-05",
         "snippet": "The model scores 87.5% on the benchmark and costs $3 per million tokens.", "discussion": "Hacker News, 412 points, 230 comments"}]
TOPIC = {"title": "New long-context model", "angle": ""}
AUTHOR = {"name": "Dev", "years_experience": "5+ years"}


def run(post, **kw):
    args = dict(author=AUTHOR, topic=TOPIC, news_items=NEWS, today=TODAY, max_chars=1300, max_hashtags=3)
    args.update(kw)
    return guardrail.check(post, **args)


def test_clean_post_passes():
    r = run("A model with a 1.1M token window landed this week.\n\nIt scored 87.5% and costs $3 per million tokens. After 5 years building agents, that matters.\n\n#AI #LLM")
    assert r.ok, r.message
    assert r.hashtags == 2


def test_invented_number_fails():
    r = run("It is 40% cheaper than last year.")
    assert not r.ok and r.reason == "fact_check" and r.offending == ["40"]


def test_million_word_equals_suffix():
    assert run("Context of 1.1 million tokens.").ok


def test_year_from_today_allowed_and_b2b_not_a_number():
    assert run("In 2026, B2B teams adopt agents.").ok


def test_hn_points_are_allowed_facts():
    assert run("412 people upvoted it on Hacker News.").ok


def test_unverified_outlet():
    r = run("TechCrunch called it a breakthrough.")
    assert not r.ok and r.reason == "unverified_source" and r.offending == ["techcrunch"]


def test_url_rejected():
    r = run("Read more at https://example.com/post")
    assert not r.ok and r.reason == "contains_url"


def test_bare_domain_path_rejected():
    assert run("Details: example.com/model-card").reason == "contains_url"


def test_too_many_hashtags():
    r = run("Short post.\n\n#a #b #c #d")
    assert not r.ok and r.reason == "too_many_hashtags"


def test_too_long():
    assert run("word " * 400).reason == "too_long"


def test_signature_appended_and_excluded_from_checks():
    r = run("Plain post.", signature="Built with 42 lines: see profile")
    assert r.ok and r.post_text.endswith("Built with 42 lines: see profile")


def test_empty():
    assert run("  ").reason == "llm_error"


def test_retry_note_names_offenders():
    r = run("It is 40% cheaper.")
    assert ": 40." in guardrail.retry_note(r)


def test_suffix_glued_to_digit_is_not_billion():
    assert guardrail.norm("2026") == 2026
    assert [m.group(0) for m in guardrail.TOKEN_RE.finditer("In 2026, B2B")] == ["2026,"]


EN_POST = "A model scored 48.1% after a 0.016% edit.\n\nWhat would you try?\n\n#AI #LLM"


def test_translation_ok_with_english_terms():
    he = "מודל קיבל 48.1% אחרי עריכה של 0.016% מהמשקלות של ה-LLM.\n\nמה הייתם מנסים?"
    assert guardrail.check_translation(he, EN_POST, today=TODAY).ok


def test_translation_new_number_fails():
    r = guardrail.check_translation("מודל קיבל 48.1% ושיפור פי 3 אחרי עריכה של 0.016%.", EN_POST, today=TODAY)
    assert not r.ok and r.reason == "fact_check" and r.offending == ["3"]


def test_translation_not_hebrew_fails():
    assert guardrail.check_translation("A model scored 48.1%.", EN_POST, today=TODAY).reason == "not_hebrew"


def test_translation_hashtags_and_links_fail():
    assert guardrail.check_translation("מודל חדש.\n\n#AI", EN_POST, today=TODAY).reason == "hashtags_in_translation"
    assert guardrail.check_translation("מודל חדש https://x.com/a", EN_POST, today=TODAY).reason == "contains_url"


def test_translation_retry_note():
    r = guardrail.check_translation("שיפור פי 3.", EN_POST, today=TODAY)
    assert "3" in guardrail.translation_retry_note(r)
