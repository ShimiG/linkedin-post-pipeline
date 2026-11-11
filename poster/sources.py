"""Free sources: Hacker News (Algolia search API) for AI news, Wikipedia for coding facts and "On this day" events,
and your own GitHub repositories for project posts (manual runs only)."""
import html
import os
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


# --------------------------------------------------------------------------- your GitHub projects (manual runs only)
GITHUB_API = "https://api.github.com"
README_CHUNK = 1400       # each source item is cut at compose.NEWS_SNIPPET_CHARS (1500)
README_PARTS = 3
# README sections that are about running the code, not about what it is or why: left out of the source text.
SKIP_SECTION = re.compile(r"^(install\w*|set ?up\b.*|quick ?start|getting started|development|contributing|license|licence|"
                          r"run it locally|running locally|requirements|prerequisites|build\w*|test\w*|changelog|"
                          r"acknowledg\w*|credits|troubleshooting|faq|configuration|environment variables|scripts|commands)$", re.I)


class ProjectError(Exception):
    """A project post could not be sourced; the message says why (shown in the Actions log)."""


def _github(path, raw=False):
    """GET from the GitHub API. PROJECTS_TOKEN (read access to your private repos) wins over the workflow token."""
    from . import gh  # local import: gh imports nothing from here, but keep sources usable without it
    token = os.environ.get("PROJECTS_TOKEN", "").strip() or gh._token()
    headers = {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28",
               "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}
    return request("GET", GITHUB_API + path, headers=headers, raw=raw)[2]


def readme_text(md):
    """README markdown -> plain text about what the project is: no code, images, badges, links or setup sections."""
    md = re.sub(r"<!--.*?-->", "", md or "", flags=re.S)
    md = re.sub(r"^(```|~~~).*?^\1[^\n]*$", "", md, flags=re.S | re.M)
    out, skipping = [], False
    for line in md.splitlines():
        h = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            title = re.sub(r"[*_`]", "", h.group(2)).strip()
            skipping = len(h.group(1)) > 1 and bool(SKIP_SECTION.match(title))
            if not skipping and len(h.group(1)) > 1:
                out.append(f"\n{title}:")
            continue
        if skipping:
            continue
        if re.match(r"^\s*\|?\s*:?-{3,}", line):  # table separator row
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)            # images and badges
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)        # links -> their text
        line = re.sub(r"<[^>]+>", "", line)                              # html tags
        line = re.sub(r"https?://\S+", "", line)                       # bare urls
        line = re.sub(r"[*_`]{1,3}", "", line)
        if line.strip().startswith("|"):
            line = " - ".join(c.strip() for c in line.strip().strip("|").split("|") if c.strip())
        line = re.sub(r"^\s*[-*+]\s+", "- ", line).rstrip()
        out.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    text = re.sub(r"(?<![:\n])\n(?!\n|- )", " ", text)  # re-join hard-wrapped lines inside a paragraph
    return re.sub(r"[ \t]{2,}", " ", text)


def readme_title(md, fallback):
    m = re.search(r"^\s{0,3}#\s+(.+?)\s*#*\s*$", md or "", re.M)
    return re.sub(r"[*_`]|!\[[^\]]*\]\([^)]*\)", "", m.group(1)).strip() if m else fallback


def chunks(text, size=README_CHUNK, limit=README_PARTS):
    """Whole paragraphs, up to `size` characters per chunk, at most `limit` chunks."""
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + len(para) + 2 > size:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}".strip() if len(para) <= size else (cur + "\n\n" + para[:size]).strip()[:size]
    if cur:
        parts.append(cur)
    return parts[:limit]


def project_topic(full_name):
    """(topic, news_items) for one repository. Raises HttpError when it cannot be read, ProjectError when it says nothing."""
    meta = _github(f"/repos/{full_name}")
    try:
        langs = list(_github(f"/repos/{full_name}/languages"))[:4]
    except HttpError:
        langs = []
    try:
        md = _github(f"/repos/{full_name}/readme", raw=True)
    except HttpError as e:
        if e.status != 404:
            raise
        md = ""
    md = md if isinstance(md, str) else ""
    text, desc = readme_text(md), (meta.get("description") or "").strip()
    if len(text) < 150 and not desc:
        raise ProjectError(f"{full_name} has no README text or description to write from")
    name = meta.get("name") or full_name.split("/")[-1]
    title = readme_title(md, name)
    pushed = (meta.get("pushed_at") or "")[:10]
    facts = f"My own project: the GitHub repository {name}" + (f", titled \"{title}\"" if title != name else "") + "."
    if desc:
        facts += f" Description: {desc}."
    if langs:
        facts += f" Languages: {', '.join(langs)}."
    facts += " Public repository." if not meta.get("private") else " Private repository (not linked in the post)."
    items = [{"title": f"{title}: repository facts", "source": "my GitHub repository", "date": pushed, "snippet": facts}]
    for i, part in enumerate(chunks(text), 1):
        items.append({"title": f"{title}: README, part {i}", "source": "my GitHub repository", "date": pushed, "snippet": part})
    topic = {"kind": "project", "title": f"My project: {title}", "category": "project",
             "angle": "My own project, described in my repository's README below." + (f" {desc}" if desc else ""),
             "repo": meta.get("full_name") or full_name,
             "source_url": None if meta.get("private") else meta.get("html_url")}
    return topic, items


def pick_project(cfg, owner, wanted="", recent=(), blocked=(), rng=random):
    """A project post. `wanted` is a repo name (or owner/name) chosen by hand; empty = a random one from
    config.yml -> projects.repos, preferring repos without a recent draft and never a blocked one. Raises ProjectError."""
    wanted = (wanted or "").strip().strip("/")
    if wanted:
        full = wanted if "/" in wanted else f"{owner}/{wanted}"
        try:
            return project_topic(full)
        except HttpError as e:
            hint = (" If it is private, add a PROJECTS_TOKEN secret with read access to it (see README)."
                    if e.status in (401, 403, 404) else "")
            raise ProjectError(f"can't read {full} (HTTP {e.status}).{hint}") from None
    names = [str(n).strip() for n in (cfg.get("projects") or {}).get("repos") or [] if str(n).strip()]
    pool = [n for n in names if n not in blocked and f"{owner}/{n}" not in blocked]
    if not pool:
        raise ProjectError("no repos to choose from: list them in config.yml -> projects.repos (blocked ones are skipped)")
    fresh = [n for n in pool if n not in recent and f"{owner}/{n}" not in recent]
    order = list(fresh or pool)
    rng.shuffle(order)
    errors = []
    for n in order:
        full = n if "/" in n else f"{owner}/{n}"
        try:
            return project_topic(full)
        except (HttpError, ProjectError) as e:
            errors.append(f"{full}: {getattr(e, 'status', '') or e}")
    raise ProjectError("none of the listed repos could be read (" + "; ".join(errors) + "). Private ones need the PROJECTS_TOKEN secret.")
