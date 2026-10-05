import unittest
from datetime import datetime, timedelta, timezone

from restaurants.hours import is_open

BOSTON_EDT = -240  # UTC-4, in minutes


def at(year, month, day, hour, minute=0):
    """A Boston local time."""
    return datetime(year, month, day, hour, minute,
                    tzinfo=timezone(timedelta(minutes=BOSTON_EDT)))


def period(open_day, open_hour, close_day, close_hour):
    return {"open": {"day": open_day, "hour": open_hour, "minute": 0},
            "close": {"day": close_day, "hour": close_hour, "minute": 0}}


# Monday 5 Oct 2026 in Boston. Google: Sunday = 0, Monday = 1.
DINNER_MON = [period(1, 17, 1, 22)]


class IsOpenTest(unittest.TestCase):
    def test_inside_and_outside_hours(self):
        self.assertTrue(is_open(DINNER_MON, BOSTON_EDT, at(2026, 10, 5, 19)))
        self.assertFalse(is_open(DINNER_MON, BOSTON_EDT, at(2026, 10, 5, 12)))
        self.assertFalse(is_open(DINNER_MON, BOSTON_EDT, at(2026, 10, 5, 22)))

    def test_past_midnight(self):
        late = [period(5, 18, 6, 2)]  # Friday 18:00 to Saturday 02:00
        self.assertTrue(is_open(late, BOSTON_EDT, at(2026, 10, 10, 1)))   # Sat 01:00

    def test_saturday_night_into_sunday_wraps_the_week(self):
        late = [period(6, 20, 0, 2)]  # Saturday 20:00 to Sunday 02:00
        self.assertTrue(is_open(late, BOSTON_EDT, at(2026, 10, 11, 1)))   # Sun 01:00
        self.assertTrue(is_open(late, BOSTON_EDT, at(2026, 10, 10, 23)))  # Sat 23:00

    def test_open_24_7(self):
        self.assertTrue(is_open([{"open": {"day": 0, "hour": 0, "minute": 0}}], BOSTON_EDT))

    def test_unknown_hours(self):
        self.assertIsNone(is_open([], BOSTON_EDT))
        self.assertIsNone(is_open(DINNER_MON, None))


if __name__ == "__main__":
    unittest.main()
