"""
Rate limits for route generation.

One OpenRouteService key serves every visitor, so its quota is shared - on
the free plan, 2,000 requests a day and 40 a minute. HeiGIT's terms say that
repeatedly going over gets access blocked and the account possibly removed,
so two guards sit in front of every ORS call:

- OrsBudget, the shared quota. A generation reserves its worst case before
  calling ORS and gives back what it didn't use, so simultaneous clicks
  can't jointly overshoot. When the budget is spent it refuses, rather than
  letting ORS refuse.
- VisitorLimit, per visitor address, so one person - or a script - can't use
  up everyone's routes.

Both count with sliding windows: "in the last minute" and "in the last 24
hours". A rolling 24 hours never allows more than the daily quota in any
calendar day, whenever ORS resets its counter.

Counts live in memory, so a server restart clears them. ORS still enforces
its own limits; this exists so that we stay under them.
"""

import collections
import os
import threading
import time

# HeiGIT's free Standard plan. The academic Collaborative plan raises these
# to 60 a minute and 10,000 a day: set ORS_REQUESTS_PER_MINUTE and
# ORS_REQUESTS_PER_DAY in .env if you're on it.
DEFAULT_ORS_PER_MINUTE = 40
DEFAULT_ORS_PER_DAY = 2000

# Use only this share of the plan, so clock differences between us and ORS
# can't tip a busy minute over the edge.
SAFETY_MARGIN = 0.9

# Per visitor, counted in route generations (each is up to 6 ORS calls).
# Enough to try a handful of routes in a row; not enough for one visitor to
# take the whole site's daily allowance.
VISITOR_PER_MINUTE = 5
VISITOR_PER_DAY = 50

MINUTE = 60.0
DAY = 24 * 3600.0


class Busy(Exception):
    """A limit was reached. `retry_after` is in seconds; `scope` says which
    window - 'minute' or 'day'."""

    def __init__(self, retry_after, scope):
        super().__init__("retry in %.0f s (%s)" % (retry_after, scope))
        self.retry_after = max(1, int(retry_after + 0.999))
        self.scope = scope


class Window:
    """At most `limit` events in any `seconds`-long span. Not thread-safe on
    its own; callers hold a lock."""

    def __init__(self, limit, seconds):
        self.limit = limit
        self.seconds = seconds
        self.events = collections.deque()

    def _expire(self, now):
        while self.events and self.events[0] <= now - self.seconds:
            self.events.popleft()

    def wait(self, n, now):
        """Seconds until `n` more events would fit; 0 if they fit now."""
        self._expire(now)
        if n > self.limit:
            return float("inf")
        excess = len(self.events) + n - self.limit
        if excess <= 0:
            return 0.0
        # The oldest `excess` events have to age out first.
        return self.events[excess - 1] + self.seconds - now

    def add(self, n, now):
        self.events.extend([now] * n)

    def give_back(self, n):
        """Return the most recent `n` events - reserved but never used."""
        for _ in range(min(n, len(self.events))):
            self.events.pop()

    def empty(self, now):
        self._expire(now)
        return not self.events


class OrsBudget:
    """The shared ORS quota."""

    def __init__(self, per_minute, per_day, clock=time.time):
        self.minute = Window(int(per_minute * SAFETY_MARGIN), MINUTE)
        self.day = Window(int(per_day * SAFETY_MARGIN), DAY)
        self.clock = clock
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls(int(os.environ.get("ORS_REQUESTS_PER_MINUTE") or DEFAULT_ORS_PER_MINUTE),
                   int(os.environ.get("ORS_REQUESTS_PER_DAY") or DEFAULT_ORS_PER_DAY))

    def reserve(self, n):
        """Hold `n` calls, or raise Busy. Pair with settle()."""
        with self.lock:
            now = self.clock()
            day_wait = self.day.wait(n, now)
            if day_wait:
                raise Busy(day_wait, "day")
            minute_wait = self.minute.wait(n, now)
            if minute_wait:
                raise Busy(minute_wait, "minute")
            self.minute.add(n, now)
            self.day.add(n, now)
        return n

    def settle(self, reserved, used):
        """Give back the calls a generation reserved but didn't make."""
        unused = max(0, reserved - used)
        if unused:
            with self.lock:
                self.minute.give_back(unused)
                self.day.give_back(unused)


class VisitorLimit:
    """Route generations per visitor address."""

    # Forget visitors with nothing left in their windows once this many are
    # tracked, so memory can't grow without bound.
    PRUNE_AT = 5000

    def __init__(self, per_minute=VISITOR_PER_MINUTE, per_day=VISITOR_PER_DAY, clock=time.time):
        self.per_minute = per_minute
        self.per_day = per_day
        self.clock = clock
        self.visitors = {}
        self.lock = threading.Lock()

    def check(self, client):
        """Count one generation for `client`, or raise Busy if over."""
        with self.lock:
            now = self.clock()
            if len(self.visitors) >= self.PRUNE_AT:
                self.visitors = {c: w for c, w in self.visitors.items() if not w[1].empty(now)}
            minute, day = self.visitors.setdefault(
                client, (Window(self.per_minute, MINUTE), Window(self.per_day, DAY)))
            day_wait = day.wait(1, now)
            if day_wait:
                raise Busy(day_wait, "day")
            minute_wait = minute.wait(1, now)
            if minute_wait:
                raise Busy(minute_wait, "minute")
            minute.add(1, now)
            day.add(1, now)
