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

    @property
    def words(self):
        return len(self.post_text.split())


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
            return Draft(res.post_text, str(out.get("hook", ""))[:200], [str(f) for f in out.get("facts_used") or []],
                         warnings, model, provider, attempt, res.hashtags)
        last = res.message
        print(f"attempt {attempt}: guardrail {res.message}")
        note = guardrail.retry_note(res)
    raise RuntimeError(f"no draft passed the checks after {L['max_attempts']} attempts; last: {last}")
