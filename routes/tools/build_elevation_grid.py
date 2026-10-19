"""
Build a city's elevation grid from USGS 3DEP lidar-derived heights.

    python -m routes.tools.build_elevation_grid               # default city
    python -m routes.tools.build_elevation_grid --city boston
    python -m routes.tools.build_elevation_grid --dry-run     # size only

Samples the USGS 3DEP elevation service on a regular grid over the city's
bounds (shared/cities.json) and writes data/elevation/<city>.json, which
routes/elevation.py reads at runtime. Run it once per city, and again only if
the bounds or the grid step change - terrain does not.

Coverage requirements: some cities also need specific streets covered even if
they fall outside the city's bounds. Boston requires every street and path in
Cambridge within 100 m of the Charles River bank, so runners can follow the
river. Those streets are fetched from OpenStreetMap, the grid is extended to
contain them if necessary, and the build fails unless every one of them can be
measured.

US only: 3DEP covers the United States. Elsewhere, routes fall back to the
routing engine's own elevation.
"""

import argparse
import datetime
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from routes import elevation
from server import cities
from server.fetch import USER_AGENT, UpstreamError

USGS_SAMPLES = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
                "3DEPElevation/ImageServer/getSamples")
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter")

DEFAULT_STEP_M = 50
BATCH = 1000          # points per USGS request; ~2 s each
NODATA_BELOW = -1000  # USGS marks missing cells with large negative values


# ----------------------------------------------------------- coverage rules

# OSM names only the Charles's centreline; the water polygons forming its
# banks are unnamed. So the river is "the water polygons the centreline runs
# through", and distance is measured from their edges. Measuring from the
# centreline instead would miss the Cambridge shore entirely - the Basin is
# up to ~600 m wide.
CAMBRIDGE_RIVERSIDE = {
    "label": "Cambridge streets and paths within 100 m of the Charles River bank",
    "buffer_m": 100,
    "query": """[out:json][timeout:150][bbox:42.33,-71.20,42.41,-71.05];
way["waterway"="river"]["name"="Charles River"]->.cl;
.cl out geom;
(way["natural"="water"]; rel["natural"="water"];)->.water;
.water out geom;
area["admin_level"="8"]["name"="Cambridge"]["wikipedia"="en:Cambridge, Massachusetts"]->.cam;
way(area.cam)["highway"](around.cl:800);
out geom;""",
}

REQUIREMENTS = {
    "boston": [CAMBRIDGE_RIVERSIDE],
}


# ------------------------------------------------------------------ network

def post_form(url, fields, timeout=180):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    host = urllib.parse.urlparse(url).netloc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise UpstreamError("%s returned HTTP %d" % (host, e.code), status=e.code)
    except (urllib.error.URLError, TimeoutError) as e:
        raise UpstreamError("Could not reach %s (%s)" % (host, getattr(e, "reason", e)))


def with_retries(fn, attempts=5, label="request"):
    for n in range(attempts):
        try:
            return fn()
        except UpstreamError as e:
            if n == attempts - 1:
                raise
            wait = 5 * (n + 1)
            print("    %s failed (%s); retrying in %ds" % (label, e, wait))
            time.sleep(wait)


def overpass(query):
    """Overpass is a shared free service that 504s under load: try each
    mirror, back off, and try again."""
    def attempt():
        last = None
        for url in OVERPASS:
            try:
                return post_form(url, {"data": query})["elements"]
            except UpstreamError as e:
                last = e
        raise last
    return with_retries(attempt, label="Overpass")


