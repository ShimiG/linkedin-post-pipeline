from datetime import date

from poster import linkedin
from poster.draft import author_for, kind_order
from poster.review import parse_command

CFG = {"rotation": ["news", "fact"], "general_post_profile_keys": ["name", "headline"]}


def test_parse_command():
    assert parse_command("/approve") == ("/approve", "")
    assert parse_command("/Rewrite  make it shorter\nthanks") == ("/rewrite", "make it shorter")
    assert parse_command("looks good") == (None, "")
    assert parse_command("") == (None, "")


def test_kind_rotation():
    assert kind_order(CFG, None, "auto") == ["news", "fact"]
    assert kind_order(CFG, "news", "auto") == ["fact", "news"]
    assert kind_order(CFG, "fact", "auto") == ["news", "fact"]
    assert kind_order(CFG, "news", "news") == ["news", "fact"]


def test_author_subset_for_general_posts():
    p = {"name": "A", "headline": "H", "secret_project": "S"}
    assert author_for(CFG, p, "news") == {"name": "A", "headline": "H"}
    assert author_for(CFG, p, "queued") == p


def test_little_text_escapes_and_hashtags():
    out = linkedin.little_text("Use (parens) and a_b @me.\n\n#AI #LLM")
    assert r"\(parens\)" in out and r"a\_b" in out and r"\@me" in out
    assert out.endswith("{hashtag|\\#|AI} {hashtag|\\#|LLM}")


def test_api_version_two_months_back():
    assert linkedin.api_version(date(2026, 11, 9)) == "202609"
    assert linkedin.api_version(date(2026, 1, 9)) == "202511"
