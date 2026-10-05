"""
Data-source spike for Map Website.

Checks two things against reality before we commit to a stack:
  1. Can OpenRouteService generate a runnable loop of a target distance?
  2. Is OpenStreetMap restaurant/bathroom data good enough to map?

Usage:
    python spike.py restaurants
    python spike.py bathrooms
    python spike.py route            # needs ORS_API_KEY in the environment
    python spike.py all

Writes preview.html next to this file; open it in a browser to eyeball results.
Standard library only.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Boston Common, a plausible start point for a visitor's run.
START_LAT = 42.3550
START_LON = -71.0656

ROUTE_LENGTH_M = 5000
SEARCH_RADIUS_M = 1500

HERE = os.path.dirname(os.path.abspath(__file__))
# The main instance is frequently overloaded; try mirrors in order.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"


# Overpass rejects urllib's default User-Agent with a 406.
USER_AGENT = "map-website-spike/0.1 (student project; contact rheff4@mit.edu)"


def post(url, data, headers, timeout=90):
    headers = dict(headers, **{"User-Agent": USER_AGENT})
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------
# Overpass / OpenStreetMap
# --------------------------------------------------------------------------

def overpass(selector):
    # One nwr statement, not a node/way union - the union re-runs the
    # expensive `around` filter per member and reliably times out.
    query = (
        "[out:json][timeout:25];"
        "nwr[{sel}](around:{r},{lat},{lon});"
        "out center tags;"
    ).format(sel=selector, r=SEARCH_RADIUS_M, lat=START_LAT, lon=START_LON)
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # Overpass is a free shared service: it answers 504/429 when its rate-limit
    # slots are busy, so a failure usually means "wait", not "broken query".
    last = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                return post(url, body, headers, timeout=120)
            except Exception as e:  # noqa: BLE001 - try the next mirror
                host = urllib.parse.urlparse(url).netloc
                print("  (%s unavailable: %s)" % (host, e))
                last = e
        delay = 10 * (attempt + 1)
        print("  all mirrors busy, waiting %ds before retry..." % delay)
        time.sleep(delay)
    raise last


def elements_to_points(elements):
    points = []
    for el in elements:
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        points.append({"lat": lat, "lon": lon, "tags": el.get("tags", {})})
    return points


def report_restaurants():
    print("\n=== OSM restaurants within %dm of Boston Common ===" % SEARCH_RADIUS_M)
    data = overpass('"amenity"="restaurant"')
    pts = elements_to_points(data.get("elements", []))

    named = [p for p in pts if p["tags"].get("name")]
    with_cuisine = [p for p in pts if p["tags"].get("cuisine")]
    with_hours = [p for p in pts if p["tags"].get("opening_hours")]
    with_website = [p for p in pts
                    if p["tags"].get("website") or p["tags"].get("contact:website")]

    total = len(pts)
    print("total found:       %d" % total)
    if total:
        def pct(n):
            return "%4d  (%.0f%%)" % (n, 100.0 * n / total)
        print("has name:          %s" % pct(len(named)))
        print("has cuisine tag:   %s" % pct(len(with_cuisine)))
        print("has opening_hours: %s" % pct(len(with_hours)))
        print("has website:       %s" % pct(len(with_website)))

        cuisines = {}
        for p in with_cuisine:
            for c in p["tags"]["cuisine"].split(";"):
                c = c.strip()
                cuisines[c] = cuisines.get(c, 0) + 1
        top = sorted(cuisines.items(), key=lambda kv: -kv[1])[:10]
        print("\ntop cuisines: " + (", ".join("%s (%d)" % (c, n) for c, n in top) or "none"))
        print("\nsample:")
        for p in named[:10]:
            t = p["tags"]
            print("  - %s  [cuisine: %s]" % (t["name"], t.get("cuisine", "-")))
    return pts


def report_bathrooms():
    print("\n=== OSM public toilets within %dm of Boston Common ===" % SEARCH_RADIUS_M)
    data = overpass('"amenity"="toilets"')
    pts = elements_to_points(data.get("elements", []))
    print("total found:       %d" % len(pts))
    for p in pts[:15]:
        t = p["tags"]
        print("  - %s  [access: %s, fee: %s]" % (
            t.get("name", "(unnamed)"), t.get("access", "-"), t.get("fee", "-")))
    return pts


# --------------------------------------------------------------------------
# OpenRouteService
# --------------------------------------------------------------------------

def report_route():
    print("\n=== ORS round-trip: %dm loop from Boston Common ===" % ROUTE_LENGTH_M)
    key = os.environ.get("ORS_API_KEY")
    if not key:
        print("ORS_API_KEY not set - skipping.")
        print("Get a free key at https://openrouteservice.org/dev/#/signup, then:")
        print('  $env:ORS_API_KEY="your-key"     (PowerShell)')
        return None

    body = json.dumps({
        "coordinates": [[START_LON, START_LAT]],
        "options": {"round_trip": {"length": ROUTE_LENGTH_M, "points": 5, "seed": 1}},
        "elevation": True,
        "instructions": False,
    }).encode("utf-8")

    try:
        data = post(ORS_URL, body, {
            "Authorization": key,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        })
    except urllib.error.HTTPError as e:
        print("ORS returned HTTP %d:" % e.code)
        print(e.read().decode("utf-8", "replace")[:800])
        return None

    feat = data["features"][0]
    props = feat["properties"]
    summary = props["summary"]
    coords = feat["geometry"]["coordinates"]

    asked_km = ROUTE_LENGTH_M / 1000.0
    got_km = summary["distance"] / 1000.0
    print("requested:         %.2f km" % asked_km)
    print("actual:            %.2f km  (%+.1f%%)" % (
        got_km, 100.0 * (got_km - asked_km) / asked_km))
    print("duration:          %.0f min at ORS walking pace" % (summary["duration"] / 60.0))
    if props.get("ascent") is not None:
        print("elevation:         +%.0fm / -%.0fm" % (props["ascent"], props["descent"]))
    print("geometry points:   %d" % len(coords))

    start, end = coords[0], coords[-1]
    closed = abs(start[0] - end[0]) < 1e-5 and abs(start[1] - end[1]) < 1e-5
    print("returns to start:  %s" % ("yes" if closed else "NO - not a true loop"))
    return feat


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------

def write_preview(route_feature, restaurants, bathrooms):
    payload = {
        "start": [START_LON, START_LAT],
        "route": route_feature,
        "restaurants": [
            {"lon": p["lon"], "lat": p["lat"],
             "name": p["tags"].get("name", "(unnamed)"),
             "sub": p["tags"].get("cuisine", "")}
            for p in restaurants
        ],
        "bathrooms": [
            {"lon": p["lon"], "lat": p["lat"],
             "name": p["tags"].get("name", "(unnamed toilets)"),
             "sub": p["tags"].get("access", "")}
            for p in bathrooms
        ],
    }

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload))
    out = os.path.join(HERE, "preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("\nWrote %s - open it in a browser." % out)


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Map Website spike</title>
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
  body { margin:0; font-family: system-ui, sans-serif; }
  #map { position:absolute; inset:0; }
  #legend { position:absolute; top:10px; left:10px; z-index:1; background:#fff;
            padding:10px 14px; border-radius:8px; box-shadow:0 1px 6px rgba(0,0,0,.3);
            font-size:13px; line-height:1.6; }
  .sw { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
</style>
</head>
<body>
<div id="map"></div>
<div id="legend"></div>
<script>
const DATA = __DATA__;

const map = new maplibregl.Map({
  container: 'map',
  style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
  center: DATA.start,
  zoom: 14
});

map.on('load', () => {
  if (DATA.route) {
    map.addSource('route', { type:'geojson', data: DATA.route });
    map.addLayer({ id:'route', type:'line', source:'route',
      paint:{ 'line-color':'#2b6cb0', 'line-width':5, 'line-opacity':0.85 } });
  }

  const fc = (items) => ({ type:'FeatureCollection', features: items.map(p => ({
    type:'Feature',
    geometry:{ type:'Point', coordinates:[p.lon, p.lat] },
    properties:{ name:p.name, sub:p.sub }
  }))});

  map.addSource('restaurants', { type:'geojson', data: fc(DATA.restaurants) });
  map.addLayer({ id:'restaurants', type:'circle', source:'restaurants',
    paint:{ 'circle-radius':5, 'circle-color':'#e53e3e',
            'circle-stroke-width':1, 'circle-stroke-color':'#fff' } });

  map.addSource('bathrooms', { type:'geojson', data: fc(DATA.bathrooms) });
  map.addLayer({ id:'bathrooms', type:'circle', source:'bathrooms',
    paint:{ 'circle-radius':7, 'circle-color':'#2f855a',
            'circle-stroke-width':1, 'circle-stroke-color':'#fff' } });

  new maplibregl.Marker({ color:'#000' }).setLngLat(DATA.start).addTo(map);

  for (const layer of ['restaurants','bathrooms']) {
    map.on('click', layer, (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup()
        .setLngLat(e.features[0].geometry.coordinates)
        .setHTML('<b>' + p.name + '</b>' + (p.sub ? '<br>' + p.sub : ''))
        .addTo(map);
    });
    map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
  }

  document.getElementById('legend').innerHTML =
    '<div><span class="sw" style="background:#2b6cb0"></span>' +
      (DATA.route ? 'ORS loop' : 'no route (no API key)') + '</div>' +
    '<div><span class="sw" style="background:#e53e3e"></span>' +
      DATA.restaurants.length + ' OSM restaurants</div>' +
    '<div><span class="sw" style="background:#2f855a"></span>' +
      DATA.bathrooms.length + ' OSM toilets</div>';
});
</script>
</body>
</html>
"""


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    restaurants, bathrooms, route = [], [], None

    if cmd in ("restaurants", "all"):
        restaurants = report_restaurants()
    if cmd in ("bathrooms", "all"):
        bathrooms = report_bathrooms()
    if cmd in ("route", "all"):
        route = report_route()

    write_preview(route, restaurants, bathrooms)


if __name__ == "__main__":
    main()
