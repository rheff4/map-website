"""
Elevation for routes, from a precomputed USGS 3DEP grid.

OpenRouteService returns elevation with every route, but in Boston it reads
about 8 m high on average with a noisy profile, and in flat areas it ranks
candidate routes in the wrong order - the "flattest" it picks is not the
flattest on the ground. So routes take their heights from a grid of USGS
lidar-derived elevations instead, built once per city by
routes/tools/build_elevation_grid.py and stored in data/elevation/<city>.json.

    grid = elevation.load('boston')          # None if the city has no grid
    grid.at(lon, lat)                        # metres, or None off the grid
    elevation.measure(grid, coords)          # ascent, descent, coverage, ...

Standard library only, like the rest of the server.
"""

import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID_DIR = os.path.join(ROOT, "data", "elevation")

# Distance between height samples along a route. The grid itself is 50 m;
# sampling more finely than that adds no information, coarser loses hills.
SAMPLE_STEP_M = 25

# A climb only counts once it rises this far above the last low point. The
# grid is already smooth, so this just drops sub-metre interpolation ripple.
# Validated against direct USGS lookups - see routes/README.md.
CLIMB_THRESHOLD_M = 1.0

# Below this share of route covered by the grid, the numbers are reported but
# flagged as partial, so the UI can say so instead of showing a false figure.
MIN_COVERAGE = 0.95

EARTH_R = 6371008.8

_cache = {}


class Grid:
    """A regular lon/lat grid of heights. Rows run south to north."""

    def __init__(self, data):
        self.city = data["city"]
        self.west = data["west"]
        self.south = data["south"]
        self.dlon = data["dlon"]
        self.dlat = data["dlat"]
        self.cols = data["cols"]
        self.rows = data["rows"]
        self.scale = data.get("scale", 0.1)       # stored value -> metres
        self.values = data["values"]
        self.source = data.get("source", "")

    def at(self, lon, lat):
        """Height in metres by bilinear interpolation, or None if the point
        is off the grid or touches a cell with no data."""
        fx = (lon - self.west) / self.dlon
        fy = (lat - self.south) / self.dlat
        if fx < 0 or fy < 0 or fx > self.cols - 1 or fy > self.rows - 1:
            return None
        i = min(int(fx), self.cols - 2)
        j = min(int(fy), self.rows - 2)
        tx, ty = fx - i, fy - j
        v00 = self.values[j][i]
        v10 = self.values[j][i + 1]
        v01 = self.values[j + 1][i]
        v11 = self.values[j + 1][i + 1]
        if v00 is None or v10 is None or v01 is None or v11 is None:
            return None
        h = (v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
             + v01 * (1 - tx) * ty + v11 * tx * ty)
        return h * self.scale


def load(city_key):
    """The grid for a city, or None if it has not been built. Cached."""
    if city_key not in _cache:
        path = os.path.join(GRID_DIR, "%s.json" % city_key)
        if not os.path.exists(path):
            _cache[city_key] = None
        else:
            with open(path, encoding="utf-8") as f:
                _cache[city_key] = Grid(json.load(f))
    return _cache[city_key]


def _distance(a, b):
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = p2 - p1, math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def resample(coords, step=SAMPLE_STEP_M):
    """Points every `step` metres along a [lon, lat, ...] line, plus its
    length. Routing engines space points unevenly - dense on curves, sparse
    on straights - so heights must be sampled by distance, not by vertex."""
    if len(coords) < 2:
        return [tuple(coords[0][:2])] if coords else [], 0.0
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + _distance(a, b))
    out, j, x = [], 0, 0.0
    while x <= cum[-1]:
        while j < len(cum) - 2 and cum[j + 1] < x:
            j += 1
        span = (cum[j + 1] - cum[j]) or 1.0
        t = (x - cum[j]) / span
        a, b = coords[j], coords[j + 1]
        out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        x += step
    return out, cum[-1]


def climb(heights, threshold=CLIMB_THRESHOLD_M):
    """(ascent, descent) in metres, counting a rise or fall only once it
    exceeds `threshold` from the last turning point. Without this, tiny
    wobbles add up: the same Boston loop measures 114 m raw and 41 m true."""
    if not heights:
        return 0.0, 0.0
    ascent = descent = 0.0
    ref = heights[0]
    for h in heights[1:]:
        if h - ref >= threshold:
            ascent += h - ref
            ref = h
        elif ref - h >= threshold:
            descent += ref - h
            ref = h
    return ascent, descent


def measure(grid, coords, step=SAMPLE_STEP_M, threshold=CLIMB_THRESHOLD_M):
    """Elevation summary for a route.

    Heights the grid cannot answer are skipped, and `coverage` says what
    share of the route was measured. `complete` is False below MIN_COVERAGE:
    show the numbers as partial rather than as fact."""
    points, length = resample(coords, step)
    heights = [grid.at(lon, lat) for lon, lat in points] if grid else []
    known = [h for h in heights if h is not None]
    coverage = len(known) / len(points) if points else 0.0
    ascent, descent = climb(known, threshold)
    return {
        "ascent": round(ascent, 1),
        "descent": round(descent, 1),
        "min": round(min(known), 1) if known else None,
        "max": round(max(known), 1) if known else None,
        "length_m": round(length, 1),
        "coverage": round(coverage, 3),
        "complete": coverage >= MIN_COVERAGE,
        "source": grid.source if grid else None,
    }
