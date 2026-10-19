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


class Hills(unittest.TestCase):
    """_choose: distance first, then climb among the close candidates."""

    def setUp(self):
        self.climbs = {}
        p = mock.patch.object(api.elevation, "measure",
                              side_effect=lambda grid, coords: {"ascent": self.climbs[id(coords)]})
        p.start()
        self.addCleanup(p.stop)

    def cand(self, metres, climb):
        coords = [[-71.06, 42.35], [-71.05, 42.36]]
        self.climbs[id(coords)] = climb
        return coords, float(metres)

    def test_flat_and_hilly_pick_by_climb(self):
        cands = [self.cand(5000, 40), self.cand(5200, 15), self.cand(4900, 70)]
        (_, m), e, choice = api._choose(cands, 5000, "flat", grid=object())
        self.assertEqual(e["ascent"], 15)
        (_, m), e, _ = api._choose(cands, 5000, "hilly", grid=object())
        self.assertEqual(e["ascent"], 70)
        self.assertEqual(choice["compared"], 3)
        self.assertFalse(choice["similar"])

    def test_never_trades_distance_for_hills(self):
        cands = [self.cand(5000, 40), self.cand(8000, 200)]
        (_, m), _, _ = api._choose(cands, 5000, "hilly", grid=object())
        self.assertEqual(m, 5000)

    def test_balanced_keeps_the_closest(self):
        cands = [self.cand(5300, 10), self.cand(5050, 90)]
        (_, m), _, _ = api._choose(cands, 5000, "balanced", grid=object())
        self.assertEqual(m, 5050)

    def test_says_when_routes_barely_differ(self):
        cands = [self.cand(5000, 40), self.cand(5100, 44)]
        _, _, choice = api._choose(cands, 5000, "flat", grid=object())
        self.assertTrue(choice["similar"])

    def test_exploring_loops_always_asks_a_second_round(self):
        with mock.patch.object(api, "_ors", side_effect=lambda k, b: leg(5000)):
            routes, calls = api._loop_candidates("key", [-71.06, 42.35], 5000, 1, explore=True)
        self.assertEqual(calls, 6)
        self.assertEqual(len(routes), 6)

    def test_exploring_out_and_back_tries_other_directions(self):
        with mock.patch.object(api, "_ors", return_value=leg(2500)):
            routes, calls = api._out_and_back_candidates("key", [-71.06, 42.35], 5000, 1, explore=True)
        self.assertEqual(len(routes), 4)
        self.assertLessEqual(calls, api.MAX_CALLS)


class RunTo(unittest.TestCase):
    START, DEST = [-71.0656, 42.3550], [-71.0550, 42.3640]

    def test_shorter_than_the_direct_route_gets_the_direct_route(self):
        with mock.patch.object(api, "_ors", return_value=leg(1800)) as ors:
            routes, calls, direct = api._run_to("key", self.START, self.DEST, 1000, 1)
        self.assertEqual(direct, 1800)
        self.assertEqual([m for _, m in routes], [1800])
        self.assertEqual(calls, 1)
        self.assertEqual(ors.call_args.args[1]["coordinates"], [self.START, self.DEST])

    def test_longer_runs_bend_out_through_an_arc_of_waypoints(self):
        def fake(key, body):
            return leg(1800 if len(body["coordinates"]) == 2 else 5100)
        with mock.patch.object(api, "_ors", side_effect=fake) as ors:
            routes, calls, _ = api._run_to("key", self.START, self.DEST, 5000, 1)
        self.assertEqual([round(m) for _, m in routes], [5100])
        self.assertEqual(calls, 2)
        coords = ors.call_args.args[1]["coordinates"]
        self.assertEqual((coords[0], coords[-1]), (self.START, self.DEST))
        self.assertEqual(len(coords), 2 + len(api.RUN_TO_ARC))

    def test_arc_waypoints_add_up_to_the_distance_on_one_side(self):
        straight = api._haversine(self.START, self.DEST)
        for path_m in (1.5 * straight, 4 * straight):
            for side in (1, -1):
                pts = api._arc_waypoints(self.START, self.DEST, path_m, side)
                line = [self.START] + pts + [self.DEST]
                total = sum(api._haversine(a, b) for a, b in zip(line, line[1:]))
                self.assertAlmostEqual(total, path_m, delta=1)
                # All on the requested side of the way there.
                toward = api._bearing(self.START, self.DEST)
                for p in pts:
                    turn = (api._bearing(self.START, p) - toward + 540) % 360 - 180
                    self.assertGreater(turn * side, -1e-6)

    def test_arc_waypoints_never_overshoot_the_destination(self):
        # A long run on a short gap must loop round, not run past and back:
        # the last waypoint is nearer the destination than the first.
        pts = api._arc_waypoints(self.START, self.DEST, 6000, 1)
        self.assertLess(api._haversine(pts[-1], self.DEST), api._haversine(pts[0], self.DEST))

    def test_spurs_to_a_waypoint_are_cut(self):
        a, b, c, d = [0.0, 0.0], [0.001, 0.0], [0.002, 0.0], [0.001, 0.001]
        coords, metres = api._trim_spurs([a, b, c, c, b, d], 400.0)
        self.assertEqual(coords, [a, b, d])
        self.assertAlmostEqual(metres, 200.0, delta=1)

    def test_a_route_without_spurs_is_untouched(self):
        route = [[0.0, 0.0], [0.001, 0.0], [0.001, 0.001]]
        self.assertEqual(api._trim_spurs(route, 222.0), (route, 222.0))

    def test_waypoint_in_the_water_tries_the_other_side(self):
        sides = []

        def fake(key, body):
            if len(body["coordinates"]) == 2:
                return leg(1800)
            sides.append(body["coordinates"][1])
            if len(sides) == 1:
                raise ApiError(422, "There's no walkable path near that start point.")
            return leg(5000)
        with mock.patch.object(api, "_ors", side_effect=fake):
            routes, _, _ = api._run_to("key", self.START, self.DEST, 5000, 1)
        self.assertEqual([m for _, m in routes], [5000])
        self.assertEqual(len(sides), 2)

    def test_never_exceeds_the_call_cap(self):
        def fake(key, body):
            return leg(1800 if len(body["coordinates"]) == 2 else 9000)
        with mock.patch.object(api, "_ors", side_effect=fake):
            _, calls, _ = api._run_to("key", self.START, self.DEST, 5000, 1, explore=True)
        self.assertLessEqual(calls, api.MAX_CALLS)

    def test_needs_a_destination_inside_the_city(self):
        with self.assertRaises(ApiError) as cm:
            api.generate(request(shape="to", to_lon="-71.30", to_lat="42.36"))
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(ApiError):
            api.generate(request(shape="to"))

    def test_bad_hills(self):
        with self.assertRaises(ApiError) as cm:
            api.generate(request(hills="steep"))
        self.assertEqual(cm.exception.status, 400)


