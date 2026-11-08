"""Prompt building + the generate -> guardrail -> retry loop, shared by drafting and /rewrite."""
import json
from dataclasses import dataclass, field

from . import guardrail, llm
from .config import prompt_text, system_prompt, today

NEWS_SNIPPET_CHARS = 1500


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


def combine(cfg, he, en):
    """The text that is actually published: translation, separator, English. English only when there is no translation."""
    he, en = (he or "").strip(), (en or "").strip()
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
    """What opens the post: the headline, the "*English below" line, a blank line, then the Hebrew text."""
    marker = str((cfg.get("bilingual") or {}).get("english_marker") or "").strip()
    top = f"{headline}\n{marker}" if marker else headline
    return f"{top}\n\n{body}"


def translate(cfg, english):
    """English post -> Hebrew, checked. Returns (hebrew, model, notes). Raises RuntimeError when no attempt passes."""
    L = cfg["llm"]
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
        res = guardrail.check_translation(out.get("post_text_he", ""), english, today=today(cfg))
        if res.ok and headline_on:
            hres = guardrail.check_headline(out.get("headline_he", ""), english, today=today(cfg))
            if hres.ok:
                return hebrew_part(cfg, hres.post_text, res.post_text), model, notes_of(out)
            res = hres
        elif res.ok:
            return res.post_text, model, notes_of(out)
        last = res.message
        print(f"translation attempt {attempt}: {res.message}")
        note = guardrail.translation_retry_note(res)
    raise RuntimeError(f"translation failed after {L['max_attempts']} attempts; last: {last}")


def add_translation(cfg, draft):
    """Fill draft.post_text_he. A failed translation never loses the English draft: it becomes a review note."""
    on, language = bilingual(cfg)
    if not on:
        return draft
    try:
        draft.post_text_he, draft.translation_model, notes = translate(cfg, draft.post_text)
        draft.warnings += [f"Translation: {n}" for n in notes]
    except RuntimeError as e:
        draft.warnings.append(f"{language} translation failed ({e}). Only the English text will be posted; comment /rewrite to try again.")
        return draft
    total = len(combine(cfg, draft.post_text_he, draft.post_text))
    limit = int((cfg.get("bilingual") or {}).get("max_total_chars") or 3000)
    if total > limit:
        draft.warnings.append(f"The combined post is {total} characters, over LinkedIn's {limit}. Shorten it (or /rewrite shorter) before approving.")
    return draft


def payload(cfg, author, topic, news_items):
    news = [{k: (str(v)[:NEWS_SNIPPET_CHARS] if k == "snippet" else v) for k, v in n.items() if k != "url"} for n in news_items]
    return {"author": author, "topic": {"title": topic["title"], "category": topic["category"], "angle": topic.get("angle", "")},
            "news": news, "has_news": bool(news), "today": today(cfg).isoformat()}


def rewrite_message(instruction, current, data):
    tpl = prompt_text("rewrite-prompt.md").split("<!-- REWRITE PROMPT START -->", 1)[1].split("<!-- REWRITE PROMPT END -->", 1)[0].strip()
    return (tpl.replace("{instruction}", instruction).replace("{draft}", current)
               .replace("{input}", json.dumps(data, ensure_ascii=False, indent=2)))


def write(cfg, author, topic, news_items, *, instruction=None, current=None):
    """Generate until the guardrail passes or attempts run out. Returns Draft, or raises RuntimeError with the last reason."""
    L, P = cfg["llm"], cfg["post"]
    system = system_prompt(cfg)
    data = payload(cfg, author, topic, news_items)
    base = rewrite_message(instruction, current, data) if instruction else "Write the post for this input:\n" + json.dumps(data, ensure_ascii=False, indent=2)
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
        res = guardrail.check(out.get("post_text", ""), author=author, topic=topic, news_items=news_items, today=today(cfg),
                              max_chars=P["max_chars"], max_hashtags=P["max_hashtags"], signature=P.get("signature") or "")
        if res.ok:
            warnings = [str(w) for w in out.get("warnings") or [] if str(w).strip()]
            d = Draft(res.post_text, str(out.get("hook", ""))[:200], [str(f) for f in out.get("facts_used") or []],
                      warnings, model, provider, attempt, res.hashtags)
            return add_translation(cfg, d)
        last = res.message
        print(f"attempt {attempt}: guardrail {res.message}")
        note = guardrail.retry_note(res)
    raise RuntimeError(f"no draft passed the checks after {L['max_attempts']} attempts; last: {last}")
