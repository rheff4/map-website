"""
Route endpoints, loaded by server/app.py.

    GET /api/routes/generate?lon=&lat=&km=&shape=loop|out-and-back|to&seed=
                             &hills=flat|balanced|hilly&to_lon=&to_lat=
    GET /api/routes/toilets                 labelled public toilets, GeoJSON

shape=to runs one way from the start to a destination (a restaurant) - see
_run_to. hills picks among the candidates near the requested distance by
their climb on the USGS grid; it never trades away distance for hills.

Routes come from OpenRouteService's foot-walking profile, called from here so
the API key never reaches the browser. Climb comes from the USGS elevation
grid (routes/elevation.py), not from ORS - see routes/README.md for why.

Without ORS_API_KEY in .env this answers 501, and the page falls back to its
straight-line preview, so the tab still works on a machine with no key.
"""

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor

from routes import elevation, limits
from server import cities, env
from server.fetch import UpstreamError, request_json
from server.router import ApiError

ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"

MIN_KM, MAX_KM, DEFAULT_KM = 1, 42, 5

# How far a route may land from the requested distance, in metres - the same
# for every distance, so a 20 km run isn't allowed to be 2 km out the way a
# percentage would. Every "close enough" below is measured against this.
TOLERANCE_M = 500

# ORS's round trips miss the requested length, by up to ~18% in testing - far
# more where few streets connect, like the Seaport waterfront. Ask for a few
# at once with different seeds and keep the closest; if even that is more than
# TOLERANCE_M away, ask once more with the length scaled to compensate.
LOOP_CANDIDATES = 3

# Hard cap on ORS calls per click, against the free tier's 2,000 a day and 40
# a minute. 6 calls a click still allows 330+ routes a day.
MAX_CALLS = 6

# Streets run longer than the straight line between two points - roughly 30%
# longer in a city grid. Used to aim an out-and-back's turnaround point.
DETOUR = 1.3

# An out-and-back re-aims its turnaround until the whole run is within
# TOLERANCE_M (the leg within half that), up to OUT_AND_BACK_AIMS tries,
# keeping the best leg seen.
OUT_AND_BACK_AIMS = 3

# Hilliness only chooses among candidates within TOLERANCE_M of the requested
# distance (or within CLOSEST_SLACK_M of the closest, if none are): distance
# first.
HILLS = ("flat", "balanced", "hilly")
CLOSEST_SLACK_M = 200
# Below this spread in climb, the page says routes here are much the same.
SIMILAR_CLIMB_M = 10

RUN_TO_AIMS = 3

# A run to a destination bends out through waypoints along a circular arc
# from the start to the destination, on one side of the straight line. With
# one waypoint the two legs met at a point: a hairpin that read as running
# out and back. An ellipse around the two put its last waypoint beyond the
# destination, so runs overshot it and turned back. These are the waypoints'
# places along the arc, as fractions of its length.
RUN_TO_ARC = (0.25, 0.5, 0.75)


EARTH_R = 6371008.8

# One ORS key serves every visitor: see routes/limits.py.
BUDGET = limits.OrsBudget.from_env()
VISITORS = limits.VisitorLimit()


TOILETS_DIR = os.path.join(elevation.ROOT, "data", "toilets")
_toilets = {}   # city -> (file mtime, data)


def register(router):
    router.get("/api/routes/generate", generate)
    router.get("/api/routes/toilets", toilets)


# ------------------------------------------------------------------ toilets

def toilets(request):
    """The city's public toilets, each with a label. Built ahead of time by
    routes/tools/build_toilets.py; re-read whenever that file changes."""
    try:
        city = cities.get(request.query.get("city"))
    except KeyError as e:
        raise ApiError(400, e.args[0])
    path = os.path.join(TOILETS_DIR, "%s.json" % city["key"])
    if not os.path.exists(path):
        raise ApiError(404, "No toilet data for %s yet. Build it with: "
                            "python -m routes.tools.build_toilets --city %s"
                       % (city["name"], city["key"]))
    mtime = os.path.getmtime(path)
    cached = _toilets.get(city["key"])
    if not cached or cached[0] != mtime:
        with open(path, encoding="utf-8") as f:
            cached = _toilets[city["key"]] = (mtime, json.load(f))
    return cached[1]


