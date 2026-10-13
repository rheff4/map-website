"""Tests for routes/elevation.py. No network, no grid file needed.

    python -m unittest discover -s routes/tests -t .
"""

import unittest

from routes import elevation


def grid(values, west=0.0, south=0.0, dlon=1.0, dlat=1.0, scale=1.0):
    return elevation.Grid({
        "city": "test", "west": west, "south": south, "dlon": dlon, "dlat": dlat,
        "cols": len(values[0]), "rows": len(values), "scale": scale,
        "values": values, "source": "test",
    })


class GridLookup(unittest.TestCase):

    def setUp(self):
        # rows run south to north
        self.g = grid([[0, 10, 20],
                       [10, 20, 30],
                       [20, 30, 40]])

    def test_exact_grid_points(self):
        self.assertEqual(self.g.at(0, 0), 0)
        self.assertEqual(self.g.at(2, 0), 20)
        self.assertEqual(self.g.at(0, 2), 20)
        self.assertEqual(self.g.at(2, 2), 40)

    def test_bilinear_between_points(self):
        # This grid is the plane height = 10*lon + 10*lat, which bilinear
        # interpolation must reproduce exactly anywhere inside it.
        for lon, lat in [(0.5, 0.5), (1.5, 0.25), (0.2, 1.9), (1.99, 1.01)]:
            self.assertAlmostEqual(self.g.at(lon, lat), 10 * lon + 10 * lat)

    def test_far_edges_are_inside(self):
        # The last row and column are valid, not off-by-one misses.
        self.assertAlmostEqual(self.g.at(2, 1.5), 35)
        self.assertAlmostEqual(self.g.at(1.5, 2), 35)

    def test_off_grid_is_none(self):
        for lon, lat in [(-0.01, 1), (2.01, 1), (1, -0.01), (1, 2.01)]:
            self.assertIsNone(self.g.at(lon, lat))

    def test_a_missing_corner_makes_the_cell_unknown(self):
        g = grid([[0, 10], [None, 20]])
        self.assertIsNone(g.at(0.5, 0.5))

    def test_scale_converts_stored_units(self):
        g = grid([[100, 100], [100, 100]], scale=0.1)   # decimetres
        self.assertAlmostEqual(g.at(0.5, 0.5), 10.0)


class Climb(unittest.TestCase):

    def test_wobble_below_threshold_is_ignored(self):
        self.assertEqual(elevation.climb([10, 10.4, 9.8, 10.3, 9.9, 10.2], threshold=1.0), (0.0, 0.0))

    def test_steady_climb_counts_in_full(self):
        up, down = elevation.climb([0, 1, 2, 3, 4, 5], threshold=1.0)
        self.assertEqual((up, down), (5.0, 0.0))

    def test_noise_does_not_inflate_a_real_hill(self):
        # A 10 m hill with half-metre noise on both slopes.
        noisy = [0, 0.4, 2, 1.6, 4, 3.6, 6, 5.6, 8, 7.6, 10, 9.6, 8, 8.4, 6, 6.4, 4, 4.4, 2, 2.4, 0]
        up, down = elevation.climb(noisy, threshold=1.0)
        self.assertEqual(up, 10.0)
        self.assertEqual(down, 10.0)

    def test_raw_sum_would_have_overcounted_the_same_hill(self):
        noisy = [0, 0.4, 2, 1.6, 4, 3.6, 6, 5.6, 8, 7.6, 10, 9.6, 8, 8.4, 6, 6.4, 4, 4.4, 2, 2.4, 0]
        raw = sum(max(0, b - a) for a, b in zip(noisy, noisy[1:]))
        self.assertGreater(raw, 10.0)          # why the threshold exists

    def test_empty(self):
        self.assertEqual(elevation.climb([]), (0.0, 0.0))


class Resample(unittest.TestCase):

    def test_points_are_evenly_spaced_by_distance(self):
        # ~1 km due north; uneven input spacing must not matter.
        line = [[-71.0, 42.0], [-71.0, 42.001], [-71.0, 42.009]]
        points, length = elevation.resample(line, step=100)
        self.assertAlmostEqual(length, 1000.8, delta=1.0)
        self.assertEqual(len(points), 11)
        gaps = [elevation._distance(a, b) for a, b in zip(points, points[1:])]
        for gap in gaps:
            self.assertAlmostEqual(gap, 100, delta=0.5)


class Measure(unittest.TestCase):

    def test_route_fully_on_the_grid(self):
        g = grid([[0, 0], [0, 0]], west=-71.01, south=41.99, dlon=0.02, dlat=0.02)
        m = elevation.measure(g, [[-71.0, 42.0], [-71.0, 42.005]])
        self.assertEqual(m["coverage"], 1.0)
        self.assertTrue(m["complete"])
        self.assertEqual(m["ascent"], 0.0)

    def test_route_leaving_the_grid_is_flagged_partial(self):
        # The grid stops at 42.005; the route runs on to 42.01.
        g = grid([[0, 0], [0, 0]], west=-71.01, south=41.995, dlon=0.02, dlat=0.01)
        m = elevation.measure(g, [[-71.0, 42.0], [-71.0, 42.01]])
        self.assertLess(m["coverage"], elevation.MIN_COVERAGE)
        self.assertFalse(m["complete"])

    def test_no_grid_means_no_numbers_not_zeros(self):
        m = elevation.measure(None, [[-71.0, 42.0], [-71.0, 42.01]])
        self.assertEqual(m["coverage"], 0.0)
        self.assertFalse(m["complete"])
        self.assertIsNone(m["min"])


if __name__ == "__main__":
    unittest.main()
