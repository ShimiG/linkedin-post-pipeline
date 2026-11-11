"""Publish to the member's own profile with the Posts API (self-serve "Share on LinkedIn" product)."""
import os
import re
import urllib.parse
from datetime import date

from .http import HttpError, request

API = "https://api.linkedin.com"
# Posts API "little text": these characters must be backslash-escaped or LinkedIn drops/mangles the text.
RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")
HASHTAG = re.compile(r"(?<![\w#])#(\w+)")


class LinkedInError(Exception):
    pass


def api_version(today=None):
    """LinkedIn versions are monthly (YYYYMM) and live about a year; two months back is always active."""
    d = today or date.today()
    y, m = d.year, d.month - 2
    if m <= 0:
        y, m = y - 1, m + 12
    return os.environ.get("LINKEDIN_VERSION") or f"{y}{m:02d}"


def little_text(text):
    """Escape reserved characters, then turn #tags into LinkedIn hashtag templates."""
    escaped = RESERVED.sub(r"\\\1", text)
    return HASHTAG.sub(lambda m: "{hashtag|\\#|" + m.group(1) + "}", escaped)


def _headers(token):
    return {"Authorization": f"Bearer {token}", "LinkedIn-Version": api_version(), "X-Restli-Protocol-Version": "2.0.0"}


def _token():
    t = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    if not t:
        raise LinkedInError("LINKEDIN_ACCESS_TOKEN secret is not set")
    return t


def person_urn(token):
    override = os.environ.get("LINKEDIN_PERSON_URN", "").strip()
    if override:
        return override
    try:
        _, _, me = request("GET", f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
    except HttpError as e:
        raise LinkedInError(_explain(e)) from None
    return f"urn:li:person:{me['sub']}"


def _explain(e):
    if e.status == 401:
        return "LinkedIn rejected the token (401). It has probably expired: generate a new one and update the LINKEDIN_ACCESS_TOKEN secret."
    if e.status == 403:
        return "LinkedIn refused (403). The token needs the w_member_social and openid scopes (Share on LinkedIn + Sign In with LinkedIn using OpenID Connect)."
    return str(e)


def upload_image(token, author, data):
    """Images API: register an upload, PUT the bytes, return the image URN to reference from the post."""
    try:
        _, _, init = request("POST", f"{API}/rest/images?action=initializeUpload", headers=_headers(token),
                             body={"initializeUploadRequest": {"owner": author}})
        value = init["value"]
        request("PUT", value["uploadUrl"], headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
                body=data, timeout=120, raw=True)
    except HttpError as e:
        raise LinkedInError(f"image upload failed: {_explain(e)}") from None
    except (KeyError, TypeError):
        raise LinkedInError("image upload failed: LinkedIn did not return an upload URL") from None
    return value["image"]


def publish(text, first_comment=None, image=None, alt=""):
    """Post `text` (with `image` bytes attached, if given). Returns (post_urn, post_url, comment_error_or_None)."""
    token = _token()
    author = person_urn(token)
    body = {"author": author, "commentary": little_text(text), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    if image:
        body["content"] = {"media": {"id": upload_image(token, author, image), "altText": (alt or "")[:4000]}}
    try:
        _, headers, _ = request("POST", f"{API}/rest/posts", headers=_headers(token), body=body)
    except HttpError as e:
        raise LinkedInError(_explain(e)) from None
    urn = headers.get("x-restli-id") or headers.get("x-linkedin-id") or ""
    url = f"https://www.linkedin.com/feed/update/{urn}/" if urn else ""
    comment_error = None
    if first_comment and urn:
        try:
            request("POST", f"{API}/rest/socialActions/{urllib.parse.quote(urn, safe='')}/comments", headers=_headers(token),
                    body={"actor": author, "object": urn, "message": {"text": first_comment}})
        except HttpError as e:
            comment_error = _explain(e)
    return urn, url, comment_error
