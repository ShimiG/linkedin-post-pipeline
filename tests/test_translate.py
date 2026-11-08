from poster import compose, llm

CFG = {"llm": {"primary": "groq", "fallback": "gemini", "max_attempts": 3, "temperature": 0.6, "retry_temperature": 0.2},
       "timezone": "UTC",
       "bilingual": {"enabled": True, "language": "Hebrew", "headline": True, "english_marker": "*English below", "separator": "• • •"}}
EN = "A model scored 48.1% after a tiny edit.\n\nWhat would you try?\n\n#AI"
BODY = "מודל קיבל 48.1% אחרי עריכה קטנה.\n\nמה הייתם מנסים?"


def fake(answers, seen):
    def gen(cfg, provider, system, user, temp, schema=None):
        seen.append(user)
        return answers[len(seen) - 1], "fake"
    return gen


def test_hebrew_part_layout(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "generate", fake([{"headline_he": "עריכה קטנה, קפיצה גדולה", "post_text_he": BODY, "notes": []}], seen))
    he, model, _ = compose.translate(CFG, EN)
    assert he == f"עריכה קטנה, קפיצה גדולה\n*English below\n\n{BODY}"
    assert compose.combine(CFG, he, EN).startswith("עריכה קטנה, קפיצה גדולה\n*English below\n\nמודל")


def test_bad_headline_is_retried_with_a_note(monkeypatch):
    seen = []
    answers = [{"headline_he": "Big jump", "post_text_he": BODY, "notes": []},
               {"headline_he": "קפיצה גדולה במודל קטן", "post_text_he": BODY, "notes": []}]
    monkeypatch.setattr(llm, "generate", fake(answers, seen))
    he, _, _ = compose.translate(CFG, EN)
    assert he.startswith("קפיצה גדולה במודל קטן\n*English below") and "headline was rejected" in seen[1]


def test_headline_off_means_plain_translation(monkeypatch):
    cfg = {**CFG, "bilingual": {**CFG["bilingual"], "headline": False}}
    monkeypatch.setattr(llm, "generate", fake([{"headline_he": "", "post_text_he": BODY, "notes": []}], []))
    assert compose.translate(cfg, EN)[0] == BODY
