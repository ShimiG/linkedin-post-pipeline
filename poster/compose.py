"""Prompt building + the generate -> guardrail -> retry loop, shared by drafting and /rewrite."""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from . import examples as examples_mod
from . import guardrail, llm
from .config import prompt_text, system_prompt, today

NEWS_SNIPPET_CHARS = 1500
EMPTY_NOTES = {"", "none", "n/a", "na", "no warnings", "nothing", "-"}


@dataclass
class Draft:
    post_text: str
    hook: str = ""
    facts_used: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    model: str = ""
    provider: str = ""
    attempt: int = 1
    hashtags: int = 0
    post_text_he: str = ""        # translation, published first; empty when bilingual is off or translation failed
    translation_model: str = ""

    @property
    def words(self):
        return len(self.post_text.split())


RLM = "\u200f"  # right-to-left mark: invisible, makes a line that opens with an English word read right to left
HEBREW_LETTER = re.compile(r"[\u0590-\u05ff]")
LATIN_END = re.compile(r"(?<=[A-Za-z0-9)\]])([.?!:;,]+)\s*$")
LANGS = {"hebrew": "he", "he": "he", "עברית": "he", "english": "en", "en": "en", "אנגלית": "en", "both": "both", "combined": "both"}
LANG_NAME = {"he": "Hebrew", "en": "English", "both": "Hebrew and English"}


def _first_strong(line):
    for ch in line:
        d = unicodedata.bidirectional(ch)
        if d in ("R", "AL"):
            return "R"
        if d == "L":
            return "L"
    return ""


def rtl_marks(text):
    """LinkedIn sets each line's direction from its first letter. A Hebrew line that opens with an English word
    (\"Claude ...\") would be shown left to right, and a period after a closing English word can jump to the wrong side.
    An invisible right-to-left mark at the start (and before that period) keeps such lines right to left.
    Lines without Hebrew letters (the "*English below" line, hashtags) are left alone."""
    out = []
    for line in (text or "").split("\n"):
        if HEBREW_LETTER.search(line):
            if _first_strong(line) == "L":
                line = RLM + line
            line = LATIN_END.sub(lambda m: RLM + m.group(1), line)
        out.append(line)
    return "\n".join(out)


def separate(cfg):
    """True when each language is published as its own post (/approve hebrew, /approve english)."""
    return str((cfg.get("bilingual") or {}).get("publish") or "combined").strip().lower() == "separate"


def without_marker(cfg, he):
    """The Hebrew part on its own: the "*English below" line removed (drafts made for one combined post have it)."""
    marker = str((cfg.get("bilingual") or {}).get("english_marker") or "").strip()
    if not marker:
        return he
    lines = [l for l in (he or "").split("\n") if l.strip() != marker]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def version(cfg, he, en, lang):
    """The text published for `lang`: "he" (Hebrew alone), "en" (English alone) or "both" (one post, Hebrew first)."""
    he, en = (he or "").strip(), (en or "").strip()
    if lang == "he":
        return rtl_marks(without_marker(cfg, he))
    if lang == "en":
        return en
    return combine(cfg, he, en)


def combine(cfg, he, en):
    """One post with both versions: translation, separator, English. English only when there is no translation."""
    he, en = rtl_marks((he or "").strip()), (en or "").strip()
    if not he:
        return en
    if not en:
        return he
    sep = (cfg.get("bilingual") or {}).get("separator") or ""
    return f"{he}\n\n{sep}\n\n{en}" if sep else f"{he}\n\n{en}"


def bilingual(cfg):
    b = cfg.get("bilingual") or {}
    return bool(b.get("enabled")), str(b.get("language") or "Hebrew")


def notes_of(out):
    return [str(n) for n in out.get("notes") or [] if str(n).strip()]


def hebrew_part(cfg, headline, body):
    """The Hebrew version: the headline, then (only when both go out as one post) the "*English below" line,
    a blank line, then the Hebrew text."""
    marker = "" if separate(cfg) else str((cfg.get("bilingual") or {}).get("english_marker") or "").strip()
    top = f"{headline}\n{marker}" if marker else headline
    return f"{top}\n\n{body}"


def translate(cfg, english, day=None):
    """English post -> Hebrew, checked. Returns (hebrew, model, notes). Raises RuntimeError when no attempt passes.
    `day` is the day the post is written for (its date numbers are allowed); today when not given."""
    L = cfg["llm"]
    day = day or today(cfg)
    _, language = bilingual(cfg)
    s = prompt_text("translate-prompt.md")
    system = s.split("<!-- TRANSLATE PROMPT START -->", 1)[1].split("<!-- TRANSLATE PROMPT END -->", 1)[0].strip()
    system = system.replace("Hebrew", language)
    headline_on = bool((cfg.get("bilingual") or {}).get("headline", True))
    base = "Translate this LinkedIn post:\n\n" + english
    if not headline_on:
        system += "\n\nheadline_he is not used: return an empty string for it."
    providers = [L["primary"], L["fallback"]]
    provider, note, last = providers[0], "", "no attempt made"
    for attempt in range(1, L["max_attempts"] + 1):
        try:
            out, model = llm.generate(cfg, provider, system, base + note, L["retry_temperature"], schema=llm.TRANSLATION_SCHEMA)
        except llm.LLMError as e:
            last = f"llm_error: {e}"
            print(f"translation attempt {attempt}: {last}")
            provider = providers[1] if provider == providers[0] else providers[0]
            note = ""
            continue
        res = guardrail.check_translation(out.get("post_text_he", ""), english, today=day)
        if res.ok and headline_on:
            hres = guardrail.check_headline(out.get("headline_he", ""), english, today=day)
            if hres.ok:
                return hebrew_part(cfg, hres.post_text, res.post_text), model, notes_of(out) + guardrail.formal_words(res.post_text)
            res = hres
        elif res.ok:
            return res.post_text, model, notes_of(out) + guardrail.formal_words(res.post_text)
        last = res.message
        print(f"translation attempt {attempt}: {res.message}")
        note = guardrail.translation_retry_note(res)
    raise RuntimeError(f"translation failed after {L['max_attempts']} attempts; last: {last}")