# ------------------------------------------------------------------ handler

def generate(request):
    try:
        city = cities.get(request.query.get("city"))
    except KeyError as e:
        raise ApiError(400, e.args[0])

    lon = request.number("lon", default=city["center"][0], low=-180, high=180)
    lat = request.number("lat", default=city["center"][1], low=-90, high=90)
    km = request.number("km", default=DEFAULT_KM, low=MIN_KM, high=MAX_KM)
    seed = int(request.number("seed", default=1, low=0, high=1e6))
    shape = request.query.get("shape") or "loop"
    if shape not in ("loop", "out-and-back", "to"):
        raise ApiError(400, "Parameter 'shape' must be 'loop', 'out-and-back' or 'to'.")
    hills = request.query.get("hills") or "balanced"
    if hills not in HILLS:
        raise ApiError(400, "Parameter 'hills' must be 'flat', 'balanced' or 'hilly'.")
    if not cities.contains(city, lon, lat):
        raise ApiError(400, "That start point is outside %s. Choose a point inside the city."
                       % city["name"])
    dest = None
    if shape == "to":
        if "to_lon" not in request.query or "to_lat" not in request.query:
            raise ApiError(400, "A run to somewhere needs 'to_lon' and 'to_lat'.")
        dest = [request.number("to_lon", low=-180, high=180),
                request.number("to_lat", low=-90, high=90)]
        if not cities.contains(city, dest[0], dest[1]):
            raise ApiError(400, "That destination is outside %s." % city["name"])

    key = env.secret("ORS_API_KEY")
    if not key:
        raise ApiError(501, "Routing isn't set up on this server: add ORS_API_KEY to .env "
                            "(see .env.example) and restart.")

    # Shared quota first: if the site is busy, don't also charge the visitor.
    try:
        reserved = BUDGET.reserve(MAX_CALLS)
    except limits.Busy as busy:
        raise _site_busy(busy)
    try:
        VISITORS.check(request.client or "unknown")
    except limits.Busy as busy:
        BUDGET.settle(reserved, 0)
        raise _visitor_busy(busy)

    start = [lon, lat]
    length = km * 1000
    grid = elevation.load(city["key"])
    # Hills need candidates to choose between, so look wider - within the
    # same per-click call cap. Without a grid there is nothing to compare.
    explore = hills != "balanced" and grid is not None
    direct_m = None
    tried = MAX_CALLS          # if generation fails part-way, refund nothing
    try:
        if shape == "loop":
            candidates, tried = _loop_candidates(key, start, length, seed, explore)
        elif shape == "out-and-back":
            candidates, tried = _out_and_back_candidates(key, start, length, seed, explore)
        else:
            candidates, tried, direct_m = _run_to(key, start, dest, length, seed, explore)
    finally:
        BUDGET.settle(reserved, tried)

    (coords, actual), elev, choice = _choose(candidates, length, hills, grid)
    props = {
        "requestedM": length,
        "lengthM": round(actual, 1),
        "toleranceM": TOLERANCE_M,
        "shape": shape,
        "seed": seed,
        "hills": hills,
        "choice": choice,
        "candidates": tried,
        "ascent": elev["ascent"] if grid else None,
        "descent": elev["descent"] if grid else None,
        "elevation": elev,
        "source": "openrouteservice",
        "stub": False,
    }
    if shape == "to":
        # Too far: even the shortest way there runs past the requested
        # distance by more than the tolerance.
        props.update(destination=dest, directM=round(direct_m, 1),
                     tooFar=direct_m - length > TOLERANCE_M)
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": props,
    }


def _choose(candidates, length, hills, grid):
    """Pick one candidate: closest to the distance, or - for flat / hilly -
    the least / most climb among those close enough to it.

    -> ((coords, metres), elevation summary, what was compared)"""
    def off(c):
        return abs(c[1] - length)

    nearest = min(off(c) for c in candidates)
    near = [c for c in candidates if off(c) <= max(TOLERANCE_M, nearest + CLOSEST_SLACK_M)]
    measured = [(c, elevation.measure(grid, c[0])) for c in near]
    climbs = [e["ascent"] for _, e in measured]
    choice = {
        "compared": len(measured),
        "climbMin": min(climbs) if grid else None,
        "climbMax": max(climbs) if grid else None,
        "similar": None,
    }
    if hills == "balanced" or grid is None or len(measured) < 2:
        c, e = min(measured, key=lambda m: off(m[0]))
    else:
        choice["similar"] = max(climbs) - min(climbs) < SIMILAR_CLIMB_M
        pick = min if hills == "flat" else max
        c, e = pick(measured, key=lambda m: m[1]["ascent"])
    return c, e, choice


