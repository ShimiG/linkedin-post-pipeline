"""Load config.yml + the author profile, with repo-variable overrides from the environment."""
import os
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_config():
    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    if os.environ.get("PUBLISH_MODE"):
        cfg["publish_mode"] = os.environ["PUBLISH_MODE"].strip()
    if os.environ.get("TIMEZONE"):
        cfg["timezone"] = os.environ["TIMEZONE"].strip()
    if cfg["publish_mode"] not in ("simulated", "linkedin"):
        raise SystemExit(f"publish_mode must be simulated or linkedin, got {cfg['publish_mode']!r}")
    try:
        tz(cfg)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise SystemExit(f"timezone must be a region name such as Asia/Jerusalem or Europe/London, got {cfg['timezone']!r} "
                         "(config.yml, or the repo variable TIMEZONE). Offsets like GMT+2 are not accepted.") from None
    return cfg


def load_profile():
    """Profile comes from the PROFILE_YML secret in Actions, or a local gitignored profile.yml."""
    text = os.environ.get("PROFILE_YML", "").strip()
    if not text:
        p = ROOT / "profile.yml"
        text = p.read_text() if p.exists() else ""
    data = yaml.safe_load(text) if text else {}
    if not isinstance(data, dict):
        raise SystemExit("profile must be a YAML mapping of key: value")
    return {str(k): (", ".join(map(str, v)) if isinstance(v, list) else str(v)).strip() for k, v in data.items() if v}


def tz(cfg):
    return ZoneInfo(cfg.get("timezone") or "UTC")


def today(cfg):
    return datetime.now(tz(cfg)).date()


def prompt_text(name):
    return (ROOT / "prompts" / name).read_text()


def system_prompt(cfg):
    s = prompt_text("post-system-prompt.md")
    start, end = "<!-- SYSTEM PROMPT START -->", "<!-- SYSTEM PROMPT END -->"
    body = s.split(start, 1)[1].split(end, 1)[0].strip()
    return body.replace("MAX_HASHTAGS", str(cfg["post"]["max_hashtags"]))
