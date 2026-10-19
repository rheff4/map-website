"""
Yelp Fusion business search: https://docs.developer.yelp.com/reference/v3_business_search

Yelp search results carry no opening hours, so a Yelp-only restaurant has
unknown "open now" and is left out when that filter is on.
"""

from restaurants import cuisines
from restaurants.sources import cache_ttl_seconds, fixture
from server import cache
from server.fetch import request_json

SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
PAGE_SIZE = 50
MAX_PAGES = 2
CACHE_VERSION = 1


def parse(business):
    """One Yelp business -> a listing (see sources/__init__.py), or None."""
    coords = business.get("coordinates") or {}
    if coords.get("latitude") is None or coords.get("longitude") is None:
        return None
    price = business.get("price")
    return {
        "source": "yelp",
        "id": business["id"],
        "name": business.get("name") or "(unnamed)",
        "address": ", ".join((business.get("location") or {}).get("display_address") or []) or None,
        "lat": coords["latitude"],
        "lon": coords["longitude"],
        "price": len(price) if price else None,
        "cuisines": cuisines.from_yelp(business.get("categories")),
        "rating": business.get("rating"),
        "review_count": business.get("review_count") or 0,
        # Drop the tracking query string Yelp appends to every link.
        "url": (business.get("url") or "").split("?")[0] or None,
        "closed": bool(business.get("is_closed")),
        "hours": None,
        "utc_offset_minutes": None,
    }


def search(lat, lon, radius_m, key, mock=False):
    if mock:
        raw = fixture("yelp_search.json")["businesses"]
    else:
        lat, lon = round(lat, 3), round(lon, 3)
        headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}

        def fetch():
            businesses = []
            for page in range(MAX_PAGES):
                query = ("latitude=%s&longitude=%s&radius=%d&categories=restaurants"
                         "&sort_by=best_match&limit=%d&offset=%d"
                         % (lat, lon, min(int(radius_m) + 150, 40000), PAGE_SIZE, page * PAGE_SIZE))
                data = request_json("GET", SEARCH_URL + "?" + query, headers)
                batch = data.get("businesses", [])
                businesses.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
            return businesses

        raw = cache.cached("yelp-search",
                           {"v": CACHE_VERSION, "lat": lat, "lon": lon, "r": int(radius_m)},
                           cache_ttl_seconds(), fetch)

    return [listing for listing in (parse(b) for b in raw) if listing]