# ------------------------------------------------------------------- limits

def _wait(seconds):
    if seconds < 90:
        return "%d seconds" % seconds
    if seconds < 90 * 60:
        return "%d minutes" % round(seconds / 60)
    return "about %d hours" % round(seconds / 3600)


def _visitor_busy(busy):
    if busy.scope == "day":
        return ApiError(429, "You've reached today's limit of %d routes on this site. "
                             "Try again in %s." % (limits.VISITOR_PER_DAY, _wait(busy.retry_after)))
    return ApiError(429, "You're generating routes faster than this site allows. "
                         "Try again in %s." % _wait(busy.retry_after))


def _site_busy(busy):
    if busy.scope == "day":
        return ApiError(503, "This site has used today's route allowance. "
                             "Try again in %s." % _wait(busy.retry_after))
    return ApiError(503, "Lots of people are generating routes right now. "
                         "Try again in %s." % _wait(busy.retry_after))


# ---------------------------------------------------------------------- ORS

def _ors(key, body):
    """One ORS request -> ([[lon, lat], ...], metres). Errors become messages
    a runner can act on, not upstream jargon."""
    # ORS's walking profile treats ferry lines as walkable. Near the Seaport
    # that turned a 5 km loop into 46 km, 36 of them by boat.
    options = dict(body.get("options", {}), avoid_features=["ferries"])
    payload = dict(body, options=options, elevation=False, instructions=False)
    try:
        data = request_json("POST", ORS_URL, body=payload, timeout=30, headers={
            "Authorization": key,
            "Accept": "application/geo+json",
        })
    except UpstreamError as e:
        raise _explain(e)
    feature = data["features"][0]
    coords = [c[:2] for c in feature["geometry"]["coordinates"]]
    return coords, float(feature["properties"]["summary"]["distance"])


def _explain(err):
    text = str(err)
    if err.status == 403 and "quota" in text.lower():
        # ORS's daily allowance is spent - a 403, not a 429, and not a bad key.
        return ApiError(503, "This site has used today's route allowance from OpenRouteService. "
                             "Try again tomorrow.")
    if err.status in (401, 403):
        return ApiError(502, "OpenRouteService rejected the API key. Check ORS_API_KEY in .env.")
    if err.status == 429:
        return ApiError(503, "Route quota reached for now (40 a minute, 2,000 a day). "
                             "Try again shortly.")
    if "routable point" in text:
        return ApiError(422, "There's no walkable path near that start point. "
                             "Try a point on a street or path.")
    return ApiError(502, "Couldn't get a route from OpenRouteService (%s)." % text)


# -------------------------------------------------------------------- loops

def _loop_round(key, start, ask, seeds):
    """Ask ORS for one round trip per seed, in parallel."""
    def one(s):
        try:
            return _ors(key, {
                "coordinates": [start],
                "options": {"round_trip": {"length": round(ask), "points": 5, "seed": s}},
            })
        except ApiError as e:
            return e

    with ThreadPoolExecutor(max_workers=len(seeds)) as pool:
        return list(pool.map(one, seeds))


