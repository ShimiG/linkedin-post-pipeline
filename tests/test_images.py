import base64
import io

import pytest
from PIL import Image

from poster import gh, images, linkedin, llm, review
from poster.compose import Draft

CFG = {"publish_mode": "simulated", "llm": {"primary": "groq", "fallback": "gemini"}, "images": {"ai_every": 5},
       "bilingual": {"enabled": True, "separator": "• • •"}, "post": {"source_link": "manual"}}
TOPIC = {"kind": "news", "title": "A new open model writes tests", "category": "ai_news", "angle": ""}
HE = "מודל פתוח חדש כותב בדיקות\n*English below\n\nגוף."
EN = "A new open model writes unit tests on its own.\n\nWhy it matters.\n\n#AI #Testing"
PROFILE = {"name": "Dev Person", "headline": "AI Automation Developer · AI Agents"}


def png(color=(10, 20, 30), noisy=True):
    img = Image.new("RGB", (64, 64), color)
    if noisy:
        for x in range(0, 64, 2):
            img.putpixel((x, x), (250, 250, 250))
            img.putpixel((x, 63 - x), (0, 200, 90))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# --------------------------------------------------------------------------- safety words
@pytest.mark.parametrize("text", ["The election results", "a strike on Gaza", "Two people were killed", "sexual content",
                                  "selling drugs online", "the President said", "a cyberattack on banks", "He died in 2006",
                                  "pornography filters", "church records", "military drones"])
def test_sensitive_words_are_caught(text):
    assert images.sensitive(text)


@pytest.mark.parametrize("text", ["Hacker News readers liked it", "Python 3.15 is out", "a silicon die shrink", "Essex University",
                                  "Kubernetes clusters", "debugging a compiler", "the sextant and navigation", "executes code"])
def test_ordinary_tech_text_is_not_flagged(text):
    assert images.sensitive(text) == ""


# --------------------------------------------------------------------------- one in five
def test_choose_one_in_five():
    assert images.choose(CFG, []) == "card"
    assert images.choose(CFG, ["card"] * 3) == "card"
    assert images.choose(CFG, ["card"] * 4) == "ai"
    assert images.choose(CFG, ["ai", "card", "card", "card", "card"]) == "card"
    assert images.choose(CFG, ["card", "none", "card", "card", "ai"]) == "ai"
    assert images.choose({"images": {"ai_every": 0}}, ["card"] * 10) == "card"


# --------------------------------------------------------------------------- the card
def test_card_is_a_square_png_with_and_without_hebrew():
    for he in (images.headline_of(HE), ""):
        data = images.render_card("news", he, images.hook_of(EN), "Dev Person", "AI Automation Developer")
        img = Image.open(io.BytesIO(data))
        assert img.format == "PNG" and img.size == (1080, 1080)


def test_long_headline_still_renders():
    long = "כותרת ארוכה מאוד " * 12
    assert Image.open(io.BytesIO(images.render_card("fact", long, "word " * 80))).size == (1080, 1080)


def test_texts_for_the_card():
    assert images.headline_of(HE) == "מודל פתוח חדש כותב בדיקות"
    assert images.hook_of("Hook line #AI\n\nmore") == "Hook line"
    assert images.short_role("AI Automation Developer · AI Agents") == "AI Automation Developer"


# --------------------------------------------------------------------------- the AI path and its fallbacks
def no_llm(*a, **k):
    pytest.fail("the prompt writer must not be called")


def test_sensitive_post_gets_a_card_without_asking_any_model(monkeypatch):
    monkeypatch.setattr(llm, "generate", no_llm)
    monkeypatch.setattr(images, "cloudflare", no_llm)
    pic = images.make(CFG, "ai", {**TOPIC, "title": "Election deepfakes"}, HE, EN, PROFILE)
    assert pic.kind == "card" and "Election" in pic.notes[0]


def test_unsafe_verdict_from_the_prompt_writer_gives_a_card(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda *a, **k: ({"safe": False, "reason": "about a court case", "prompt": ""}, "m"))
    monkeypatch.setattr(images, "cloudflare", no_llm)
    pic = images.make(CFG, "ai", TOPIC, HE, EN, PROFILE)
    assert pic.kind == "card" and "court case" in pic.notes[0]


def test_sensitive_word_in_the_prompt_gives_a_card(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda *a, **k: ({"safe": True, "reason": "", "prompt": "A robot holding a gun next to a laptop on a desk"}, "m"))
    monkeypatch.setattr(images, "cloudflare", no_llm)
    pic = images.make(CFG, "ai", TOPIC, HE, EN, PROFILE)
    assert pic.kind == "card" and "gun" in pic.notes[0]


def test_ai_image_gets_the_fixed_style_and_is_jpeg(monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "generate", lambda *a, **k: ({"safe": True, "reason": "", "prompt": "A friendly robot writing checklists beside a glowing laptop"}, "m"))
    monkeypatch.setattr(images, "cloudflare", lambda cfg, p: sent.update(p=p) or png())
    pic = images.make(CFG, "ai", TOPIC, HE, EN, PROFILE)
    assert pic.kind == "ai" and pic.ext == "jpg" and Image.open(io.BytesIO(pic.data)).format == "JPEG"
    assert sent["p"].endswith(images.STYLE) and "No people" in sent["p"]
    assert pic.prompt.startswith("A friendly robot")


