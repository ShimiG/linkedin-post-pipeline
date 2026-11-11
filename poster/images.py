"""The picture posted with each draft.

Most drafts get a branded card: the Hebrew headline and the English hook on the brand gradient, drawn with Pillow.
About one draft in `images.ai_every` gets an AI illustration of the topic instead (Cloudflare Workers AI, free tier),
but only when every safety layer agrees:

1. the post itself mentions nothing sensitive (politics, war, crime, death, sex, religion...: SENSITIVE below);
2. the model writing the image prompt judges the topic safe to illustrate, under the rules in prompts/image-prompt.md;
3. the prompt it wrote passes the same word check;
4. a fixed style line is always appended (no people, no text, no logos), so the image model gets it on every call;
5. the result is not blank (some models return a black image when their own filter trips).

If any layer says no, the draft gets a card and the review notes say why. You see the image on the draft issue
before anything is posted, and `/image card`, `/image ai` or `/image none` changes it.
"""
import base64
import io
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import date

from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont, ImageStat

from . import gh, llm
from .config import ROOT, prompt_text
from .http import HttpError, request

FONT = ROOT / "fonts" / "Heebo.ttf"
SIZE = 1080
PAD = 90
C1, C2 = (79, 70, 229), (13, 148, 136)  # the app logo's gradient: indigo -> teal
WHITE = (255, 255, 255)

LABELS = {  # kind -> (Hebrew label, English tag)
    "news": ("חדשות בינה מלאכותית", "AI news"),
    "fact": ("עובדה מעולם הקוד", "Coding fact"),
    "onthisday": ("היום בהיסטוריה", "On this day"),
    "queued": ("מהשטח", "Field notes"),
    "project": ("מהפרויקטים שלי", "My project"),
}

# Words that keep a post away from AI images. Matched as whole words, any case, in the post, its title and the image
# prompt. A false positive only means a card instead of an illustration, so this errs wide.
SENSITIVE_WORDS = [
    # politics and government
    r"politic\w*", r"elect(?:ion|ions|ed|oral)", r"vot(?:e|es|ed|ing|ers?)", r"president\w*", r"prime minister\w*",
    r"minister\w*", r"government\w*", r"parliament\w*", r"congress\w*", r"senat\w*", r"democrat\w*", r"republican\w*",
    r"propaganda", r"protest\w*", r"riot\w*", r"sanction\w*", r"flags?",
    # conflict and violence
    r"wars?", r"warfare", r"militar\w*", r"army", r"armies", r"soldiers?", r"troops?", r"weapon\w*", r"guns?", r"gunfire",
    r"rifles?", r"pistols?", r"bombs?", r"bombing\w*", r"explosi\w*", r"missiles?", r"terror\w*", r"\w*attack\w*", r"deepfakes?",
    r"kill\w*", r"murder\w*", r"assassinat\w*", r"violen\w*", r"blood\w*", r"gore", r"injur\w*", r"tortur\w*",
    r"genocide", r"massacre\w*", r"hostages?",
    # death
    r"dead", r"death\w*", r"died", r"dying", r"funeral\w*", r"suicid\w*", r"self-harm",
    # crime
    r"crim(?:e|es|inal|inals)", r"illegal\w*", r"steal\w*", r"stole", r"stolen", r"theft\w*", r"fraud\w*", r"scam\w*",
    r"launder\w*", r"police", r"arrest\w*", r"prison\w*", r"jail\w*", r"smuggl\w*", r"traffick\w*",
    # drugs, alcohol, gambling
    r"drugs?", r"cocaine", r"heroin", r"cannabis", r"marijuana", r"alcohol\w*", r"gambl\w*", r"casinos?",
    # sex and nudity
    r"sex", r"sexual\w*", r"sexy", r"porn\w*", r"erotic\w*", r"nud(?:e|es|ity)", r"naked", r"nsfw", r"lingerie",
    # religion and hate
    r"religio\w*", r"gods?", r"church\w*", r"mosques?", r"synagogues?", r"temples?", r"nazi\w*", r"hitler",
    r"holocaust", r"racis\w*", r"extremis\w*", r"hate", r"abus\w*",
    # countries and groups at the centre of current conflicts
    r"israel\w*", r"gaza", r"palestin\w*", r"iran\w*", r"ukrain\w*", r"russia\w*", r"hamas", r"hezbollah", r"isis",
]
SENSITIVE = re.compile(r"(?<![\w-])(?:" + "|".join(SENSITIVE_WORDS) + r")(?![\w-])", re.I)