class RateLimits(unittest.TestCase):
    """How generate() applies routes/limits.py. ORS and the clock are fake."""

    def setUp(self):
        from routes import limits
        self.limits = limits
        self.now = [1000.0]
        clock = lambda: self.now[0]                     # noqa: E731
        self.budget = limits.OrsBudget(per_minute=40, per_day=2000, clock=clock)
        self.visitors = limits.VisitorLimit(per_minute=5, per_day=50, clock=clock)
        self.loop = mock.Mock(return_value=([([[-71.06, 42.35], [-71.05, 42.36]], 5000.0)], 3))
        for target, value in [("BUDGET", self.budget), ("VISITORS", self.visitors),
                              ("_loop_candidates", self.loop)]:
            p = mock.patch.object(api, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(api.env, "secret", return_value="key")
        p.start()
        self.addCleanup(p.stop)

    def generate(self, client="1.2.3.4"):
        return api.generate(Request("GET", "/api/routes/generate", dict(COMMON), None, client=client))

    def calls_held(self):
        return len(self.budget.minute.events)

    def test_a_route_holds_only_the_calls_it_made(self):
        self.generate()
        self.assertEqual(self.calls_held(), 3)          # reserved 6, used 3

    def test_fast_visitor_gets_429_and_costs_the_site_nothing_extra(self):
        for _ in range(5):
            self.generate()
        with self.assertRaises(ApiError) as cm:
            self.generate()
        self.assertEqual(cm.exception.status, 429)
        self.assertIn("Try again in", cm.exception.message)
        self.assertEqual(self.calls_held(), 15)         # the refused click was refunded

    def test_other_visitors_are_unaffected(self):
        for _ in range(5):
            self.generate("1.2.3.4")
        self.generate("5.6.7.8")

    def test_busy_site_gets_503_and_does_not_charge_the_visitor(self):
        for _ in range(6):
            self.budget.reserve(6)                       # all 36 held by others
        with self.assertRaises(ApiError) as cm:
            self.generate()
        self.assertEqual(cm.exception.status, 503)
        self.assertNotIn("1.2.3.4", self.visitors.visitors)

    def test_failed_generation_keeps_its_whole_reservation(self):
        # Unknown how many ORS calls were made before it failed: assume all.
        self.loop.side_effect = ApiError(502, "upstream trouble")
        with self.assertRaises(ApiError):
            self.generate()
        self.assertEqual(self.calls_held(), api.MAX_CALLS)

    def test_bad_input_costs_nothing(self):
        with self.assertRaises(ApiError):
            api.generate(Request("GET", "/api/routes/generate",
                                 dict(COMMON, shape="spiral"), None, client="1.2.3.4"))
        self.assertEqual(self.calls_held(), 0)
        self.assertNotIn("1.2.3.4", self.visitors.visitors)


if __name__ == "__main__":
    unittest.main()
