"""
Route endpoints, loaded by server/app.py.

    GET /api/routes/generate?lon=&lat=&km=&shape=loop|out-and-back&seed=
    GET /api/routes/toilets                 labelled public toilets, GeoJSON

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

# ORS's round trips miss the requested length, by up to ~18% in testing - far
# more where few streets connect, like the Seaport waterfront. Ask for a few
# at once with different seeds and keep the closest; if even that is more than
# LOOP_RETRY_OFF away, ask once more with the length scaled to compensate.
LOOP_CANDIDATES = 3
LOOP_RETRY_OFF = 0.10

# Hard cap on ORS calls per click, against the free tier's 2,000 a day and 40
# a minute. 6 calls a click still allows 330+ routes a day.
MAX_CALLS = 6

# Streets run longer than the straight line between two points - roughly 30%
# longer in a city grid. Used to aim an out-and-back's turnaround point.
DETOUR = 1.3

# An out-and-back re-aims its turnaround until the leg is within this share of
# half the distance, up to OUT_AND_BACK_AIMS tries, keeping the best leg seen.
OUT_AND_BACK_TOLERANCE = 0.08
OUT_AND_BACK_AIMS = 3

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
    if shape not in ("loop", "out-and-back"):
        raise ApiError(400, "Parameter 'shape' must be 'loop' or 'out-and-back'.")
    if not cities.contains(city, lon, lat):
        raise ApiError(400, "That start point is outside %s. Choose a point inside the city."
                       % city["name"])

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
    tried = MAX_CALLS          # if generation fails part-way, refund nothing
    try:
        if shape == "loop":
            coords, actual, tried = _best_loop(key, start, length, seed)
        else:
            coords, actual, tried = _out_and_back(key, start, length, seed)
    finally:
        BUDGET.settle(reserved, tried)

    grid = elevation.load(city["key"])
    elev = elevation.measure(grid, coords)
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "requestedM": length,
            "lengthM": round(actual, 1),
            "shape": shape,
            "seed": seed,
            "candidates": tried,
            "ascent": elev["ascent"] if grid else None,
            "descent": elev["descent"] if grid else None,
            "elevation": elev,
            "source": "openrouteservice",
            "stub": False,
        },
    }


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


def _best_loop(key, start, length, seed):
    """Several ORS round trips at once; keep the one closest to the length.

    If the closest is still well off, ask again for a scaled length: a request
    for 5 km that keeps coming back at 8 km is asked for ~3 km next time."""
    base = seed * LOOP_CANDIDATES * 2
    results = _loop_round(key, start, length, [base + k for k in range(LOOP_CANDIDATES)])
    routes = [r for r in results if not isinstance(r, ApiError)]
    if not routes:
        raise results[0]          # all failed the same way, e.g. bad start point
    best = min(routes, key=lambda r: abs(r[1] - length))
    calls = len(results)

    if abs(best[1] - length) / length > LOOP_RETRY_OFF and calls + LOOP_CANDIDATES <= MAX_CALLS:
        ask = length * min(2.0, max(0.5, length / best[1]))
        more = _loop_round(key, start, ask, [base + LOOP_CANDIDATES + k for k in range(LOOP_CANDIDATES)])
        calls += len(more)
        routes += [r for r in more if not isinstance(r, ApiError)]
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


def _out_and_back(key, start, length, seed):
    """Run out to a turnaround and back the same way.

    ORS's round_trip only makes loops, so this aims at a turnaround point,
    routes to it, and mirrors the leg. Street distance isn't proportional to
    straight-line distance - a river or a dead end can double it - so it
    re-aims a few times and keeps the best leg rather than trusting any one
    correction.

    A direction can be blocked: aim across the harbour and ORS snaps the
    turnaround to the same spot on the shore however far you aim. When
    re-aiming stops changing the leg, or the turnaround is unroutable, it
    moves on to another direction. Returns the best leg found within
    MAX_CALLS."""
    half = length / 2
    last_error = None
    best = None
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
            if got <= 0 or abs(got - half) / half <= OUT_AND_BACK_TOLERANCE:
                return leg + leg[::-1][1:], 2 * got, calls
            if previous is not None and abs(got - previous) < 0.03 * half:
                break                                  # blocked that way
            previous = got
            reach *= min(2.0, max(0.5, half / got))    # never more than 2x a step
        if calls >= MAX_CALLS:
            break
    if best is None:
        raise last_error or ApiError(422, "Couldn't find an out-and-back route from here.")
    leg, got = best
    return leg + leg[::-1][1:], 2 * got, calls
