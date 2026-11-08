"""Groq (OpenAI-compatible) with Gemini failover. Both have free tiers."""
import json
import os

from .http import HttpError, request

GEMINI_SCHEMA = {"type": "OBJECT", "required": ["post_text", "hook", "facts_used", "warnings"], "properties": {
    "post_text": {"type": "STRING"}, "hook": {"type": "STRING"},
    "facts_used": {"type": "ARRAY", "items": {"type": "STRING"}}, "warnings": {"type": "ARRAY", "items": {"type": "STRING"}}}}


class LLMError(Exception):
    pass


def _groq(cfg, system, user, temperature):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set")
    model = cfg["llm"]["groq_model"]
    body = {"model": model, "temperature": temperature, "max_tokens": 4096, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + "\n\nReturn the JSON object only."}]}
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    _, _, data = request("POST", cfg["llm"]["groq_url"], headers={"Authorization": f"Bearer {key}"}, body=body, timeout=90)
    return data["choices"][0]["message"]["content"], model


def _gemini(cfg, system, user, temperature):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY is not set")
    model = cfg["llm"]["gemini_model"]
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096,
                                 "responseMimeType": "application/json", "responseSchema": GEMINI_SCHEMA}}
    url = f"{cfg['llm']['gemini_base']}/{model}:generateContent"
    _, _, data = request("POST", url, headers={"x-goog-api-key": key}, body=body, timeout=90)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts), model


PROVIDERS = {"groq": _groq, "gemini": _gemini}


def generate(cfg, provider, system, user, temperature):
    """Returns (parsed_json, model). Raises LLMError on any provider or parse failure."""
    try:
        text, model = PROVIDERS[provider](cfg, system, user, temperature)
    except (HttpError, OSError, KeyError, IndexError) as e:
        raise LLMError(f"{provider}: {e}") from None
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError(f"{provider}: model did not return valid JSON") from None
    if not isinstance(out, dict):
        raise LLMError(f"{provider}: JSON was not an object")
    return out, model
