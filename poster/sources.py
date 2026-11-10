"""Free, keyless sources: Hacker News (Algolia search API) for AI news, Wikipedia for coding facts and "On this day" events."""
import html
import random
import re
import time
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser

from .http import HttpError, get_json, request

HN_SEARCH = "https://hn.algolia.com/api/v1/search"
WIKI_API = "https://en.wikipedia.org/w/api.php"
# Wikipedia's "On this day" feed: {selected, events, births, deaths, holidays}, each entry {text, year, pages:[summary]}.
ONTHISDAY_FEEDS = ("https://en.wikipedia.org/api/rest_v1/feed/onthisday/all/{mm:02d}/{dd:02d}",
                   "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{mm:02d}/{dd:02d}")
ONTHISDAY_SECTIONS = ("selected", "events", "births", "deaths")   # in order of preference on equal relevance
ARTICLE_MAX_CHARS = 2500


def host(url):
    m = re.match(r"^[a-z]+://([^/?#]+)", url or "", re.I)
    return m.group(1).lower().removeprefix("www.") if m else ""


# --------------------------------------------------------------------------- Hacker News
def hn_candidates(cfg, used_ids, now=None, blocked_sites=()):
    """Recent AI stories with an external link, most points first, skipping drafted stories and blocked websites."""
    n = cfg["news"]
    since = int((now or time.time()) - n["lookback_days"] * 86400)
    seen = {}
    for q in n["queries"]:
        params = {"query": q, "tags": "story", "hitsPerPage": 30,
                  "numericFilters": f"created_at_i>{since},points>={n['min_points']}"}
        try:
            data = get_json(HN_SEARCH + "?" + urllib.parse.urlencode(params), timeout=20)
        except (HttpError, OSError) as e:
            print(f"hn search {q!r} failed: {e}")
            continue
        for h in data.get("hits", []):
            sid = str(h.get("objectID"))
            if sid in used_ids or not h.get("url") or sid in seen:
                continue
            if host(h["url"]) in blocked_sites:
                continue
            if host(h["url"]) in ("github.com", "youtube.com", "twitter.com", "x.com"):
                continue  # repos and videos rarely give a readable article body
            seen[sid] = {
                "hn_id": sid, "title": h.get("title", ""), "url": h["url"], "source": host(h["url"]),
                "points": int(h.get("points") or 0), "comments": int(h.get("num_comments") or 0),
                "date": datetime.fromtimestamp(int(h.get("created_at_i") or 0), timezone.utc).date().isoformat(),
                "discussion_url": f"https://news.ycombinator.com/item?id={sid}",
            }
    return sorted(seen.values(), key=lambda s: s["points"], reverse=True)[: n["max_candidates"]]


class _TextExtractor(HTMLParser):
    """Collects <p> text and the meta description; skips script/style/nav/footer."""
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras, self.description, self._skip, self._in_p, self._buf = [], "", 0, False, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "meta" and (a.get("name") == "description" or a.get("property") == "og:description") and not self.description:
            self.description = a.get("content") or ""
        elif tag == "p" and not self._skip:
            self._in_p, self._buf = True, []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "p" and self._in_p:
            self._in_p = False
            t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(t) >= 60:
                self.paras.append(t)

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


def article_text(url):
    """Best-effort readable text of an article. Returns '' when the page is blocked or not HTML."""
    try:
        _, headers, body = request("GET", url, headers={"Accept": "text/html"}, timeout=15, raw=True)
    except (HttpError, OSError) as e:
        print(f"article fetch failed {url}: {e}")
        return ""
    if "html" not in headers.get("content-type", "html"):
        return ""
    p = _TextExtractor()
    try:
        p.feed(body)
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    desc = html.unescape(p.description).strip()
    paras = [t for t in p.paras if not (desc and (t.startswith(desc[:80]) or desc.startswith(t[:80])))]
    text = " ".join(([desc] if desc else []) + paras).strip()
    return text[:ARTICLE_MAX_CHARS]