# Appended by code to every image prompt, whatever the model wrote.
STYLE = ("Flat modern vector illustration, clean geometric shapes, soft gradient background in indigo and teal, "
         "professional and friendly, suitable for a business network. No people, no faces, no body parts, no text, "
         "no letters, no numbers, no logos, no flags, no symbols of any country, party or religion.")
IMAGE_SCHEMA = {"type": "OBJECT", "required": ["safe", "reason", "prompt"], "properties": {
    "safe": {"type": "BOOLEAN"}, "reason": {"type": "STRING"}, "prompt": {"type": "STRING"}}}


class ImageSkip(Exception):
    """The AI image was not made (unsafe, not configured, or the model failed). The draft gets a card instead."""


@dataclass
class Picture:
    kind: str                 # card | ai
    data: bytes
    ext: str                  # png | jpg
    alt: str
    prompt: str = ""
    notes: list = field(default_factory=list)


def sensitive(*texts):
    """The first sensitive word in the texts, or ''."""
    for t in texts:
        m = SENSITIVE.search(t or "")
        if m:
            return m.group(0)
    return ""


# --------------------------------------------------------------------------- which kind
def choose(cfg, recent):
    """'ai' or 'card'. `recent` is the image kinds of earlier drafts, newest first (drafts made before images: skipped)."""
    every = int((cfg.get("images") or {}).get("ai_every", 5) or 0)
    if every <= 0:
        return "card"
    since = 0
    for k in recent:
        if k == "ai":
            break
        since += 1
    return "ai" if since >= every - 1 else "card"


# --------------------------------------------------------------------------- texts from the post
# Characters the models like that the font has no glyph for (they would draw as boxes), and invisible direction marks.
PLAIN = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "―": "-", " ": " ", " ": " ",
                       " ": " ", " ": " ", **{c: None for c in "​‌‍‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"}})


def plain(text):
    return re.sub(r"[ \t]+", " ", (text or "").translate(PLAIN))


def headline_of(he):
    """The Hebrew headline: the first line of the Hebrew part."""
    he = plain(he).strip()
    return he.splitlines()[0].strip() if he else ""


def hook_of(en):
    """The English hook: the first paragraph, without hashtags."""
    first = re.split(r"\n\s*\n", plain(en).strip(), maxsplit=1)[0]
    return re.sub(r"(?<!\w)#\w+", "", first).strip()


# --------------------------------------------------------------------------- the card
def _font(size, weight="Bold"):
    # BASIC layout: Pillow does no bidi of its own, so python-bidi alone orders the Hebrew. With Pillow's raqm engine
    # (present on Linux runners, not on a default Mac) the text would be reversed twice.
    f = ImageFont.truetype(str(FONT), size, layout_engine=ImageFont.Layout.BASIC)
    try:
        f.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return f


def _gradient():
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(C1, C2))
    return small.resize((SIZE, SIZE), Image.BILINEAR)


def _visual(text, rtl):
    return get_display(text, base_dir="R") if rtl else text


def _wrap(draw, text, font, width, rtl):
    lines, cur = [], ""
    for w in text.split():
        cand = f"{cur} {w}".strip()
        if not cur or draw.textlength(_visual(cand, rtl), font=font) <= width:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    return lines + [cur] if cur else lines


def _fit(draw, text, sizes, width, rtl, max_lines, weight="Bold"):
    """The largest font size at which the text fits in max_lines; at the smallest size, extra lines end in an ellipsis."""
    for size in sizes:
        f = _font(size, weight)
        lines = _wrap(draw, text, f, width, rtl)
        if len(lines) <= max_lines:
            return f, size, lines
    lines = lines[:max_lines]
    lines[-1] = lines[-1].rstrip(" .,") + "…"
    return f, size, lines


