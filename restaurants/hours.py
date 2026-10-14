"""
"Open now", worked out at request time from weekly opening hours.

Hours are kept as weekly periods in Google's shape (day 0 = Sunday) and
evaluated against the current time, so cached data never reports a stale
"open now". Google supplies periods and a UTC offset directly; OpenStreetMap's
opening_hours text is converted by parse_osm(), and its offset comes from the
city config via city_utc_offset().
"""

import re
from datetime import datetime, timedelta, timezone

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY


def _minute_of_week(point):
    # Google: day 0 = Sunday.
    return (point.get("day", 0) * MINUTES_PER_DAY
            + point.get("hour", 0) * 60 + point.get("minute", 0))


def is_open(periods, utc_offset_minutes, now=None):
    """True/False, or None when the hours are unknown."""
    if not periods or utc_offset_minutes is None:
        return None

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(timezone.utc) + timedelta(minutes=utc_offset_minutes)
    day = (local.weekday() + 1) % 7          # Python: Monday = 0 -> Sunday = 0
    t = day * MINUTES_PER_DAY + local.hour * 60 + local.minute

    for period in periods:
        opening, closing = period.get("open"), period.get("close")
        if opening and not closing:
            return True                      # Google's way of saying "24/7"
        if not opening:
            continue
        start, end = _minute_of_week(opening), _minute_of_week(closing)
        if end <= start:
            end += MINUTES_PER_WEEK          # Saturday night past midnight
        if start <= t < end or start <= t + MINUTES_PER_WEEK < end:
            return True
    return False


# ---------------------------------------------------------------------------
# OpenStreetMap opening_hours
# ---------------------------------------------------------------------------

_OSM_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]   # OSM order; Google day = (i + 1) % 7
_DAY_SPEC = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?"
_RULE = re.compile(r"^((?:%s)(?:,(?:%s))*)?\s*(.*)$" % (_DAY_SPEC, _DAY_SPEC))
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\+?$")


def _expand_days(spec):
    days = []
    for part in spec.split(","):
        if "-" in part:
            a, b = (_OSM_DAYS.index(d) for d in part.split("-"))
            i = a
            while True:                      # wraps, e.g. Fr-Mo
                days.append(i)
                if i == b:
                    break
                i = (i + 1) % 7
        else:
            days.append(_OSM_DAYS.index(part))
    return days


def parse_osm(text):
    """OSM opening_hours text -> weekly periods, or None if we can't read it.

    Covers the common forms - "Mo-Fr 11:00-22:00; Sa,Su 12:00-23:00",
    "Tu-Su 17:00-02:00", "24/7", "Mo off". Anything fancier (months, public
    holidays with times, sunrise, comments) gives None, i.e. "hours unknown",
    rather than a wrong answer. Holiday-only rules ("PH off") are ignored.
    """
    if not text:
        return None
    text = text.strip()
    if text == "24/7":
        return [{"open": {"day": 0, "hour": 0, "minute": 0}}]

    schedule = {}                            # OSM day index -> [(start, end) minutes]
    for rule in text.split(";"):
        rule = rule.strip()
        if not rule or rule.startswith(("PH", "SH")):
            continue
        m = _RULE.match(rule)
        days = _expand_days(m.group(1)) if m.group(1) else list(range(7))
        times = m.group(2).strip()
        if times in ("off", "closed"):
            for d in days:
                schedule[d] = []
            continue
        ranges = []
        for part in times.split(","):
            t = _TIME_RANGE.match(part.strip())
            if not t:
                return None
            start = int(t.group(1)) * 60 + int(t.group(2))
            end = int(t.group(3)) * 60 + int(t.group(4))
            if start >= MINUTES_PER_DAY:
                return None
            if end <= start:
                end += MINUTES_PER_DAY       # past midnight
            ranges.append((start, end))
        for d in days:                       # later rules override earlier ones
            schedule[d] = ranges

    if not schedule:
        return None
    periods = []
    for d, ranges in sorted(schedule.items()):
        gday = (d + 1) % 7
        for start, end in ranges:
            close_day = (gday + end // MINUTES_PER_DAY) % 7
            end %= MINUTES_PER_DAY
            periods.append({"open": {"day": gday, "hour": start // 60, "minute": start % 60},
                            "close": {"day": close_day, "hour": end // 60, "minute": end % 60}})
    return periods


# ---------------------------------------------------------------------------
# City UTC offset (for sources that don't give one)
# ---------------------------------------------------------------------------

def _nth_sunday(year, month, n):
    """Date of the nth Sunday of a month; n = -1 for the last one."""
    if n > 0:
        first = datetime(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    nxt = datetime(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _in_dst(rule, std_offset, now):
    utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    y = utc.year
    if rule == "us":   # 2nd Sunday of March 02:00 local to 1st Sunday of November 02:00 local
        start = _nth_sunday(y, 3, 2) + timedelta(hours=2, minutes=-std_offset)
        end = _nth_sunday(y, 11, 1) + timedelta(hours=2, minutes=-(std_offset + 60))
    elif rule == "eu":  # last Sunday of March to last Sunday of October, 01:00 UTC
        start = _nth_sunday(y, 3, -1) + timedelta(hours=1)
        end = _nth_sunday(y, 10, -1) + timedelta(hours=1)
    else:
        return False
    return start <= utc < end


def city_utc_offset(city, now=None):
    """Minutes east of UTC for the city right now, or None if not configured."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        offset = now.astimezone(ZoneInfo(city["timezone"])).utcoffset()
        return int(offset.total_seconds() // 60)
    except Exception:  # noqa: BLE001 - no tz database (Windows) or no timezone set
        pass
    std = city.get("utc_offset_minutes")
    if std is None:
        return None
    return std + (60 if _in_dst(city.get("dst"), std, now) else 0)