def pick_news(cfg, used_ids, blocked_sites=()):
    """First candidate whose article has enough text to ground a post. Returns (topic, news_items) or None."""
    for c in hn_candidates(cfg, used_ids, blocked_sites=blocked_sites):
        text = article_text(c["url"])
        if len(text) < 300:
            print(f"skip {c['url']}: only {len(text)} chars readable")
            continue
        item = {"title": c["title"], "source": c["source"], "date": c["date"], "snippet": text,
                "url": c["url"], "discussion": f"Hacker News, {c['points']} points, {c['comments']} comments"}
        topic = {"kind": "news", "title": c["title"], "category": "ai_news", "angle": "", "hn_id": c["hn_id"],
                 "source_url": c["url"], "discussion_url": c["discussion_url"]}
        return topic, [item]
    return None


# --------------------------------------------------------------------------- Wikipedia
def wiki_intro(title):
    # Whole article as plain text (not just the intro, which is a single line for some subjects), trimmed below.
    params = {"action": "query", "prop": "extracts|info", "explaintext": 1, "redirects": 1,
              "inprop": "url", "format": "json", "formatversion": 2, "titles": title}
    data = get_json(WIKI_API + "?" + urllib.parse.urlencode(params), timeout=20)
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    pg = pages[0]
    extract = pg.get("extract", "")
    extract = extract.split("\n== See also ==", 1)[0].split("\n== References ==", 1)[0]
    extract = re.sub(r"\n=+ *(.+?) *=+\n", r"\n\1: ", extract)       # "== History ==" -> "History: "
    extract = re.sub(r"\n{2,}", "\n", extract).strip()
    return {"title": pg.get("title", title), "extract": extract[:ARTICLE_MAX_CHARS], "url": pg.get("fullurl", "")}


def pick_fact(cfg, used_titles, rng=random, blocked=()):
    allowed = [s for s in cfg["fact_subjects"] if s not in blocked]
    subjects = [s for s in allowed if s not in used_titles]
    if not subjects:
        print("every fact subject has been used; recycling the list (blocked ones stay out)")
        subjects = list(allowed)
    rng.shuffle(subjects)
    for s in subjects[:5]:
        try:
            w = wiki_intro(s)
        except (HttpError, OSError) as e:
            print(f"wikipedia {s!r} failed: {e}")
            continue
        if not w or len(w["extract"]) < 200:
            continue
        item = {"title": w["title"], "source": "wikipedia.org", "date": "", "snippet": w["extract"], "url": w["url"]}
        topic = {"kind": "fact", "title": w["title"], "category": "coding_fact", "angle": "", "wiki_subject": s,
                 "source_url": w["url"]}
        return topic, [item]
    return None


# --------------------------------------------------------------------------- Wikipedia "On this day"
def keyword_hits(keywords, text):
    """Keywords found in text as whole words. ALL-CAPS keywords (AI, IBM) must match exactly; the rest ignore case
    and also match their plural ("computer" finds "computers")."""
    hits = set()
    for kw in keywords:
        kw = str(kw).strip()
        if not kw:
            continue
        exact = kw.isupper()
        pattern = rf"(?<![A-Za-z0-9]){re.escape(kw)}{'' if exact else 's?'}(?![A-Za-z0-9])"
        if re.search(pattern, text or "", 0 if exact else re.I):
            hits.add(kw.lower())
    return hits


def _page(pg):
    title = pg.get("normalizedtitle") or (pg.get("titles") or {}).get("normalized") or str(pg.get("title", "")).replace("_", " ")
    url = ((pg.get("content_urls") or {}).get("desktop") or {}).get("page") or ""
    if not url and title:
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
    return {"title": title, "description": str(pg.get("description") or ""), "extract": str(pg.get("extract") or "").strip(), "url": url}


def onthisday_feed(day):
    """Wikipedia's lists for this month and day; {} when neither host answers."""
    for tpl in ONTHISDAY_FEEDS:
        try:
            data = get_json(tpl.format(mm=day.month, dd=day.day), timeout=20)
        except (HttpError, OSError) as e:
            print(f"on-this-day feed failed: {e}")
            continue
        if isinstance(data, dict):
            return data
    return {}


