"""
Fill in location ids and coordinates for recognition entries.

You type name + address when adding an entry; this looks each restaurant up
once and writes its id and coordinates back to the file, plus the name and
address it matched so you can check it picked the right place. Matching
against search results is then exact.

    python -m restaurants.tools.enrich_recognition              # Boston
    python -m restaurants.tools.enrich_recognition --dry-run    # show, don't write
    python -m restaurants.tools.enrich_recognition --force      # redo all entries

Two ways to look up, chosen automatically:
- OpenStreetMap (free, the default): every named food place in the city is
  downloaded in one Overpass request; each address is geocoded with
  Nominatim (one request per second, its usage limit) and the entry is
  snapped to the place with a matching name within 150 m -> osm_id.
- Google (if GOOGLE_PLACES_API_KEY is in .env, or with --source google):
  a Places text search on "name, address" -> place_id.

Placeholder entries are skipped. Lines marked CHECK need a human look.
"""

import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from restaurants import matching  # noqa: E402
from restaurants.sources import google, osm  # noqa: E402
from server import cities, env  # noqa: E402
from server.fetch import UpstreamError  # noqa: E402

SNAP_RADIUS_M = 150
NOMINATIM_PAUSE_S = 1.1


def lookup_google(entry, city, key):
    center_lon, center_lat = city["center"]
    query = ", ".join(p for p in (entry.get("name"), entry.get("address")) if p)
    found = google.find(query, center_lat, center_lon, key)
    if found is None:
        return None, "no match on Google"
    if not cities.contains(city, found["lon"], found["lat"]):
        return None, "Google matched %s, outside %s" % (found["address"], city["name"])
    flag = "ok" if matching.names_match(entry["name"], found["name"]) else "CHECK"
    return {"place_id": found["id"], "lat": found["lat"], "lon": found["lon"],
            "matched_name": found["name"], "matched_address": found["address"]}, flag


def lookup_osm(entry, city, mapped):
    """Geocode the address, then snap to the mapped food place of that name.
    `mapped` is every named food place in the city (one Overpass download)."""
    point = None
    address = entry.get("address") or ""
    street_only = re.sub(r"^\s*[\d\-]+[A-Za-z]?\s+", "", address)   # "300 Pier 4 Blvd" -> "Pier 4 Blvd"
    for query in (address, "%s, %s" % (entry["name"], city["name"]),
                  street_only if street_only != address else None):
        if not query:
            continue
        point = osm.geocode(query, city)
        time.sleep(NOMINATIM_PAUSE_S)
        if point:
            break

    same_name = [p for p in mapped if matching.names_match(entry["name"], p["name"])]
    if point is None:
        if len(same_name) == 1:                 # unique in the city: safe enough
            best = same_name[0]
            return {"osm_id": best["id"], "lat": best["lat"], "lon": best["lon"],
                    "matched_name": best["name"], "matched_address": best["address"]}, \
                "CHECK - address not found; matched the only OSM place with this name"
        return None, "Nominatim could not find the address"

    def dist(p):
        return matching.distance_m(point["lat"], point["lon"], p["lat"], p["lon"])

    near = [p for p in same_name if dist(p) <= SNAP_RADIUS_M]
    if near:
        best = min(near, key=dist)
        return {"osm_id": best["id"], "lat": best["lat"], "lon": best["lon"],
                "matched_name": best["name"],
                "matched_address": best["address"] or point["address"]}, "ok"

    # No restaurant of that name mapped there: place it at the address.
    around = sorted((p for p in mapped if dist(p) <= SNAP_RADIUS_M), key=dist)
    others = ", ".join(p["name"] for p in around[:4]) or "none"
    return {"lat": point["lat"], "lon": point["lon"], "matched_name": None,
            "matched_address": point["address"]}, \
        "CHECK - placed at the address; no OSM place named like this within %d m (nearby: %s)" \
        % (SNAP_RADIUS_M, others)


def main():
    # The Windows console can't print every character in place names (e.g. "ō").
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--city", help="city key from shared/cities.json (default: the default city)")
    parser.add_argument("--source", choices=("auto", "osm", "google"), default="auto",
                        help="where to look entries up (default: Google if a key is set, else OSM)")
    parser.add_argument("--dry-run", action="store_true", help="print results, do not write the file")
    parser.add_argument("--force", action="store_true",
                        help="look up again entries that already have a location (overwrites hand fixes)")
    args = parser.parse_args()

    env.load(os.path.join(ROOT, ".env"))
    key = env.secret("GOOGLE_PLACES_API_KEY")
    source = args.source if args.source != "auto" else ("google" if key else "osm")
    if source == "google" and not key:
        sys.exit("GOOGLE_PLACES_API_KEY is not set in .env.")

    city = cities.get(args.city)
    path = cities.data_path(city["recognition"])
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    print("Looking up %s entries on %s...\n" % (city["name"], "Google" if source == "google" else
                                                "OpenStreetMap"))
    mapped = []
    if source == "osm":
        print("  Downloading every named food place in %s from OpenStreetMap (one request)..."
              % city["name"])
        try:
            mapped = osm.food_places_in_bounds(city)
        except UpstreamError as e:
            sys.exit("  %s" % e)
        print("  %d places. Now looking up each address (about 1 s each)...\n" % len(mapped))

    # The same restaurant often has several entries; look it up once.
    looked_up = {}
    changed = 0
    for entry in data.get("entries", []):
        if entry.get("placeholder"):
            continue
        # Anything already placed is left alone - including hand fixes - unless --force.
        located = entry.get("lat") is not None
        if located and not args.force:
            continue

        query = (entry.get("name"), entry.get("address"))
        if query not in looked_up:
            try:
                looked_up[query] = (lookup_google(entry, city, key) if source == "google"
                                    else lookup_osm(entry, city, mapped))
            except UpstreamError as e:
                looked_up[query] = (None, "ERROR %s" % e)
            result, flag = looked_up[query]
            label = result and (result.get("matched_name") or result.get("matched_address"))
            print("  %-6s %s%s" % (flag.split(" ")[0] if result else "?", entry["name"],
                                   (" -> %s" % label) if label else ""))
            if flag.startswith(("CHECK", "ERROR")) or not result:
                print("         %s" % flag)
        result, _ = looked_up[query]
        if result is None:
            continue

        for k in ("place_id", "osm_id"):      # drop an id from a previous lookup
            entry.setdefault(k, None)
            if k not in result:
                entry[k] = None
        entry.update(result)
        changed += 1

    if args.dry_run:
        print("\nDry run: %d entries would be updated." % changed)
        return
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    print("\nUpdated %d entries in %s. Review any marked CHECK or ?." % (changed, city["recognition"]))


if __name__ == "__main__":
    main()
