"""
Restaurant search: fetch -> merge -> attach recognition -> exclude -> score -> rank.

The page does the filtering by price, cuisine and "open now" itself, on the
full ranked list this returns, so changing a filter never costs an API call.
"""

import os
import re

from restaurants import cuisines, hours, matching, recognition, scoring
from restaurants.sources import google, yelp
from server import cities, env
from server.fetch import UpstreamError

MIN_RADIUS_M = 200
MAX_RADIUS_M = 5000
DEFAULT_RADIUS_M = 1500


def mode():
    """Demo mode (bundled fixtures) or live (real APIs), and the keys to use."""
    google_key = env.secret("GOOGLE_PLACES_API_KEY")
    yelp_key = env.secret("YELP_API_KEY")
    if (os.environ.get("RESTAURANTS_MOCK") or "").strip().lower() in ("1", "true", "yes"):
        return {"demo": True, "reason": "RESTAURANTS_MOCK=1 is set in .env.",
                "google": None, "yelp": None}
    if not google_key and not yelp_key:
        return {"demo": True, "reason": "No GOOGLE_PLACES_API_KEY or YELP_API_KEY in .env.",
                "google": None, "yelp": None}
    return {"demo": False, "reason": None, "google": google_key, "yelp": yelp_key}


def _fetch(status, name, env_name, key, demo, call):
    """Run one source, recording how it went. A failing source never fails the search."""
    if demo:
        status[name] = {"state": "demo"}
        return call(None, True)
    if not key:
        status[name] = {"state": "off", "message": "No %s in .env." % env_name}
        return []
    try:
        listings = call(key, False)
    except UpstreamError as e:
        status[name] = {"state": "error", "message": str(e)}
        return []
    status[name] = {"state": "ok", "count": len(listings)}
    return listings


def _stub(entry):
    """A place we only know from the curated list: no ratings, price or hours."""
    slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-")
    return {"source": "recognition", "id": "%s-%.4f-%.4f" % (slug, entry["lat"], entry["lon"]),
            "name": entry["name"], "address": entry.get("address"),
            "lat": entry["lat"], "lon": entry["lon"], "price": None, "cuisines": [],
            "rating": None, "review_count": 0, "url": None, "closed": False,
            "hours": None, "utc_offset_minutes": None}


def _recognised_extras(entries, lat, lon, radius_m, m, warnings):
    """Places for recognised restaurants the searches did not return.

    A search returns at most 60 + 100 places, so a celebrated restaurant can be
    missing from it. The curated list is what we trust most, so those places
    are added back: looked up by Google place id if we have one, otherwise
    placed from the entry's own coordinates.
    """
    extras = []
    for entry in entries:
        if entry["lat"] is not None and \
                matching.distance_m(lat, lon, entry["lat"], entry["lon"]) > radius_m:
            continue

        place = matching.find_place_for_entry(extras, entry)
        if place is None:
            listing = None
            if entry["place_id"] and (m["demo"] or m["google"]):
                try:
                    listing = google.details(entry["place_id"], m["google"], mock=m["demo"])
                except UpstreamError as e:
                    warnings.append("Could not look up %s on Google: %s" % (entry["name"], e))
            if listing is not None:
                place = matching.new_place(google=listing)
            elif entry["lat"] is not None:
                place = matching.new_place(stub=_stub(entry))
            else:
                continue
            extras.append(place)
        place["recognition"].append(entry)
    return extras


def summarize(place, lat, lon, now=None):
    """The fields the page shows, taken from the best available listing."""
    g, y = place["google"], place["yelp"]
    main = matching.primary(place)
    return {
        "id": place["key"],
        "name": main["name"],
        "address": main.get("address") or (y or {}).get("address"),
        "lat": main["lat"],
        "lon": main["lon"],
        "distance_m": round(matching.distance_m(lat, lon, main["lat"], main["lon"])),
        "price": (g or {}).get("price") or (y or {}).get("price"),
        "cuisines": cuisines.merge((g or {}).get("cuisines"), (y or {}).get("cuisines")),
        "open_now": hours.is_open(g["hours"], g["utc_offset_minutes"], now) if g else None,
        "url": (g or {}).get("url") or (y or {}).get("url"),
        "closed": any(listing and listing.get("closed") for listing in (g, y)),
    }


def search(lat, lon, radius_m=DEFAULT_RADIUS_M, city_key=None, now=None):
    city = cities.get(city_key)
    m = mode()
    status, warnings = {}, []

    google_listings = _fetch(status, "google", "GOOGLE_PLACES_API_KEY", m["google"], m["demo"],
                             lambda key, mock: google.search(lat, lon, radius_m, key, mock))
    yelp_listings = _fetch(status, "yelp", "YELP_API_KEY", m["yelp"], m["demo"],
                           lambda key, mock: yelp.search(lat, lon, radius_m, key, mock))
    places = matching.merge_listings(google_listings, yelp_listings)

    rec = recognition.load(city, include_placeholders=m["demo"])
    warnings.extend(rec["warnings"])
    unmatched = matching.attach_recognition(places, rec["entries"])
    places.extend(_recognised_extras(unmatched, lat, lon, radius_m, m, warnings))

    chains = scoring.load_chains()
    excluded = {"chains": 0, "closed": 0}
    results = []
    for place in places:
        item = summarize(place, lat, lon, now)
        if item["distance_m"] > radius_m:
            continue
        if item.pop("closed"):
            excluded["closed"] += 1
            continue
        if scoring.is_chain(item["name"], chains):
            excluded["chains"] += 1
            continue
        item.update(scoring.score_place(place))
        results.append(item)

    results.sort(key=lambda r: (-r["score"], r["distance_m"]))

    return {
        "city": {"key": city["key"], "name": city["name"]},
        "origin": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "demo": m["demo"],
        "demo_reason": m["reason"],
        "sources": status,
        "recognition": {"loaded": len(rec["entries"]), "placeholders": rec["placeholders"],
                        "total": rec["total"]},
        "warnings": warnings,
        "excluded": excluded,
        "results": results,
    }