def _loop_candidates(key, start, length, seed, explore=False):
    """Several ORS round trips at once -> ([(coords, metres), ...], calls).

    If the closest is still well off, ask again for a scaled length: a request
    for 5 km that keeps coming back at 8 km is asked for ~3 km next time. When
    exploring (choosing by hills), always ask a second round for more choice."""
    base = seed * LOOP_CANDIDATES * 2
    results = _loop_round(key, start, length, [base + k for k in range(LOOP_CANDIDATES)])
    routes = [r for r in results if not isinstance(r, ApiError)]
    if not routes:
        raise results[0]          # all failed the same way, e.g. bad start point
    best = min(routes, key=lambda r: abs(r[1] - length))
    calls = len(results)

    off = abs(best[1] - length) > TOLERANCE_M
    if (off or explore) and calls + LOOP_CANDIDATES <= MAX_CALLS:
        ask = length * min(2.0, max(0.5, length / best[1])) if off else length
        more = _loop_round(key, start, ask, [base + LOOP_CANDIDATES + k for k in range(LOOP_CANDIDATES)])
        calls += len(more)
        routes += [r for r in more if not isinstance(r, ApiError)]

    return routes, calls


def _best_loop(key, start, length, seed):
    """The loop closest to the length -> (coords, metres, calls)."""
    routes, calls = _loop_candidates(key, start, length, seed)
    best = min(routes, key=lambda r: abs(r[1] - length))
    return best[0], best[1], calls


# ------------------------------------------------------------ out-and-back

def _destination(start, bearing_deg, dist_m):
    d = dist_m / EARTH_R
    brg = math.radians(bearing_deg)
    la1, lo1 = math.radians(start[1]), math.radians(start[0])
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(brg))
    lo2 = lo1 + math.atan2(math.sin(brg) * math.sin(d) * math.cos(la1),
                           math.cos(d) - math.sin(la1) * math.sin(la2))
    return [math.degrees(lo2), math.degrees(la2)]


def _bearing(a, b):
    """Initial compass bearing from a to b, in degrees."""
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dlon = math.radians(b[0] - a[0])
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360


def _haversine(a, b):
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2)
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def _out_and_back(key, start, length, seed):
    """The out-and-back closest to the length -> (coords, metres, calls)."""
    routes, calls = _out_and_back_candidates(key, start, length, seed)
    best = min(routes, key=lambda r: abs(r[1] - length))
    return best[0], best[1], calls


def _out_and_back_candidates(key, start, length, seed, explore=False):
    """Run out to a turnaround and back the same way.

    ORS's round_trip only makes loops, so this aims at a turnaround point,
    routes to it, and mirrors the leg. Street distance isn't proportional to
    straight-line distance - a river or a dead end can double it - so it
    re-aims a few times and keeps the best leg rather than trusting any one
    correction.

    A direction can be blocked: aim across the harbour and ORS snaps the
    turnaround to the same spot on the shore however far you aim. When
    re-aiming stops changing the leg, or the turnaround is unroutable, it
    moves on to another direction. Returns ([(coords, metres), ...], calls):
    the first leg within tolerance, or - when exploring - one per direction
    that reached it; failing that, the best leg found within MAX_CALLS."""
    def there_and_back(leg, got):
        return leg + leg[::-1][1:], 2 * got

    half = length / 2
    last_error = None
    best = None
    good = []
    calls = 0
    for attempt in range(4):
        bearing = (seed * 67 + attempt * 90) % 360
        reach = half / DETOUR
        previous = None
        for _ in range(OUT_AND_BACK_AIMS):
            if calls >= MAX_CALLS:
                break
            calls += 1
            try:
                leg, got = _ors(key, {"coordinates": [start, _destination(start, bearing, reach)]})
            except ApiError as e:
                if e.status != 422:
                    raise
                last_error = e
                break                                  # unroutable that way
            if best is None or abs(got - half) < abs(best[1] - half):
                best = (leg, got)
            if got <= 0 or abs(got - half) <= TOLERANCE_M / 2:
                good.append(there_and_back(leg, got))
                if not explore:
                    return good, calls
                break                                  # try another direction too
            if previous is not None and abs(got - previous) < 0.03 * half:
                break                                  # blocked that way
            previous = got
            reach *= min(2.0, max(0.5, half / got))    # never more than 2x a step
        if calls >= MAX_CALLS:
            break
    if good:
        return good, calls
    if best is None:
        raise last_error or ApiError(422, "Couldn't find an out-and-back route from here.")
    return [there_and_back(*best)], calls


# ------------------------------------------------------------ run to a place

