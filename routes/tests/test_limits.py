"""Tests for routes/limits.py, on a fake clock.

    python -m unittest discover -s routes/tests -t .
"""

import threading
import unittest

from routes import limits


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class WindowTests(unittest.TestCase):

    def test_fits_until_the_limit(self):
        w = limits.Window(3, 60)
        self.assertEqual(w.wait(3, 0), 0)
        w.add(3, 0)
        self.assertGreater(w.wait(1, 0), 0)

    def test_wait_is_until_the_oldest_needed_event_ages_out(self):
        w = limits.Window(3, 60)
        w.add(1, 0)
        w.add(1, 10)
        w.add(1, 20)
        self.assertEqual(w.wait(1, 30), 30)       # event at t=0 expires at t=60
        self.assertEqual(w.wait(2, 30), 40)       # needs t=10's too, at t=70

    def test_more_than_the_limit_can_never_fit(self):
        self.assertEqual(limits.Window(3, 60).wait(4, 0), float("inf"))

    def test_events_expire(self):
        w = limits.Window(1, 60)
        w.add(1, 0)
        self.assertEqual(w.wait(1, 60), 0)

    def test_give_back_returns_the_most_recent(self):
        w = limits.Window(5, 60)
        w.add(1, 0)
        w.add(4, 30)
        w.give_back(4)
        self.assertEqual(list(w.events), [0])


class OrsBudgetTests(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.budget = limits.OrsBudget(per_minute=40, per_day=2000, clock=self.clock)

    def test_keeps_a_safety_margin_below_the_plan(self):
        self.assertEqual(self.budget.minute.limit, 36)
        self.assertEqual(self.budget.day.limit, 1800)

    def test_refuses_before_ors_would(self):
        for _ in range(6):
            self.budget.reserve(6)                       # 36 calls
        with self.assertRaises(limits.Busy) as cm:
            self.budget.reserve(6)
        self.assertEqual(cm.exception.scope, "minute")
        self.assertEqual(cm.exception.retry_after, 60)

    def test_unused_calls_are_given_back(self):
        for _ in range(6):
            r = self.budget.reserve(6)
            self.budget.settle(r, 3)                      # a loop that needed 3
        self.budget.reserve(6)                            # 21 used: room remains

    def test_minute_frees_up(self):
        for _ in range(6):
            self.budget.reserve(6)
        self.clock.advance(60)
        self.budget.reserve(6)

    def test_daily_allowance(self):
        budget = limits.OrsBudget(per_minute=1000, per_day=100, clock=self.clock)
        for _ in range(15):
            budget.reserve(6)                             # 90 of 90 usable
        with self.assertRaises(limits.Busy) as cm:
            budget.reserve(6)
        self.assertEqual(cm.exception.scope, "day")
        self.assertGreater(cm.exception.retry_after, 23 * 3600)

    def test_simultaneous_clicks_cannot_overshoot_together(self):
        admitted = []

        def click():
            try:
                self.budget.reserve(6)
                admitted.append(1)
            except limits.Busy:
                pass
        threads = [threading.Thread(target=click) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(admitted), 6)                # 36 // 6, never 7

    def test_plan_limits_come_from_env(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"ORS_REQUESTS_PER_MINUTE": "60",
                                          "ORS_REQUESTS_PER_DAY": "10000"}):
            b = limits.OrsBudget.from_env()
        self.assertEqual((b.minute.limit, b.day.limit), (54, 9000))


class VisitorLimitTests(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.visitors = limits.VisitorLimit(per_minute=5, per_day=50, clock=self.clock)

    def test_per_minute(self):
        for _ in range(5):
            self.visitors.check("1.2.3.4")
        with self.assertRaises(limits.Busy) as cm:
            self.visitors.check("1.2.3.4")
        self.assertEqual(cm.exception.scope, "minute")

    def test_visitors_are_counted_separately(self):
        for _ in range(5):
            self.visitors.check("1.2.3.4")
        self.visitors.check("5.6.7.8")                    # not affected

    def test_per_day(self):
        for _ in range(10):
            for _ in range(5):
                self.visitors.check("1.2.3.4")
            self.clock.advance(61)
        with self.assertRaises(limits.Busy) as cm:
            self.visitors.check("1.2.3.4")
        self.assertEqual(cm.exception.scope, "day")

    def test_idle_visitors_are_forgotten(self):
        self.visitors.PRUNE_AT = 3
        for ip in ("a", "b", "c"):
            self.visitors.check(ip)
        self.clock.advance(24 * 3600 + 1)
        self.visitors.check("d")
        self.assertEqual(set(self.visitors.visitors), {"d"})


if __name__ == "__main__":
    unittest.main()
