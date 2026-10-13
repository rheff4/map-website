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
import urllib.error
import urllib.parse
import urllib.request

from restaurants import cuisines, hours
from restaurants.sources import cache_ttl_seconds
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
    }


def _parse_all(elements):
    return [listing for listing in (parse(e) for e in elements) if listing]


def _id_clause(osm_ids):
    """'node/1', 'way/2' -> 'node(id:1);way(id:2);'"""
    by_type = {}
    for osm_id in osm_ids:
        kind, _, num = osm_id.partition("/")
        if kind in ("node", "way", "relation") and num.isdigit():
            by_type.setdefault(kind, []).append(num)
    return "".join("%s(id:%s);" % (k, ",".join(sorted(v))) for k, v in sorted(by_type.items()))


def search(lat, lon, radius_m, extra_ids=()):
    """Restaurants around a point, plus any extra OSM objects by id (the
    recognised places, which may be tagged cafe or fast_food)."""
    lat, lon = round(lat, 3), round(lon, 3)
    radius = int(radius_m) + 150         # the rounded centre can be ~70 m off
    ids = sorted(set(extra_ids))
    query = ("[out:json][timeout:25];"
             "(nwr[\"amenity\"=\"restaurant\"](around:%d,%s,%s);%s);"
             "out center tags;" % (radius, lat, lon, _id_clause(ids)))

    raw = cache.cached("osm-search",
                       {"v": CACHE_VERSION, "lat": lat, "lon": lon, "r": int(radius_m), "ids": ids},
                       cache_ttl_seconds(), lambda: overpass(query).get("elements", []))
    return _parse_all(raw)


def food_places_in_bounds(city):
    """Every named food place in the city's bounds, in ONE Overpass request -
    for snapping curated entries to the restaurant actually mapped there.
    Cached for a day, so re-running the enrichment tool is cheap."""
    (west, south), (east, north) = city["bounds"]
    query = ("[out:json][timeout:120];"
             "nwr[\"amenity\"~\"^(%s)$\"][\"name\"](%s,%s,%s,%s);"
             "out center tags;" % ("|".join(FOOD_AMENITIES), south, west, north, east))
    raw = cache.cached("osm-food-bbox", {"v": CACHE_VERSION, "city": city["key"], "bounds": city["bounds"]},
                       24 * 3600, lambda: overpass(query, timeout=150).get("elements", []))
    return _parse_all(raw)


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