def usgs_heights(points):
    """Heights in metres for up to BATCH [lon, lat] points; None where USGS
    has no data."""
    resp = post_form(USGS_SAMPLES, {
        "geometry": json.dumps({"points": points, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryMultipoint",
        "returnFirstValueOnly": "true",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "json",
    })
    if "samples" not in resp:
        raise UpstreamError("USGS returned no samples: %s" % json.dumps(resp)[:200])
    out = [None] * len(points)
    for s in resp["samples"]:
        try:
            v = float(s["value"])
        except (TypeError, ValueError):
            continue                       # "NoData"
        if v > NODATA_BELOW:
            out[s["locationId"]] = v
    return out


# ------------------------------------------------- riverside street geometry

def _projector(lat0):
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 110574
    return lambda lon, lat: (lon * kx, lat * ky)


def _stitch(fragments):
    """Join multipolygon member ways into closed rings. Large OSM water areas
    are stored as many open fragments sharing endpoints; testing only pieces
    that are already closed silently skips most of the river."""
    pieces = [list(f) for f in fragments if len(f) > 1]
    rings = []
    while pieces:
        ring = pieces.pop(0)
        grew = True
        while ring[0] != ring[-1] and grew:
            grew = False
            for i, f in enumerate(pieces):
                if f[0] == ring[-1]:
                    ring += f[1:]
                elif f[-1] == ring[-1]:
                    ring += f[::-1][1:]
                elif f[-1] == ring[0]:
                    ring = f[:-1] + ring
                elif f[0] == ring[0]:
                    ring = f[::-1][:-1] + ring
                else:
                    continue
                pieces.pop(i)
                grew = True
                break
        if len(ring) > 3 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def _inside(p, rings):
    """Even-odd point-in-polygon, so inner rings (islands) count as holes."""
    x, y = p
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def riverside_ways(rule):
    """The OSM ways satisfying a riverside coverage rule, as [lon, lat] lists."""
    elements = overpass(rule["query"])
    xy = _projector(42.36)

    centre, polygons, roads = [], [], []
    for e in elements:
        tags = e.get("tags", {})
        if e["type"] == "way" and tags.get("waterway") == "river":
            centre += [xy(g["lon"], g["lat"]) for g in e["geometry"]]
        elif tags.get("natural") == "water":
            if e["type"] == "way":
                frags = [e["geometry"]]
            else:
                frags = [m["geometry"] for m in e.get("members", [])
                         if m.get("type") == "way" and m.get("geometry")]
            polygons.append([[xy(g["lon"], g["lat"]) for g in f] for f in frags])
        elif "highway" in tags:
            roads.append(e)

    if not centre:
        raise SystemExit("Coverage rule found no river centreline - has OSM tagging changed?")

    probe = centre[::max(1, len(centre) // 400)]
    river = [frags for frags in polygons
             if sum(_inside(p, _stitch(frags)) for p in probe) >= 3]
    bank = [(f[i], f[i + 1]) for frags in river for f in frags for i in range(len(f) - 1)]

    # Bucket bank segments so each street point is checked against few of them.
    cell = 2.0 * rule["buffer_m"]
    buckets = {}
    for seg in bank:
        (x1, y1), (x2, y2) = seg
        for cx in range(int(min(x1, x2) // cell) - 1, int(max(x1, x2) // cell) + 2):
            for cy in range(int(min(y1, y2) // cell) - 1, int(max(y1, y2) // cell) + 2):
                buckets.setdefault((cx, cy), []).append(seg)

    def near_bank(p):
        x, y = p
        for (x1, y1), (x2, y2) in buckets.get((int(x // cell), int(y // cell)), ()):
            dx, dy = x2 - x1, y2 - y1
            length2 = dx * dx + dy * dy
            t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length2))
            if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) <= rule["buffer_m"]:
                return True
        return False

    def densify(pts, every=10.0):
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // every))
            out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                    for k in range(1, n + 1)]
        return out

    # A street counts if any part of it is within the buffer.
    chosen = [r for r in roads
              if any(near_bank(p) for p in densify([xy(g["lon"], g["lat"]) for g in r["geometry"]]))]
    return [[[g["lon"], g["lat"]] for g in r["geometry"]] for r in chosen], len(river)


# -------------------------------------------------------------------- build

def grid_shape(bounds, step_m):
    (west, south), (east, north) = bounds
    lat0 = (south + north) / 2
    dlat = step_m / 110574.0
    dlon = step_m / (111320.0 * math.cos(math.radians(lat0)))
    cols = int(math.ceil((east - west) / dlon)) + 1
    rows = int(math.ceil((north - south) / dlat)) + 1
    return dlon, dlat, cols, rows


def extend(bounds, ways, margin):
    (west, south), (east, north) = bounds
    for way in ways:
        for lon, lat in way:
            west, east = min(west, lon - margin[0]), max(east, lon + margin[0])
            south, north = min(south, lat - margin[1]), max(north, lat + margin[1])
    return [[west, south], [east, north]]


def write_grid(path, header, values):
    """One grid row per line: still a valid JSON file, but diffs and a quick
    look in an editor stay readable."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = json.dumps(header, indent=2)[:-2]          # drop the closing "\n}"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + ',\n  "values": [\n')
        for j, row in enumerate(values):
            f.write("    " + json.dumps(row, separators=(",", ":")))
            f.write(",\n" if j < len(values) - 1 else "\n")
        f.write("  ]\n}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", help="city key in shared/cities.json (default: the default city)")
    ap.add_argument("--step", type=float, default=DEFAULT_STEP_M, help="grid spacing in metres")
    ap.add_argument("--dry-run", action="store_true", help="report the grid size and stop")
    args = ap.parse_args(argv)

    city = cities.get(args.city)
    key = city["key"]
    bounds = [list(city["bounds"][0]), list(city["bounds"][1])]
    print("Elevation grid for %s, %g m step" % (city["name"], args.step))

    covered = []
    for rule in REQUIREMENTS.get(key, []):
        print("  coverage rule: %s" % rule["label"])
        ways, n_river = riverside_ways(rule)
        if not ways:
            raise SystemExit("  rule matched no streets - refusing to build without it")
        dlon, dlat, _, _ = grid_shape(bounds, args.step)
        before = json.dumps(bounds)
        bounds = extend(bounds, ways, (dlon, dlat))
        print("    %d ways along %d river polygons; bounds %s" % (
            len(ways), n_river, "unchanged" if json.dumps(bounds) == before else "extended to include them"))
        covered.append((rule, ways))

    dlon, dlat, cols, rows = grid_shape(bounds, args.step)
    total = cols * rows
    print("  grid: %d x %d = %d points, %d USGS requests" % (cols, rows, total, -(-total // BATCH)))
    if args.dry_run:
        return 0

    (west, south), _ = bounds
    flat = [[west + i * dlon, south + j * dlat] for j in range(rows) for i in range(cols)]
    heights = []
    started = time.time()
    for b in range(0, total, BATCH):
        batch = flat[b:b + BATCH]
        heights += with_retries(lambda: usgs_heights(batch), label="USGS batch %d" % (b // BATCH + 1))
        done = len(heights)
        if (b // BATCH) % 10 == 9 or done == total:
            rate = done / (time.time() - started)
            print("    %6d / %d  (%.0f s left)" % (done, total, (total - done) / rate))

    missing = sum(h is None for h in heights)
    values = [[None if h is None else int(round(h * 10)) for h in heights[j * cols:(j + 1) * cols]]
              for j in range(rows)]

    header = {
        "_readme": [
            "Ground elevation grid for routes/elevation.py. Generated - do not edit by hand.",
            "Rebuild: python -m routes.tools.build_elevation_grid --city %s" % key,
            "values[row][col] is height in decimetres (multiply by scale for metres); rows run south to north.",
            "Point (col, row) is at lon = west + col*dlon, lat = south + row*dlat. null = no data.",
        ],
        "city": key,
        "source": "USGS 3DEP (3DEPElevation ImageServer, bilinear)",
        "built": datetime.date.today().isoformat(),
        "step_m": args.step,
        "west": west, "south": south, "dlon": dlon, "dlat": dlat,
        "cols": cols, "rows": rows, "scale": 0.1,
        "missing": missing,
        "coverage_rules": [{"label": rule["label"], "ways": len(ways)} for rule, ways in covered],
    }
    path = os.path.join(elevation.GRID_DIR, "%s.json" % key)
    write_grid(path, header, values)
    print("  wrote %s (%.0f KB, %d points without data)" % (
        os.path.relpath(path, elevation.ROOT), os.path.getsize(path) / 1024, missing))

    # The coverage rules are a promise: check every required point can be measured.
    elevation._cache.pop(key, None)
    grid = elevation.load(key)
    for rule, ways in covered:
        pts = [p for way in ways for p in way]
        bad = [p for p in pts if grid.at(p[0], p[1]) is None]
        print("  check: %s - %d / %d points measurable" % (rule["label"], len(pts) - len(bad), len(pts)))
        if bad:
            raise SystemExit("  coverage rule not met; first unmeasurable point: %s" % bad[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
