"""End-to-end search over the demo fixtures - no network, no keys."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from restaurants import api, service
from server.router import ApiError, Request

BOSTON_COMMON = (42.3550, -71.0656)
# Monday 5 Oct 2026, 19:00 in Boston.
MONDAY_EVENING = datetime(2026, 10, 5, 19, 0, tzinfo=timezone(timedelta(hours=-4)))

DEMO_ENV = {"RESTAURANTS_MOCK": "1"}


@mock.patch.dict(os.environ, DEMO_ENV)
class DemoSearchTest(unittest.TestCase):
    def setUp(self):
        self.result = service.search(*BOSTON_COMMON, radius_m=2000, now=MONDAY_EVENING)
        self.by_name = {r["name"]: r for r in self.result["results"]}

    def test_runs_in_demo_mode(self):
        self.assertTrue(self.result["demo"])
        self.assertEqual(self.result["sources"]["google"]["state"], "demo")

    def test_chains_and_closed_places_are_excluded(self):
        self.assertNotIn("Chipotle Mexican Grill", self.by_name)
        self.assertNotIn("Dunkin'", self.by_name)
        self.assertNotIn("Old Port Clam Shack", self.by_name)
        self.assertEqual(self.result["excluded"], {"chains": 2, "closed": 1})

    def test_google_and_yelp_duplicates_are_merged(self):
        self.assertNotIn("Lantern and Ladle", self.by_name)
        sources = {row.get("source") for row in self.by_name["Lantern & Ladle"]["breakdown"]}
        self.assertTrue({"google", "yelp", "eater_38"} <= sources)

    def test_recognised_place_missing_from_search_is_added(self):
        quiet = self.by_name["Quiet Harbor Kitchen"]
        self.assertTrue(quiet["recognised"])
        self.assertEqual(quiet["breakdown"][0]["source"], "michelin_selected")

    def test_ranked_by_score_and_led_by_multi_source_recognition(self):
        scores = [r["score"] for r in self.result["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        top = self.result["results"][0]
        self.assertTrue(top["recognised"])
        self.assertGreaterEqual(len({r["source"] for r in top["breakdown"]
                                     if r["kind"] == "recognition"}), 2)

    def test_few_reviews_earn_nothing(self):
        self.assertEqual(self.by_name["Tiny Dumpling Room"]["score"], 0)

    def test_open_now_uses_the_given_time(self):
        self.assertTrue(self.by_name["Lantern & Ladle"]["open_now"])        # 17-22 daily
        self.assertFalse(self.by_name["Rue Blanche Bistro"]["open_now"])    # closed Mondays
        self.assertIsNone(self.by_name["Sweet Basil Thai"]["open_now"])     # Yelp only

    def test_radius_limits_results(self):
        near = service.search(*BOSTON_COMMON, radius_m=500, now=MONDAY_EVENING)
        self.assertTrue(near["results"])
        self.assertTrue(all(r["distance_m"] <= 500 for r in near["results"]))


@mock.patch.dict(os.environ, {"RESTAURANTS_MOCK": "", "GOOGLE_PLACES_API_KEY": "your-key-here",
                              "YELP_API_KEY": ""})
class ModeTest(unittest.TestCase):
    def test_placeholder_keys_mean_demo_mode(self):
        self.assertTrue(service.mode()["demo"])


@mock.patch.dict(os.environ, DEMO_ENV)
class ApiTest(unittest.TestCase):
    def request(self, **query):
        return Request("GET", "/api/restaurants/search", {k: str(v) for k, v in query.items()}, None)

    def test_defaults_to_city_centre(self):
        result = api.get_search(self.request())
        self.assertEqual(result["city"]["key"], "boston")

    def test_rejects_points_outside_the_city(self):
        with self.assertRaises(ApiError) as ctx:
            api.get_search(self.request(lat=40.7128, lon=-74.0060))   # New York
        self.assertEqual(ctx.exception.status, 400)

    def test_rejects_bad_radius(self):
        with self.assertRaises(ApiError):
            api.get_search(self.request(radius=50000))
        with self.assertRaises(ApiError):
            api.get_search(self.request(radius="far"))


if __name__ == "__main__":
    unittest.main()