def _run_to(key, start, dest, length, seed, explore=False):
    """One way from start to dest, about `length` long.

    The direct route sets a floor: asked for less (or barely more), the
    runner gets the direct route and the page says how long it really is.
    Within TOLERANCE_M of the direct route either way, the direct route is
    the run. Asked for more than TOLERANCE_M short of it, the runner still
    gets it, and the page warns that the destination is too far.

    Longer runs bend out to one side through waypoints along a circular arc
    from start to destination (RUN_TO_ARC), sized so the straight-line
    path through them, scaled by how much longer this pair's streets run than
    the crow flies, is the requested distance; then re-aimed like an
    out-and-back. Both sides are tried when exploring for hills, when one
    side is water. Spurs ORS adds to reach a waypoint are cut (_trim_spurs).

    -> ([(coords, metres), ...], calls, direct metres)"""
    direct = _ors(key, {"coordinates": [start, dest]})
    calls = 1
    if length <= direct[1] + TOLERANCE_M:
        return [direct], calls, direct[1]

    straight = max(_haversine(start, dest), 1.0)
    street = max(1.0, direct[1] / straight)
    sides = (1, -1) if seed % 2 else (-1, 1)

    candidates = []
    for side in sides:
        target = length
        for _ in range(RUN_TO_AIMS):
            if calls >= MAX_CALLS:
                break
            waypoints = _arc_waypoints(start, dest, target / street, side)
            calls += 1
            try:
                route = _trim_spurs(*_ors(key, {"coordinates": [start] + waypoints + [dest]}))
            except ApiError as e:
                if e.status != 422:
                    raise
                break                                  # a waypoint in the water: other side
            candidates.append(route)
            if abs(route[1] - length) <= TOLERANCE_M:
                if not explore:
                    return candidates, calls, direct[1]
                break
            target *= min(2.0, max(0.5, length / route[1]))
    return candidates or [direct], calls, direct[1]


def _arc_waypoints(start, dest, path_m, side):
    """Waypoints at RUN_TO_ARC along a circular arc from start to dest, bulging
    to the left (side=-1) or right (side=1) of the way there, sized so start
    -> waypoints -> dest is path_m long in straight lines. A long run on a
    short gap bends the arc past a semicircle, round towards a full circle:
    a loop that arrives at the destination rather than overshooting it."""
    c = max(_haversine(start, dest) / 2, 1.0)
    mid = [(start[0] + dest[0]) / 2, (start[1] + dest[1]) / 2]
    toward = _bearing(start, dest)

    def points(phi):
        # phi: the angle the arc turns through. In a frame with x towards the
        # destination and y towards the bulge, start is (-c, 0), dest (c, 0).
        r = c / math.sin(phi / 2)
        cy = -r * math.cos(phi / 2)                    # the circle's centre is (0, cy)
        from_start = math.atan2(-cy, -c)
        out = []
        for f in RUN_TO_ARC:
            t = from_start - f * phi                   # over the bulge, start to dest
            x, y = r * math.cos(t), cy + r * math.sin(t)
            out.append(_destination(mid, toward + side * math.degrees(math.atan2(y, x)),
                                    math.hypot(x, y)))
        return out

    def path(phi):
        pts = [start] + points(phi) + [dest]
        return sum(_haversine(p, q) for p, q in zip(pts, pts[1:]))

    lo, hi = 1e-3, 2 * math.pi - 1e-3
    for _ in range(40):                                # path(phi) grows with phi
        phi = (lo + hi) / 2
        lo, hi = (phi, hi) if path(phi) < path_m else (lo, phi)
    return points((lo + hi) / 2)


def _trim_spurs(coords, metres):
    """Cut spurs - stretches run out and straight back over the same points.
    ORS adds one whenever a waypoint lands on a dead-end path: in to the
    waypoint, then back out the way it came. -> (coords, metres)"""
    def key(c):
        return round(c[0], 6), round(c[1], 6)

    out = []
    for c in coords:
        if out and key(c) == key(out[-1]):
            continue                                   # repeated point at a waypoint
        if len(out) >= 2 and key(c) == key(out[-2]):
            out.pop()                                  # went to out[-1] and came straight back
            continue
        out.append(c)

    def length(cs):
        return sum(_haversine(a, b) for a, b in zip(cs, cs[1:]))

    before = length(coords)
    return out, metres * (length(out) / before if before else 1.0)