def test_missing_cloudflare_secrets_give_a_card(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: ({"safe": True, "reason": "", "prompt": "A friendly robot writing checklists beside a laptop"}, "m"))
    pic = images.make(CFG, "ai", TOPIC, HE, EN, PROFILE)
    assert pic.kind == "card" and "CLOUDFLARE" in pic.notes[0]


def test_blank_image_is_refused():
    with pytest.raises(images.ImageSkip):
        images.finish_ai(png((0, 0, 0), noisy=False))


def test_cloudflare_response_shapes():
    raw = png()
    assert images.decode_image_response("image/png", raw) == raw
    js = ('{"result": {"image": "%s"}, "success": true}' % base64.b64encode(raw).decode()).encode()
    assert images.decode_image_response("application/json", js) == raw
    with pytest.raises(images.ImageSkip):
        images.decode_image_response("application/json", b'{"success": false}')


# --------------------------------------------------------------------------- the issue
INFO = {"kind": "card", "path": "images/x.png", "branch": "post-images", "alt": "alt text", "prompt": ""}


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/repo")


def draft_issue(image=INFO, labels=("draft",)):
    body = gh.render_draft(TOPIC, [], Draft(EN, model="m", post_text_he=HE, translation_model="m"), image=image)
    return {"number": 9, "title": "Post draft: " + TOPIC["title"], "body": body, "labels": [{"name": l} for l in labels], "state": "open"}


def test_image_block_round_trip(repo):
    body = draft_issue()["body"]
    assert gh.parse_image(body) == INFO
    assert "https://github.com/me/repo/blob/post-images/images/x.png?raw=true" in body
    swapped = gh.replace_image(body, {"kind": "none"})
    assert gh.parse_image(swapped) == {"kind": "none"} and gh.parse_parts(swapped) == gh.parse_parts(body)


def test_image_block_is_added_to_older_drafts(repo):
    body = draft_issue(image=None)["body"]
    assert gh.parse_image(body) == {}
    added = gh.replace_image(body, INFO)
    assert gh.parse_image(added) == INFO and added.index(gh.SECTION_END) < added.index(gh.IMG_START)


@pytest.fixture
def board(monkeypatch):
    log = []
    for name in ("comment", "add_labels", "close", "update_issue"):
        monkeypatch.setattr(gh, name, lambda *a, _n=name, **k: log.append((_n, a, k)))
    return log


def test_image_none_removes_it(repo, board):
    review.image(CFG, draft_issue(), "none")
    assert gh.parse_image(board[0][2]["body"]) == {"kind": "none"}
    assert "text only" in board[1][1][1]


def test_image_command_redraws_same_kind(repo, board, monkeypatch):
    asked = {}
    monkeypatch.setattr(review, "load_profile", lambda: PROFILE)
    monkeypatch.setattr(images, "make", lambda cfg, want, *a: asked.update(want=want) or images.Picture("card", b"x", "png", "alt"))
    monkeypatch.setattr(images, "store", lambda cfg, pic, title: {**INFO, "path": "images/new.png"})
    review.image(CFG, draft_issue(), "")
    assert asked["want"] == "card" and gh.parse_image(board[0][2]["body"])["path"] == "images/new.png"


def test_image_command_refuses_after_publishing(repo, board):
    review.image(CFG, draft_issue(labels=("draft", "published")), "ai")
    assert [e[0] for e in board] == ["comment"] and "Already published" in board[0][1][1]


def test_approve_live_attaches_the_image(repo, board, monkeypatch):
    sent = {}
    monkeypatch.setattr(gh, "raw_file", lambda path, ref: b"IMG:" + path.encode())
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.update(k) or ("urn:li:share:1", "https://li/1", None))
    review.approve({**CFG, "publish_mode": "linkedin"}, draft_issue())
    assert sent == {"image": b"IMG:images/x.png", "alt": "alt text"}


def test_approve_live_without_image(repo, board, monkeypatch):
    sent = {}
    monkeypatch.setattr(gh, "raw_file", no_llm)
    monkeypatch.setattr(linkedin, "publish", lambda text, fc, **k: sent.update(k) or ("urn:li:share:1", "https://li/1", None))
    review.approve({**CFG, "publish_mode": "linkedin"}, draft_issue(image={"kind": "none"}))
    assert sent["image"] is None


def test_simulated_approve_shows_the_image(repo, board):
    review.approve(CFG, draft_issue())
    assert "images/x.png" in board[0][1][1]


def test_linkedin_uploads_then_references_the_image(monkeypatch):
    calls = []

    def fake(method, url, headers=None, body=None, **k):
        calls.append((method, url, body))
        if "initializeUpload" in url:
            return 200, {}, {"value": {"uploadUrl": "https://upload/1", "image": "urn:li:image:42"}}
        if url == "https://upload/1":
            return 201, {}, ""
        return 201, {"x-restli-id": "urn:li:share:7"}, ""
    monkeypatch.setattr(linkedin, "request", fake)
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.setenv("LINKEDIN_PERSON_URN", "urn:li:person:me")
    urn, _, _ = linkedin.publish("Hello", image=b"bytes", alt="a picture")
    assert urn == "urn:li:share:7"
    assert [c[1] for c in calls][1:] == ["https://upload/1", f"{linkedin.API}/rest/posts"]
    assert calls[1][2] == b"bytes"
    assert calls[2][2]["content"] == {"media": {"id": "urn:li:image:42", "altText": "a picture"}}


def test_characters_without_glyphs_are_replaced():
    assert images.headline_of("נלחם ב‑ALS‏") == "נלחם ב-ALS"
    assert images.hook_of("a 31.2‑second flight") == "a 31.2-second flight"