def add_translation(cfg, draft, day=None):
    """Fill draft.post_text_he. A failed translation never loses the English draft: it becomes a review note."""
    on, language = bilingual(cfg)
    if not on:
        return draft
    try:
        draft.post_text_he, draft.translation_model, notes = translate(cfg, draft.post_text, day)
        draft.warnings += [f"Translation: {n}" for n in notes if n.strip().lower().strip(".") not in EMPTY_NOTES]
    except RuntimeError as e:
        draft.warnings.append(f"{language} translation failed ({e}). Only the English text will be posted; comment /rewrite to try again.")
        return draft
    limit = int((cfg.get("bilingual") or {}).get("max_total_chars") or 3000)
    total = len(version(cfg, draft.post_text_he, draft.post_text, "he" if separate(cfg) else "both"))
    if total > limit:
        what = "The Hebrew post" if separate(cfg) else "The combined post"
        draft.warnings.append(f"{what} is {total} characters, over LinkedIn's {limit}. Shorten it (or /rewrite shorter) before approving.")
    return draft


def post_day(cfg, topic):
    """The day the post is written for: an "On this day" draft's date (a later posting day), else today."""
    if topic.get("kind") == "onthisday" and topic.get("otd_date"):
        try:
            return date.fromisoformat(topic["otd_date"])
        except ValueError:
            pass
    return today(cfg)


def payload(cfg, author, topic, news_items):
    news = [{k: (str(v)[:NEWS_SNIPPET_CHARS] if k == "snippet" else v) for k, v in n.items() if k != "url"} for n in news_items]
    return {"author": author, "topic": {"title": topic["title"], "category": topic["category"], "angle": topic.get("angle", "")},
            "news": news, "has_news": bool(news), "today": post_day(cfg, topic).isoformat()}


def rewrite_message(instruction, current, data):
    tpl = prompt_text("rewrite-prompt.md").split("<!-- REWRITE PROMPT START -->", 1)[1].split("<!-- REWRITE PROMPT END -->", 1)[0].strip()
    return (tpl.replace("{instruction}", instruction).replace("{draft}", current)
               .replace("{input}", json.dumps(data, ensure_ascii=False, indent=2)))


def examples_block(texts):
    """Style references go in the message, not in the input JSON, so they are never treated as fact sources."""
    if not texts:
        return ""
    joined = "\n\n---\n\n".join(texts)
    return ("\n\nSTYLE EXAMPLES: posts the author likes. Learn their tone, rhythm, length, hook and how they close. "
            "Do not reuse their sentences or phrases, and take no facts, numbers or names from them.\n\n---\n\n" + joined + "\n\n---")


def write(cfg, author, topic, news_items, *, instruction=None, current=None, examples=None):
    """Generate until the guardrail passes or attempts run out. Returns Draft, or raises RuntimeError with the last reason."""
    L, P = cfg["llm"], cfg["post"]
    system = system_prompt(cfg)
    data = payload(cfg, author, topic, news_items)
    ex = examples_mod.load(cfg) if examples is None else examples
    base = rewrite_message(instruction, current, data) if instruction else "Write the post for this input:\n" + json.dumps(data, ensure_ascii=False, indent=2)
    base += examples_block(ex)
    providers = [L["primary"], L["fallback"]]
    provider, note, last = providers[0], "", "no attempt made"
    for attempt in range(1, L["max_attempts"] + 1):
        temp = L["temperature"] if attempt == 1 else L["retry_temperature"]
        try:
            out, model = llm.generate(cfg, provider, system, base + note, temp)
        except llm.LLMError as e:
            last = f"llm_error: {e}"
            print(f"attempt {attempt}: {last}")
            provider = providers[1] if provider == providers[0] else providers[0]  # fail over
            note = ""
            continue
        res = guardrail.check(out.get("post_text", ""), author=author, topic=topic, news_items=news_items, today=post_day(cfg, topic),
                              max_chars=P["max_chars"], max_hashtags=P["max_hashtags"], signature=P.get("signature") or "",
                              hook_max=int(P.get("hook_max_chars") or 0),
                              examples=ex, copy_ngram=int((cfg.get("examples") or {}).get("copy_ngram", 8)))
        if res.ok:
            warnings = [str(w) for w in out.get("warnings") or [] if str(w).strip().lower().strip(".") not in EMPTY_NOTES]
            d = Draft(res.post_text, str(out.get("hook", ""))[:200], [str(f) for f in out.get("facts_used") or []],
                      warnings, model, provider, attempt, res.hashtags)
            return add_translation(cfg, d, post_day(cfg, topic))
        last = res.message
        print(f"attempt {attempt}: guardrail {res.message}")
        note = guardrail.retry_note(res)
    raise RuntimeError(f"no draft passed the checks after {L['max_attempts']} attempts; last: {last}")
