"""
OpenStreetMap - free restaurant data, no key or account.

- Listings come from the Overpass API (https://wiki.openstreetmap.org/wiki/Overpass_API):
  name, location, cuisine, often opening hours and website. No ratings and
  almost never a price level.
- Addresses are geocoded with Nominatim (https://nominatim.org/), used only by
  tools/enrich_recognition.py. Its usage policy allows at most one request per
  second and asks for an identifying User-Agent, which server/fetch.py sends.

Both are free shared services run by volunteers, so responses are cached
(server/cache.py) and every call has a short timeout and falls back across
Overpass mirrors. Data (c) OpenStreetMap contributors, ODbL.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from restaurants import cuisines, hours, matching
from server import cache
from server.fetch import USER_AGENT, UpstreamError

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Places a recognised restaurant may be tagged as in OSM. Searches only list
# amenity=restaurant, but snapping a curated entry accepts any of these.
FOOD_AMENITIES = ("restaurant", "fast_food", "cafe", "bar", "pub", "food_court", "biergarten")

CACHE_VERSION = 1


def _request(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def overpass(query, timeout=30):
    """Run an Overpass QL query, trying each mirror once."""
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    errors = []
    for url in OVERPASS_URLS:
        try:
            return _request(url, body, timeout)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            errors.append("%s: %s" % (urllib.parse.urlparse(url).netloc,
                                      getattr(e, "code", None) or getattr(e, "reason", e)))
    raise UpstreamError("OpenStreetMap (Overpass) is busy right now - try again in a minute. (%s)"
                        % "; ".join(errors))


def parse(element):
    """One Overpass element -> a listing (see sources/__init__.py), or None."""
    tags = element.get("tags") or {}
    lat = element.get("lat", (element.get("center") or {}).get("lat"))
    lon = element.get("lon", (element.get("center") or {}).get("lon"))
    if not tags.get("name") or lat is None or lon is None:
        return None

    osm_id = "%s/%s" % (element["type"], element["id"])
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    address = ", ".join(p for p in (street, tags.get("addr:city")) if p) or None
    website = tags.get("website") or tags.get("contact:website")
    if website and not website.lower().startswith(("http://", "https://")):
        website = "https://" + website

    return {
        "source": "osm",
        "id": osm_id,
        "name": tags["name"],
        "address": address,
        "lat": lat,
        "lon": lon,
        "price": None,
        "cuisines": cuisines.from_osm(tags.get("cuisine")),
        "rating": None,
        "review_count": 0,
        "url": website or "https://www.openstreetmap.org/" + osm_id,
        "closed": False,
        "hours": hours.parse_osm(tags.get("opening_hours")),
        "utc_offset_minutes": None,      # filled in from the city config
        "brand": tags.get("brand"),
        "amenity": tags.get("amenity"),
    }


def _parse_all(elements):
    return [listing for listing in (parse(e) for e in elements) if listing]


# ---------------------------------------------------------------------------
# City-wide snapshot
#
# Searching Overpass per request made every search depend on a volunteer
# server that is often overloaded. Instead we keep one snapshot of every
# named food place in the city (one request, ~3,000 places, a few MB) and
# answer searches from it locally: instant, and still working while
# Overpass is down. A snapshot older than a day is still served, and a fresh
# one is fetched in the background.
# ---------------------------------------------------------------------------

SNAPSHOT_FRESH_S = 24 * 3600
SNAPSHOT_MAX_AGE_S = 30 * 24 * 3600     # beyond this, refuse to serve it

_refreshing = set()
_refresh_lock = threading.Lock()


def _snapshot_query(city):
    (west, south), (east, north) = city["bounds"]
    return ("[out:json][timeout:120];"
            "nwr[\"amenity\"~\"^(%s)$\"][\"name\"](%s,%s,%s,%s);"
            "out center tags;" % ("|".join(FOOD_AMENITIES), south, west, north, east))


def _snapshot_key(city):
    return {"v": CACHE_VERSION, "city": city["key"], "bounds": city["bounds"]}


def _fetch_snapshot(city):
    elements = overpass(_snapshot_query(city), timeout=150).get("elements", [])
    cache.put("osm-food-bbox", _snapshot_key(city), elements)
    return elements


def _refresh_in_background(city):
    with _refresh_lock:
        if city["key"] in _refreshing:
            return
        _refreshing.add(city["key"])

    def run():
        try:
            _fetch_snapshot(city)
        except UpstreamError:
            pass                              # keep serving the older snapshot
        finally:
            with _refresh_lock:
                _refreshing.discard(city["key"])

    threading.Thread(target=run, daemon=True).start()


def city_snapshot(city):
    """Every named food place in the city (listings). Serves a stale copy and
    refreshes it in the background; fetches synchronously only the first time."""
    key = _snapshot_key(city)
    if cache.get("osm-food-bbox", key, SNAPSHOT_FRESH_S) is not None:
        return _parse_all(cache.get("osm-food-bbox", key, SNAPSHOT_FRESH_S))
    stale = cache.get("osm-food-bbox", key, SNAPSHOT_MAX_AGE_S)
    if stale is not None:
        _refresh_in_background(city)
        return _parse_all(stale)
    return _parse_all(_fetch_snapshot(city))


def food_places_in_bounds(city):
    """Every named food place in the city - for the enrichment tool."""
    return city_snapshot(city)


def search(lat, lon, radius_m, city, extra_ids=()):
    """Restaurants around a point, plus the recognised places by OSM id
    (those may be tagged cafe, fast_food or bar)."""
    extra = set(extra_ids)
    reach = radius_m + 150
    return [p for p in city_snapshot(city)
            if (p["amenity"] == "restaurant" or p["id"] in extra)
            and matching.distance_m(lat, lon, p["lat"], p["lon"]) <= reach]


def geocode(text, city):
    """Best Nominatim match for an address inside the city's bounds, or None.
    Callers must wait a second between calls (Nominatim's usage policy)."""
    (west, south), (east, north) = city["bounds"]
    params = urllib.parse.urlencode({
        "q": text, "format": "jsonv2", "limit": 1, "countrycodes": "us",
        "viewbox": "%s,%s,%s,%s" % (west, north, east, south), "bounded": 1,
    })
    try:
        results = _request(NOMINATIM_URL + "?" + params, timeout=20)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise UpstreamError("Nominatim lookup failed: %s" % getattr(e, "reason", e))
    if not results:
        return None
    top = results[0]
    return {"lat": float(top["lat"]), "lon": float(top["lon"]), "address": top.get("display_name")}