def render_card(kind, headline, hook, name="", role=""):
    """A 1080x1080 PNG. `headline` is Hebrew (right to left); without one the English hook becomes the headline."""
    img = _gradient()
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse((SIZE - 380, -220, SIZE + 220, 380), fill=(255, 255, 255, 18))
    d.ellipse((-260, SIZE - 300, 300, SIZE + 260), fill=(255, 255, 255, 14))
    width = SIZE - 2 * PAD

    label_he, label_en = LABELS.get(kind, LABELS["news"])
    lf = _font(34, "SemiBold")
    t = _visual(label_he, True)
    tw = d.textlength(t, font=lf)
    d.rounded_rectangle((SIZE - PAD - tw - 56, PAD, SIZE - PAD, PAD + 68), radius=34, fill=(255, 255, 255, 235))
    d.text((SIZE - PAD - 28, PAD + 34), t, font=lf, fill=C1, anchor="rm")
    d.text((PAD, PAD + 34), label_en.upper(), font=_font(28, "SemiBold"), fill=(255, 255, 255, 200), anchor="lm")

    rtl = bool(headline)
    big = headline or hook
    small = hook if headline else ""
    hf, hsize, hlines = _fit(d, big, (92, 80, 70, 62), width, rtl, 4)
    ef, _, elines = _fit(d, small, (40,), width, False, 3, "Regular") if small else (None, 0, [])
    hstep, estep = round(hsize * 1.22), 54
    block = len(hlines) * hstep + (44 + 10 + 50 + len(elines) * estep if elines else 0)
    top, bottom = PAD + 68 + 60, SIZE - 200
    y = top + max(0, (bottom - top - block) // 2)

    for line in hlines:
        if rtl:
            d.text((SIZE - PAD, y), _visual(line, True), font=hf, fill=WHITE, anchor="ra")
        else:
            d.text((PAD, y), line, font=hf, fill=WHITE)
        y += hstep
    if elines:
        y += 44
        bar = (SIZE - PAD - 120, y, SIZE - PAD, y + 10) if rtl else (PAD, y, PAD + 120, y + 10)
        d.rounded_rectangle(bar, radius=5, fill=(255, 255, 255, 220))
        y += 60
        for line in elines:
            d.text((PAD, y), line, font=ef, fill=(255, 255, 255, 215))
            y += estep

    d.line((PAD, SIZE - 170, SIZE - PAD, SIZE - 170), fill=(255, 255, 255, 90), width=2)
    d.rounded_rectangle((PAD, SIZE - 135, PAD + 64, SIZE - 71), radius=16, fill=WHITE)
    d.text((PAD + 32, SIZE - 103), "</>", font=_font(26, "Bold"), fill=C1, anchor="mm")
    if name:
        d.text((PAD + 88, SIZE - 125), name, font=_font(34, "SemiBold"), fill=WHITE)
    if role:
        d.text((PAD + 88, SIZE - 84), role, font=_font(26, "Regular"), fill=(255, 255, 255, 190))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def short_role(headline):
    """'AI Automation Developer · AI Agents & ...' -> 'AI Automation Developer'."""
    return re.split(r"\s+[·|•-]\s+", headline or "", maxsplit=1)[0].strip()[:60]


# --------------------------------------------------------------------------- the AI illustration
def image_prompt(cfg, topic, en):
    """Ask the text model for a safe prompt. Returns the prompt; raises ImageSkip when it is not safe to illustrate."""
    s = prompt_text("image-prompt.md")
    system = s.split("<!-- IMAGE PROMPT START -->", 1)[1].split("<!-- IMAGE PROMPT END -->", 1)[0].strip()
    user = f"POST TITLE: {topic.get('title', '')}\n\nPOST:\n{en}"
    L = cfg["llm"]
    errors = []
    for provider in (L["primary"], L.get("fallback")):
        if not provider:
            continue
        try:
            out, _ = llm.generate(cfg, provider, system, user, 0.4, schema=IMAGE_SCHEMA)
        except llm.LLMError as e:
            errors.append(str(e))
            continue
        if out.get("safe") is not True:
            raise ImageSkip(f"the prompt writer judged this topic unsafe to illustrate ({str(out.get('reason') or 'no reason given')[:160]})")
        prompt = re.sub(r"\s+", " ", str(out.get("prompt") or "")).strip()
        if len(prompt) < 20:
            errors.append(f"{provider}: empty prompt")
            continue
        hit = sensitive(prompt)
        if hit:
            raise ImageSkip(f"the image prompt contained \"{hit}\"")
        return prompt[:600]
    raise ImageSkip("no image prompt: " + "; ".join(errors))


def cloudflare(cfg, prompt):
    """Image bytes from Cloudflare Workers AI. Raises ImageSkip when it is not configured or fails."""
    acct, token = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(), os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not acct or not token:
        raise ImageSkip("the CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN secrets are not set")
    ic = cfg.get("images") or {}
    model = ic.get("ai_model") or "@cf/black-forest-labs/flux-1-schnell"
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    try:
        _, headers, content = request("POST", url, headers={"Authorization": f"Bearer {token}"},
                                      body={"prompt": prompt, "steps": int(ic.get("ai_steps", 4))}, timeout=120, binary=True)
    except (HttpError, OSError) as e:
        raise ImageSkip(f"Cloudflare failed: {str(e)[:200]}") from None
    return decode_image_response(headers.get("content-type", ""), content)


def decode_image_response(content_type, content):
    if content_type.startswith("image/"):
        return content
    try:
        data = json.loads(content.decode("utf-8", "replace"))
        return base64.b64decode(data["result"]["image"])
    except (ValueError, KeyError, TypeError) as e:
        raise ImageSkip(f"Cloudflare returned no image ({type(e).__name__})") from None


def finish_ai(raw):
    """Check the generated image and re-encode it as JPEG. Raises ImageSkip for unreadable or blank images."""
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except OSError:
        raise ImageSkip("the image model returned an unreadable file") from None
    if max(ImageStat.Stat(img).stddev) < 6:
        raise ImageSkip("the image model returned a blank image (its own safety filter may have tripped)")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def ai_picture(cfg, topic, en):
    hit = sensitive(topic.get("title", ""), en)
    if hit:
        raise ImageSkip(f"the post mentions \"{hit}\"")
    prompt = image_prompt(cfg, topic, en)
    data = finish_ai(cloudflare(cfg, f"{prompt} {STYLE}"))
    return Picture("ai", data, "jpg", alt=f"Illustration: {hook_of(en)}"[:300], prompt=prompt)


# --------------------------------------------------------------------------- one entry point
def make(cfg, want, topic, he, en, profile):
    """A Picture of kind `want` (card | ai). An AI image that cannot be made falls back to a card, with a note."""
    notes = []
    if want == "ai":
        try:
            return ai_picture(cfg, topic, en)
        except ImageSkip as e:
            notes.append(f"Card instead of an AI image: {e}.")
    hook = hook_of(en)
    data = render_card(topic.get("kind", "news"), headline_of(he), hook, profile.get("name", ""), short_role(profile.get("headline", "")))
    return Picture("card", data, "png", alt=(headline_of(he) + " | " if he else "") + hook, notes=notes)


def store(cfg, pic, title):
    """Save the image on the images branch. Returns the image info kept in the issue."""
    branch = (cfg.get("images") or {}).get("branch") or "post-images"
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "post").lower()).strip("-")[:40].rstrip("-") or "post"
    path = f"images/{date.today().isoformat()}-{slug}-{secrets.token_hex(3)}.{pic.ext}"
    gh.ensure_branch(branch)
    gh.put_bytes(path, pic.data, f"Post image: {title[:60]}", branch=branch)
    return {"kind": pic.kind, "path": path, "branch": branch, "alt": pic.alt[:300], "prompt": pic.prompt}
