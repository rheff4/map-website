"""
"Open now", worked out at request time from weekly opening hours.

Google's own openNow flag would go stale in the cache, so we cache the weekly
periods instead and evaluate them against the current time. The UTC offset
comes from Google too, which avoids needing a time-zone database on Windows.
"""

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
