"""Tiny stdlib HTTP helper (no requests dependency)."""
import json
import urllib.error
import urllib.request

USER_AGENT = "linkedin-post-pipeline/1.0 (personal, low-volume; GitHub Actions)"


class HttpError(Exception):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status, self.body, self.url = status, body, url


def request(method, url, *, headers=None, body=None, timeout=30, raw=False, binary=False):
    """Returns (status, headers, parsed_json_or_text), or the body as bytes with binary=True. Raises HttpError on 4xx/5xx."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode() if not isinstance(body, (bytes, str)) else (body.encode() if isinstance(body, str) else body)
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
            resp_headers = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), url) from None
    if binary:
        return status, resp_headers, content
    text = content.decode(charset, "replace")
    if raw or not text:
        return status, resp_headers, text
    try:
        return status, resp_headers, json.loads(text)
    except json.JSONDecodeError:
        return status, resp_headers, text


def get_json(url, **kw):
    return request("GET", url, **kw)[2]
