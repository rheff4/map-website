"""
Google Places API (New): https://developers.google.com/maps/documentation/places/web-service

Cost notes: rating, price and opening hours are in the pricier field tiers,
and each search page is billed separately. Responses are cached on disk
(server/cache.py) and the cache key rounds the search point to ~100 m so
nearby searches share results.
"""

import math

from restaurants import cuisines
from restaurants.sources import cache_ttl_seconds, fixture
from server import cache
from server.fetch import request_json

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/"

PLACE_FIELDS = [
    "id", "displayName", "formattedAddress", "shortFormattedAddress", "location",
    "priceLevel", "rating", "userRatingCount", "primaryType", "types",
    "regularOpeningHours.periods", "utcOffsetMinutes", "googleMapsUri", "businessStatus",
]

# Text Search returns 20 places per page, at most 3 pages.
MAX_PAGES = 3

PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 1, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}

CACHE_VERSION = 1   # bump when PLACE_FIELDS changes, to ignore old cache entries


def _headers(key, fields):
    return {"X-Goog-Api-Key": key, "X-Goog-FieldMask": ",".join(fields)}


def _rectangle(lat, lon, radius_m):
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    return {"low": {"latitude": lat - dlat, "longitude": lon - dlon},
            "high": {"latitude": lat + dlat, "longitude": lon + dlon}}


def parse(place):
    """One Places API place -> a listing (see sources/__init__.py), or None."""
    loc = place.get("location") or {}
    if loc.get("latitude") is None or loc.get("longitude") is None:
        return None
    return {
        "source": "google",
        "id": place["id"],
        "name": (place.get("displayName") or {}).get("text") or "(unnamed)",
        "address": place.get("shortFormattedAddress") or place.get("formattedAddress"),
        "lat": loc["latitude"],
        "lon": loc["longitude"],
        "price": PRICE_LEVELS.get(place.get("priceLevel")),
        "cuisines": cuisines.from_google(place.get("primaryType"), place.get("types")),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount") or 0,
        "url": place.get("googleMapsUri"),
        "closed": place.get("businessStatus") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"),
        "hours": (place.get("regularOpeningHours") or {}).get("periods"),
        "utc_offset_minutes": place.get("utcOffsetMinutes"),
    }


def _parse_all(places):
    return [listing for listing in (parse(p) for p in places) if listing]


def search(lat, lon, radius_m, key, mock=False):
    """Restaurants around a point, as listings."""
    if mock:
        return _parse_all(fixture("google_search.json")["places"])

    lat, lon = round(lat, 3), round(lon, 3)
    # Pad the box: the rounded centre can be ~70 m off, and the service
    # trims results to the true radius afterwards.
    box = _rectangle(lat, lon, radius_m + 150)
    headers = _headers(key, ["places." + f for f in PLACE_FIELDS] + ["nextPageToken"])

    def fetch():
        places, token = [], None
        for _ in range(MAX_PAGES):
            body = {"textQuery": "restaurants", "includedType": "restaurant",
                    "pageSize": 20, "locationRestriction": {"rectangle": box}}
            if token:
                body["pageToken"] = token
            data = request_json("POST", SEARCH_URL, headers, body)
            places.extend(data.get("places", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return places

    raw = cache.cached("google-search",
                       {"v": CACHE_VERSION, "lat": lat, "lon": lon, "r": int(radius_m)},
                       cache_ttl_seconds(), fetch)
    return _parse_all(raw)


def details(place_id, key, mock=False):
    """One place by id, as a listing, or None."""
    if mock:
        place = fixture("google_details.json")["places"].get(place_id)
        return parse(place) if place else None

    raw = cache.cached(
        "google-details", {"v": CACHE_VERSION, "id": place_id}, cache_ttl_seconds(),
        lambda: request_json("GET", DETAILS_URL + place_id, _headers(key, PLACE_FIELDS)))
    return parse(raw)


def find(text, lat, lon, key):
    """Best match for a free-text 'name, address', biased to a city. For tools."""
    body = {"textQuery": text, "pageSize": 1,
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon},
                                        "radius": 30000.0}}}
    data = request_json("POST", SEARCH_URL,
                        _headers(key, ["places." + f for f in PLACE_FIELDS]), body)
    places = _parse_all(data.get("places", []))
    return places[0] if places else None
