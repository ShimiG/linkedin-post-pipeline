"""Deterministic post checks (see prompts/guardrail-logic.md). No second LLM call."""
import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*(?:\s?(?:[KkMmBb](?![A-Za-z\d])|thousand|million|billion))?\+?", re.I)
URL_RE = re.compile(r"(https?://|www\.)\S+|\b[a-z0-9-]+\.(com|io|org|net|dev|ai|co)/\S*", re.I)
HASHTAG_RE = re.compile(r"(?:^|\s)#\w+")
OUTLETS = ["techcrunch", "the verge", "wired", "ars technica", "reuters", "bloomberg", "forbes", "bbc", "cnn",
           "the guardian", "new york times", "nytimes", "wall street journal", "wsj", "financial times",
           "business insider", "axios", "hacker news", "stack overflow", "github", "gartner", "mckinsey",
           "harvard business review", "hbr", "linkedin news", "the information", "venturebeat", "wikipedia"]
MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


@dataclass
class Result:
    ok: bool
    reason: str = "ok"
    offending: list = field(default_factory=list)
    message: str = ""
    post_text: str = ""
    hashtags: int = 0


def tidy(text):
    """Deterministic clean-up the model keeps getting wrong: em-dashes, and paragraphs glued with single newlines."""
    t = (text or "").replace("\r\n", "\n").strip()
    t = re.sub(r"\s*\u2014\s*", " - ", t)                   # em-dash -> spaced hyphen
    t = re.sub(r"[ \t]+\n", "\n", t)
    if "\n\n" not in t and "\n" in t:                         # one paragraph per line -> blank line between
        t = t.replace("\n", "\n\n")
    t = _hashtags_on_own_line(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _hashtags_on_own_line(t):
    """'...think? #AI #LLM' at the very end -> '...think?' + blank line + '#AI #LLM'."""
    head, _, last = t.rpartition("\n")
    words = last.split()
    n = 0
    while n < len(words) and re.fullmatch(r"#\w+", words[-1 - n]):
        n += 1
    if n == 0 or n == len(words):
        return t
    fixed = " ".join(words[:-n]) + "\n\n" + " ".join(words[-n:])
    return f"{head}\n{fixed}" if head else fixed


def norm(tok):
    t = re.sub(r"[#,+]", "", str(tok).strip())
    t = re.sub(r"[.,]$", "", t)
    m = re.match(r"^(\d+(?:\.\d+)?)\s?(k|m|b|thousand|million|billion)?$", t, re.I)
    if not m:
        return None
    return round(float(m.group(1)) * MULT.get((m.group(2) or "").lower(), 1), 3)


def allowed_numbers(sources_text, today):
    allowed = set()

    def add(v):
        n = norm(v)
        if n is not None:
            allowed.add(n)

    for v in (today.year, today.month, today.day):
        add(v)
    for m in TOKEN_RE.finditer(sources_text + " " + today.isoformat()):
        add(m.group(0))
        plain = re.sub(r"[,+]", "", m.group(0)).strip()
        lead = re.match(r"^\d+(?:\.\d+)?", plain)
        if lead:
            add(lead.group(0))
            if "." in lead.group(0):
                add(lead.group(0).split(".")[0])
    return allowed


def sources_text(author, topic, news_items):
    parts = [f"{k}: {v}" for k, v in (author or {}).items()]
    parts += [str(topic.get("title", "")), str(topic.get("angle", ""))]
    for n in news_items or []:
        parts += [str(v) for v in n.values()]
    return "\n".join(parts)


FIRST_PERSON = re.compile(r"\b(I|I'm|I’m|I've|I’ve|I'd|I’d|me|my|myself)\b")
STOPWORDS = set("that this with from have been were they their them when what which about into than then there these those would could should after before since over just also very more most only even much many some such your youre".split())


def content_words(text):
    return {w for w in re.findall(r"[a-z][a-z'’]{3,}", text.lower()) if w not in STOPWORDS}


def borrowed_first_person(post, author, news_items, threshold=0.6, min_words=4):
    """First-person sentences that mostly reuse the article's words: the article author's story told as the poster's own.
    Sentences backed by the poster's profile are allowed."""
    src = set().union(*(content_words(str(n.get("snippet", "")) + " " + str(n.get("title", ""))) for n in news_items)) if news_items else set()
    mine = content_words(" ".join(f"{k} {v}" for k, v in (author or {}).items()))
    hits = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", post):
        if not FIRST_PERSON.search(sent):
            continue
        words = content_words(sent) - mine
        if len(words) >= min_words and len(words & src) / len(words) >= threshold:
            hits.append(sent.strip()[:120])
    return hits


def _words(text):
    return re.findall(r"[a-z0-9'’]+", text.lower())


def copied_phrase(post, examples, n=8):
    """The first run of n consecutive words the post shares with any example, or ''."""
    words = _words(post)
    grams = {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    for ex in examples or []:
        ew = _words(ex)
        for i in range(len(ew) - n + 1):
            g = " ".join(ew[i:i + n])
            if g in grams:
                return g
    return ""


def check(post, *, author, topic, news_items, today, max_chars, max_hashtags, signature="", examples=(), copy_ngram=8):
    post = tidy(post)
    if not post:
        return Result(False, "llm_error", message="empty post_text")
    if signature and signature not in post:
        post = post.rstrip() + "\n\n" + signature
    fail = lambda reason, off, msg: Result(False, reason, off, msg, post)
    if len(post) > max_chars:
        return fail("too_long", [], f"too_long: {len(post)} > {max_chars}")
    body = post.replace(signature, "") if signature else post

    urls = [m.group(0) for m in URL_RE.finditer(body)]
    if urls:
        return fail("contains_url", urls, "contains_url: " + ", ".join(urls))

    src = sources_text(author, topic, news_items)
    allowed = allowed_numbers(src, today)
    offending = []
    for m in TOKEN_RE.finditer(body):
        tok = re.sub(r"[.,]$", "", m.group(0).strip())
        n = norm(tok)
        if n is not None and n not in allowed and tok not in offending:
            offending.append(tok)
    if offending:
        return fail("fact_check", offending, "fact_check: " + ", ".join(offending))

    hay, low = src.lower(), body.lower()
    bad = [o for o in OUTLETS if re.search(rf"(^|[^a-z]){re.escape(o)}([^a-z]|$)", low) and o not in hay]
    if bad:
        return fail("unverified_source", bad, "unverified_source: " + ", ".join(bad))

    copied = copied_phrase(body, examples, copy_ngram) if examples else ""
    if copied:
        return fail("copied_from_example", [copied], f"copied_from_example: \"{copied}\"")

    if topic.get("kind") == "news":
        borrowed = borrowed_first_person(body, author, news_items)
        if borrowed:
            return fail("borrowed_first_person", borrowed, "borrowed_first_person: " + " | ".join(borrowed))

    tags = HASHTAG_RE.findall(body)
    if len(tags) > max_hashtags:
        return fail("too_many_hashtags", [t.strip() for t in tags], f"too_many_hashtags: {len(tags)} > {max_hashtags}")
    return Result(True, post_text=post, hashtags=len(tags))


def retry_note(res):
    why = {"unverified_source": "named sources or companies that are not in the input",
           "too_many_hashtags": "used too many hashtags",
           "contains_url": "included a link (links go in the first comment, never in the post)",
           "fact_check": "used figures not present in the input",
           "borrowed_first_person": "retold the article author's own experience in the first person, as if it were yours",
           "copied_from_example": "copied wording from a style example"}.get(res.reason)
    if res.reason == "borrowed_first_person":
        return (f"\n\nYour previous draft {why}: {' | '.join(res.offending)}. Those are the article author's experiences. "
                "Attribute them in the third person (\"the author writes…\", \"a developer found…\") and keep \"I\" for facts from author only.")
    if why and res.offending:
        return f"\n\nYour previous draft {why}: {', '.join(res.offending)}. Do not use them. Rewrite the post using only facts from the input."
    if res.reason == "too_long":
        return f"\n\nYour previous draft was too long ({res.message}). Rewrite it in at most 110 words."
    return "\n\nYour previous answer was not valid JSON or failed. Return the JSON object only."


# --------------------------------------------------------------------------- translation
HEBREW_RE = re.compile(r"[֐-׿]")
LATIN_RE = re.compile(r"[A-Za-z]")


def check_translation(he, en, *, today):
    """The translation may not introduce numbers, links or hashtags, and must actually be in Hebrew script."""
    he = tidy(he)
    if not he:
        return Result(False, "llm_error", message="empty translation")
    fail = lambda reason, off, msg: Result(False, reason, off, msg, he)
    urls = [m.group(0) for m in URL_RE.finditer(he)]
    if urls:
        return fail("contains_url", urls, "contains_url: " + ", ".join(urls))
    tags = [t.strip() for t in HASHTAG_RE.findall(he)]
    if tags:
        return fail("hashtags_in_translation", tags, "hashtags_in_translation: " + ", ".join(tags))
    heb, lat = len(HEBREW_RE.findall(he)), len(LATIN_RE.findall(he))
    if heb < 0.5 * (heb + lat):
        return fail("not_hebrew", [he[:60]], f"not_hebrew: only {heb} of {heb + lat} letters are Hebrew")
    english_paras = []
    for para in he.split("\n\n"):  # every paragraph, so one untranslated sentence cannot hide among Hebrew ones
        heb, lat = len(HEBREW_RE.findall(para)), len(LATIN_RE.findall(para))
        if heb + lat >= 15 and heb < 0.5 * (heb + lat):
            english_paras.append(para[:60])
    if english_paras:
        return fail("not_hebrew", english_paras, "not_hebrew: paragraph(s) left untranslated: " + " | ".join(english_paras))
    allowed = allowed_numbers(en, today)
    offending = []
    for m in TOKEN_RE.finditer(he):
        tok = re.sub(r"[.,]$", "", m.group(0).strip())
        n = norm(tok)
        if n is not None and n not in allowed and tok not in offending:
            offending.append(tok)
    if offending:
        return fail("fact_check", offending, "fact_check: numbers not in the English post: " + ", ".join(offending))
    return Result(True, post_text=he)


def check_headline(headline, en, *, today, max_chars=90):
    """One short Hebrew line; same no-new-numbers / no-links / no-hashtags rules as the translation."""
    h = re.sub(r"\s+", " ", (headline or "")).strip().rstrip(".")
    if not h:
        return Result(False, "headline_missing", message="headline_missing: no headline returned")
    if len(h) > max_chars:
        return Result(False, "headline_too_long", [h], f"headline_too_long: {len(h)} > {max_chars}", h)
    res = check_translation(h, en, today=today)
    if not res.ok:
        return Result(False, "headline_" + res.reason, res.offending, "headline " + res.message, h)
    return Result(True, post_text=h)


def translation_retry_note(res):
    if res.reason.startswith("headline_"):
        return ("\n\nYour previous headline was rejected (" + res.message + "). Write headline_he as one short Hebrew line, "
                "3 to 8 words, about what the post says, with only numbers that appear in the English post and no hashtags or links.")
    if res.reason == "fact_check":
        return f"\n\nYour previous translation contained numbers that are not in the English post: {', '.join(res.offending)}. Keep exactly the numbers of the English post."
    if res.reason == "not_hebrew":
        return "\n\nYour previous translation left these parts in English: " + " | ".join(res.offending) + ". Translate every paragraph, including the closing question, into Hebrew."
    if res.reason in ("contains_url", "hashtags_in_translation"):
        return f"\n\nYour previous translation included {', '.join(res.offending)}. Leave out links and hashtags."
    return "\n\nYour previous answer was not valid JSON. Return the JSON object only."
