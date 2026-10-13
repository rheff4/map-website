"""
Restaurant search: fetch -> merge -> attach recognition -> exclude -> score -> rank.

The page does the filtering by price, cuisine and "open now" itself, on the
full ranked list this returns, so changing a filter never costs an API call.
"""

import os
import re

from restaurants import cuisines, hours, matching, recognition, scoring
from restaurants.sources import google, osm, yelp
from server import cities, env
from server.fetch import UpstreamError

MIN_RADIUS_M = 200
MAX_RADIUS_M = 5000
DEFAULT_RADIUS_M = 1500


def mode():
    """Demo mode (bundled fixtures) or live, and the keys to use.

    Live mode always uses OpenStreetMap, which is free and needs no key;
    Google and Yelp are added on top when their keys are set.
    """
    if (os.environ.get("RESTAURANTS_MOCK") or "").strip().lower() in ("1", "true", "yes"):
        return {"demo": True, "reason": "RESTAURANTS_MOCK=1 is set in .env.",
                "google": None, "yelp": None}
    return {"demo": False, "reason": None,
            "google": env.secret("GOOGLE_PLACES_API_KEY"), "yelp": env.secret("YELP_API_KEY")}


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


def _fetch_osm(status, city, entries, lat, lon, radius_m, now):
    """OpenStreetMap restaurants, plus every recognised place snapped to an OSM
    id nearby - those may be tagged cafe or fast_food and not appear otherwise."""
    extra_ids = [e["osm_id"] for e in entries if e.get("osm_id") and (
        e["lat"] is None or matching.distance_m(lat, lon, e["lat"], e["lon"]) <= radius_m + 200)]
    try:
        listings = osm.search(lat, lon, radius_m, extra_ids)
    except UpstreamError as e:
        status["osm"] = {"state": "error", "message": str(e)}
        return []
    offset = hours.city_utc_offset(city, now)
    for listing in listings:
        listing["utc_offset_minutes"] = offset
    status["osm"] = {"state": "ok", "count": len(listings)}
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


def _first(place, field):
    for listing in matching.listings(place):
        if listing.get(field):
            return listing[field]
    return None


def summarize(place, lat, lon, now=None):
    """The fields the page shows, taken from the best available listing.

    Curated entries fill gaps the APIs leave: Michelin publishes a price and a
    cuisine for every restaurant it lists, which OSM rarely has.
    """
    main = matching.primary(place)
    timed = next((l for l in matching.listings(place) if l.get("hours")), None)
    curated_prices = [e["price"] for e in place["recognition"] if e.get("price")]
    curated_cuisines = [{"key": e["cuisine"], "label": cuisines.LABELS[e["cuisine"]]}
                        for e in place["recognition"] if e.get("cuisine") in cuisines.LABELS]
    return {
        "id": place["key"],
        "name": main["name"],
        "address": _first(place, "address"),
        "lat": main["lat"],
        "lon": main["lon"],
        "distance_m": round(matching.distance_m(lat, lon, main["lat"], main["lon"])),
        "price": _first(place, "price") or (max(curated_prices) if curated_prices else None),
        "cuisines": cuisines.merge(*([l.get("cuisines") for l in matching.listings(place)]
                                     + [curated_cuisines])),
        "open_now": hours.is_open(timed["hours"], timed["utc_offset_minutes"], now) if timed else None,
        "url": _first(place, "url"),
        "brand": _first(place, "brand"),
        "closed": any(listing.get("closed") for listing in matching.listings(place)),
    }


def search(lat, lon, radius_m=DEFAULT_RADIUS_M, city_key=None, now=None):
    city = cities.get(city_key)
    m = mode()
    status, warnings = {}, []

    rec = recognition.load(city, demo=m["demo"])
    warnings.extend(rec["warnings"])

    google_listings = _fetch(status, "google", "GOOGLE_PLACES_API_KEY", m["google"], m["demo"],
                             lambda key, mock: google.search(lat, lon, radius_m, key, mock))
    yelp_listings = _fetch(status, "yelp", "YELP_API_KEY", m["yelp"], m["demo"],
                           lambda key, mock: yelp.search(lat, lon, radius_m, key, mock))
    osm_listings = [] if m["demo"] else _fetch_osm(status, city, rec["entries"], lat, lon,
                                                    radius_m, now)
    places = matching.merge_listings(google_listings, yelp_listings, osm_listings)

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
        brand = item.pop("brand")
        if scoring.is_chain(item["name"], chains) or (brand and scoring.is_chain(brand, chains)):
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
