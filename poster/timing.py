"""When posts go out: the good time slot for each version (`/schedule <version> next`) and the quiet weekend hours
in which `/approve` asks first. All times are in config.yml -> timezone; the settings are config.yml -> posting."""
from datetime import datetime, timedelta

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # index = datetime.weekday()
DEFAULT = {
    "he": {"days": ["sun", "mon", "tue", "wed", "thu"], "from": "08:30", "until": "10:30"},
    "en": {"days": ["mon", "tue", "wed", "thu"], "from": "09:30", "until": "11:00"},
    "quiet": {"from": "fri 12:00", "until": "sun 00:00"},
}


def _hm(text):
    h, m = str(text).strip().split(":")
    return int(h), int(m)


def _week_minute(text):
    """'fri 12:00' -> minutes since Monday 00:00."""
    day, hm = str(text).strip().lower().split()
    h, m = _hm(hm)
    return DAYS.index(day[:3]) * 1440 + h * 60 + m


def settings(cfg):
    p = cfg.get("posting") or {}
    return {"he": {**DEFAULT["he"], **(p.get("hebrew") or {})}, "en": {**DEFAULT["en"], **(p.get("english") or {})},
            "quiet": {**DEFAULT["quiet"], **(p.get("quiet") or {})}}


def quiet(cfg, at):
    """True in the quiet hours (by default Friday 12:00 until the end of Saturday), when hardly anyone reads LinkedIn."""
    q = settings(cfg)["quiet"]
    a, b = _week_minute(q["from"]), _week_minute(q["until"])
    now = at.weekday() * 1440 + at.hour * 60 + at.minute
    return a <= now < b if a <= b else (now >= a or now < b)


def next_slot(cfg, lang, now, avoid_days=()):
    """The next good time to publish `lang` ("he", "en"; "both" uses the Hebrew slot), at or after `now` (aware).
    Inside today's slot it is a few minutes from now. `avoid_days` (dates) are skipped, so the two versions
    of one draft go out on different days."""
    s = settings(cfg)["en" if lang == "en" else "he"]
    days = {d.strip().lower()[:3] for d in s["days"]}
    (fh, fm), (uh, um) = _hm(s["from"]), _hm(s["until"])
    for i in range(15):
        d = (now + timedelta(days=i)).date()
        if DAYS[d.weekday()] not in days or d in avoid_days:
            continue
        start = datetime(d.year, d.month, d.day, fh, fm, tzinfo=now.tzinfo)
        end = datetime(d.year, d.month, d.day, uh, um, tzinfo=now.tzinfo)
        if now < start:
            return start
        soon = (now + timedelta(minutes=10)).replace(second=0, microsecond=0)
        soon += timedelta(minutes=(-soon.minute) % 5)
        if soon < end:
            return soon
    return None


def slot_days(cfg, langs):
    """Weekday names ("mon"...) on which every version in `langs` has a slot."""
    s = settings(cfg)
    return set.intersection(*({d.strip().lower()[:3] for d in s[l]["days"]} for l in langs))


def open_day(cfg, today, taken=(), langs=("he",)):
    """The first day after `today` with a slot for every version in `langs` and no other post published or
    scheduled (`taken`, dates). An "On this day" draft is written for this day, so there is time to review it."""
    days = slot_days(cfg, langs) or slot_days(cfg, ("he",))
    first = None
    for i in range(1, 22):
        d = today + timedelta(days=i)
        if DAYS[d.weekday()] in days:
            first = first or d
            if d not in taken:
                return d
    return first or today + timedelta(days=1)


def slot_on(cfg, lang, day, now):
    """The good time to publish `lang` on `day` (a date), not before `now` (aware): that version's slot, or the
    Hebrew slot when the version has none that day. None when the day's slots are over."""
    start = max(now, datetime(day.year, day.month, day.day, tzinfo=now.tzinfo))
    for l in dict.fromkeys(("en" if lang == "en" else "he", "he")):
        at = next_slot(cfg, l, start)
        if at and at.date() == day:
            return at
    return None