def onthisday_candidates(cfg, day, used_ids=(), feed=None):
    """Events, births and deaths on this month/day that match on_this_day.keywords, most relevant first.

    Relevance: keywords in the event line count double, keywords in the linked articles' titles and short
    descriptions count once. The article the keywords point to is the main one: the source link and most of the background.
    """
    o = cfg["on_this_day"]
    feed = onthisday_feed(day) if feed is None else feed
    sections = [s for s in (o.get("sections") or ONTHISDAY_SECTIONS) if s in ONTHISDAY_SECTIONS]
    out, seen = [], set()
    for rank, section in enumerate(sections):
        for e in feed.get(section) or []:
            text, year = re.sub(r"\s+", " ", str(e.get("text") or "")).strip(), e.get("year")
            if not text or not isinstance(year, int) or not 0 < year < day.year:
                continue
            years_ago = day.year - year
            if years_ago < int(o.get("min_years_ago", 1)):
                continue
            pages = [p for p in map(_page, e.get("pages") or []) if p["title"]]
            in_text = keyword_hits(o["keywords"], text)
            per_page = [keyword_hits(o["keywords"], f"{p['title']}. {p['description']}") for p in pages]
            score = 2 * len(in_text) + len(set().union(*per_page) - in_text)
            if score < int(o.get("min_score", 1)):
                continue
            # A birth or death is about its one person; an event's main article is the one the keywords point at.
            main = pages[0] if pages else None
            if pages and section in ("selected", "events"):
                main = pages[max(range(len(pages)), key=lambda i: (len(per_page[i]), -i))]
            oid = f"{day.month:02d}-{day.day:02d}/{year}/{main['title'] if main else text[:60]}"
            if oid in used_ids or oid in seen:
                continue  # "selected" repeats entries of "events"
            seen.add(oid)
            what = {"births": f"{text} was born", "deaths": f"{text} died"}.get(section, text)
            out.append({"otd_id": oid, "section": section, "year": year, "years_ago": years_ago, "text": what,
                        "page": main, "others": [p for p in pages if p is not main], "score": score, "rank": rank})
    out.sort(key=lambda c: (-c["score"], c["rank"], -c["year"]))
    return out[: int(o.get("max_candidates", 5))]


def _clip(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def onthisday_background(c):
    """(text, url): the main article at length, then the opening of up to two other linked articles for context."""
    main = c["page"]
    if not main:
        return "", ""
    try:
        w = wiki_intro(main["title"])
    except (HttpError, OSError) as e:
        print(f"wikipedia {main['title']!r} failed: {e}")
        w = None
    parts = [(main["title"], _clip((w or {}).get("extract") or main["extract"], 900))]
    parts += [(p["title"], _clip(p["extract"], 250)) for p in c["others"][:2]]
    text = "\n".join(f"{title}: {body}" for title, body in parts if body)
    return text, (w or {}).get("url") or main["url"]


def pick_onthisday(cfg, used_ids, day):
    """The most relevant event on this date with enough background text. Returns (topic, news_items) or None."""
    for c in onthisday_candidates(cfg, day, used_ids):
        background, url = onthisday_background(c)
        snippet = f"On {day.day} {day.strftime('%B')} {c['year']}: {c['text']}"
        if background:
            snippet += f"\n\nBackground from Wikipedia.\n{background}"
        if len(snippet) < 200:
            print(f"skip on-this-day {c['otd_id']}: only {len(snippet)} chars of source text")
            continue
        event_date = f"{c['year']:04d}-{day.month:02d}-{day.day:02d}"
        title = f"On this day in {c['year']}: {c['text']}"
        item = {"title": title, "source": "wikipedia.org", "date": event_date, "snippet": snippet[:ARTICLE_MAX_CHARS],
                "years_ago": c["years_ago"], "url": url}
        topic = {"kind": "onthisday", "title": title if len(title) <= 140 else title[:137].rstrip() + "...",
                 "category": "on_this_day", "angle": "", "otd_id": c["otd_id"], "otd_date": day.isoformat(),
                 "source_url": url}
        return topic, [item]
    return None
