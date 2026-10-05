"""
Fill in Google place id and coordinates for recognition entries.

You type name + address when adding an entry; this looks each one up once on
Google and writes place_id, lat, lon back to the file, plus the name and
address Google matched so you can check it picked the right restaurant.
Matching against search results is then exact.

    python -m restaurants.tools.enrich_recognition              # Boston
    python -m restaurants.tools.enrich_recognition --dry-run    # show, don't write
    python -m restaurants.tools.enrich_recognition --force      # redo all entries

Needs GOOGLE_PLACES_API_KEY in .env. Placeholder entries are skipped.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from restaurants import matching  # noqa: E402
from restaurants.sources import google  # noqa: E402
from server import cities, env  # noqa: E402
from server.fetch import UpstreamError  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--city", help="city key from shared/cities.json (default: the default city)")
    parser.add_argument("--dry-run", action="store_true", help="print results, do not write the file")
    parser.add_argument("--force", action="store_true", help="look up entries that already have a place id")
    args = parser.parse_args()

    env.load(os.path.join(ROOT, ".env"))
    key = env.secret("GOOGLE_PLACES_API_KEY")
    if not key:
        sys.exit("GOOGLE_PLACES_API_KEY is not set in .env.")

    city = cities.get(args.city)
    path = cities.data_path(city["recognition"])
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    center_lon, center_lat = city["center"]
    # The same restaurant often has several entries; look it up once.
    looked_up = {}
    changed = 0

    for entry in data.get("entries", []):
        if entry.get("placeholder"):
            continue
        if entry.get("place_id") and entry.get("lat") is not None and not args.force:
            continue

        query = ", ".join(p for p in (entry.get("name"), entry.get("address")) if p)
        if query not in looked_up:
            try:
                looked_up[query] = google.find(query, center_lat, center_lon, key)
            except UpstreamError as e:
                print("  ERROR  %s: %s" % (query, e))
                looked_up[query] = None
        found = looked_up[query]

        if found is None:
            print("  ?      %s - no match on Google, fill in lat/lon by hand" % query)
            continue
        if not cities.contains(city, found["lon"], found["lat"]):
            print("  ?      %s - Google matched %s, outside %s; check it"
                  % (query, found["address"], city["name"]))
            continue

        flag = "ok" if matching.names_match(entry["name"], found["name"]) else "CHECK"
        print("  %-6s %s -> %s, %s" % (flag, entry["name"], found["name"], found["address"]))
        entry.update(place_id=found["id"], lat=found["lat"], lon=found["lon"],
                     matched_name=found["name"], matched_address=found["address"])
        changed += 1

    if args.dry_run:
        print("\nDry run: %d entries would be updated." % changed)
        return
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    print("\nUpdated %d entries in %s. Review any marked CHECK." % (changed, city["recognition"]))


if __name__ == "__main__":
    main()
