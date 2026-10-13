"""Tests for routes/api.py. ORS is faked - no network, no key, no quota.

    python -m unittest discover -s routes/tests -t .
"""

import unittest
from unittest import mock

from routes import api
from server.router import ApiError, Request

COMMON = {"lon": "-71.0656", "lat": "42.3550"}


def request(**query):
    return Request("GET", "/api/routes/generate", dict(COMMON, **query), None)


def leg(length_m, start=(-71.0656, 42.3550)):
    """A fake ORS result: a short line and its reported length."""
    return [list(start), [start[0] + 0.001, start[1] + 0.001]], float(length_m)


class Validation(unittest.TestCase):

    def test_bad_shape(self):
        with self.assertRaises(ApiError) as cm:
            api.generate(request(shape="spiral"))
        self.assertEqual(cm.exception.status, 400)

    def test_start_outside_the_city(self):
        with self.assertRaises(ApiError) as cm:
            api.generate(request(lon="-71.30", lat="42.36"))
        self.assertEqual(cm.exception.status, 400)
        self.assertIn("outside Boston", cm.exception.message)

    def test_distance_limits(self):
        for km in ("0.5", "50"):
            with self.assertRaises(ApiError):
                api.generate(request(km=km))

    def test_no_key_is_501_so_the_page_can_fall_back(self):
        with mock.patch.object(api.env, "secret", return_value=None):
            with self.assertRaises(ApiError) as cm:
                api.generate(request())
        self.assertEqual(cm.exception.status, 501)
        self.assertIn("ORS_API_KEY", cm.exception.message)


class OrsRequest(unittest.TestCase):

    def fake_response(self):
        return {"features": [{
            "geometry": {"coordinates": [[-71.06, 42.35, 9.0], [-71.05, 42.36, 9.5]]},
            "properties": {"summary": {"distance": 5000}},
        }]}

    def test_ferries_are_always_avoided(self):
        # ORS's walking profile routes over ferries: 36 km of a "5 km" loop
        # near the Seaport was by boat.
        with mock.patch.object(api, "request_json", return_value=self.fake_response()) as rj:
            api._ors("key", {"coordinates": [[-71.06, 42.35]],
                             "options": {"round_trip": {"length": 5000, "seed": 1}}})
        body = rj.call_args.kwargs["body"]
        self.assertEqual(body["options"]["avoid_features"], ["ferries"])
        self.assertIn("round_trip", body["options"])          # merged, not replaced

    def test_coordinates_come_back_two_dimensional(self):
        with mock.patch.object(api, "request_json", return_value=self.fake_response()):
            coords, metres = api._ors("key", {"coordinates": [[-71.06, 42.35]]})
        self.assertEqual(coords, [[-71.06, 42.35], [-71.05, 42.36]])
        self.assertEqual(metres, 5000.0)

    def test_errors_become_actionable_messages(self):
        cases = [
            (api.UpstreamError("x", status=403), 502, "API key"),
            (api.UpstreamError("x", status=429), 503, "quota"),
            (api.UpstreamError("Could not find routable point within 350 m", status=404), 422, "walkable"),
        ]
        for upstream, status, phrase in cases:
            err = api._explain(upstream)
            self.assertEqual(err.status, status)
            self.assertIn(phrase, err.message)


class Loops(unittest.TestCase):

    def test_keeps_the_candidate_closest_to_the_length(self):
        lengths = iter([5900, 5100, 4300])
        with mock.patch.object(api, "_ors", side_effect=lambda k, b: leg(next(lengths))):
            _, actual, calls = api._best_loop("key", [-71.06, 42.35], 5000, seed=1)
        self.assertEqual(actual, 5100)
        self.assertEqual(calls, 3)                              # close enough: no retry

    def test_asks_again_for_less_when_every_candidate_is_too_long(self):
        asked = []

        def fake(key, body):
            ask = body["options"]["round_trip"]["length"]
            asked.append(ask)
            return leg(8000 if ask == 5000 else 5200)
        with mock.patch.object(api, "_ors", side_effect=fake):
            _, actual, calls = api._best_loop("key", [-71.06, 42.35], 5000, seed=1)
        self.assertEqual(actual, 5200)
        self.assertEqual(calls, 6)
        self.assertTrue(all(a < 5000 for a in asked[3:]))       # second round asked for less

    def test_never_exceeds_the_call_cap(self):
        with mock.patch.object(api, "_ors", side_effect=lambda k, b: leg(9000)):
            _, _, calls = api._best_loop("key", [-71.06, 42.35], 5000, seed=1)
        self.assertLessEqual(calls, api.MAX_CALLS)

    def test_bad_start_point_reports_the_reason(self):
        err = ApiError(422, "There's no walkable path near that start point.")
        with mock.patch.object(api, "_ors", side_effect=err):
            with self.assertRaises(ApiError) as cm:
                api._best_loop("key", [-71.06, 42.35], 5000, seed=1)
        self.assertEqual(cm.exception.status, 422)


class OutAndBack(unittest.TestCase):

    def test_route_retraces_its_outward_leg(self):
        with mock.patch.object(api, "_ors", return_value=leg(2500)):
            coords, total, _ = api._out_and_back("key", [-71.06, 42.35], 5000, seed=1)
        n = len(coords)
        for i in range(n):
            self.assertEqual(coords[i], coords[n - 1 - i])
        self.assertEqual(total, 5000)

    def test_moves_on_when_a_direction_is_blocked(self):
        # Aiming across the harbour: ORS snaps the turnaround to the same spot
        # on the shore however far you aim, so the leg never grows. It should
        # stop re-aiming that way and try another direction.
        directions = []

        def fake(key, body):
            target = body["coordinates"][1]
            east = target[0] > -71.06 + 1e-9
            directions.append("east" if east else "west")
            return leg(1200 if east else 2500)      # east is blocked by water
        # seed 1 aims at 67 deg then 157 deg (both eastward), then 247 (west).
        with mock.patch.object(api, "_ors", side_effect=fake):
            _, total, calls = api._out_and_back("key", [-71.06, 42.35], 5000, seed=1)
        self.assertEqual(directions[0], "east")                 # really starts blocked
        self.assertEqual(directions.count("east"), 4)           # two tries per blocked way, not three
        self.assertEqual(directions[-1], "west")
        self.assertEqual(total, 5000)
        self.assertLessEqual(calls, api.MAX_CALLS)

    def test_unroutable_turnaround_tries_another_direction(self):
        replies = iter([ApiError(422, "no route"), leg(2500)])

        def fake(key, body):
            r = next(replies)
            if isinstance(r, ApiError):
                raise r
            return r
        with mock.patch.object(api, "_ors", side_effect=fake):
            _, total, _ = api._out_and_back("key", [-71.06, 42.35], 5000, seed=1)
        self.assertEqual(total, 5000)

    def test_never_exceeds_the_call_cap(self):
        with mock.patch.object(api, "_ors", side_effect=lambda k, b: leg(400)):
            _, _, calls = api._out_and_back("key", [-71.06, 42.35], 5000, seed=1)
        self.assertLessEqual(calls, api.MAX_CALLS)


if __name__ == "__main__":
    unittest.main()
