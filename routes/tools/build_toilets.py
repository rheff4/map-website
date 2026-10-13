"""
Build a city's public-toilet layer, with every toilet labelled.

    python -m routes.tools.build_toilets                # default city
    python -m routes.tools.build_toilets --city boston

Fetches toilets (OSM amenity=toilets) inside the city's bounds, and the named
streets around them, from OpenStreetMap. Each toilet gets a label:

- its own name, if OSM gives it a real one;
- otherwise the nearest intersection of two differently named streets, as
  "Street A / Street B", the bigger road first;
- failing that, the nearest named street, as "near Street A".

A name that only says what the place is - "City Toilet", "Restroom" - counts
as no name: two toilets both labelled "City Toilet" can't be told apart on a
map. The name still shows in the popup.

"Public" means not tagged access=private or access=no. Customers-only toilets
are kept and marked as such, since a runner may well be a customer.

Writes data/toilets/<city>.json, served by GET /api/routes/toilets. Re-run to
refresh: toilets open and close, and OpenStreetMap keeps improving.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

from routes.tools.build_elevation_grid import overpass
from server import cities

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "data", "toilets")

# Streets a person would name an intersection by, biggest first. Leaves out
# footpaths (rarely named, and "X / unnamed path" helps no one) and
# motorways and trunk roads (you don't stand at a corner of Storrow Drive).
STREET_RANK = {
    "primary": 0,
    "secondary": 1,
    "tertiary": 2,
    "pedestrian": 3,         # e.g. Washington Street at Downtown Crossing
    "unclassified": 4,
    "residential": 5,
    "living_street": 6,
}

# Streets are fetched within this distance of any toilet. Any intersection
# closer than this is guaranteed to be found.
SEARCH_M = 400

NOT_PUBLIC = {"private", "no"}

# Names that describe or brand rather than identify. "City Toilet" and
# "Portland Loo" are both models of street toilet, so several different
# toilets share each name.
GENERIC_NAME = re.compile(
    r"^\s*(the\s+)?(public\s+|city\s+|park\s+|portland\s+)?(toilets?|restrooms?|rest\s*rooms?|"
    r"bathrooms?|comfort\s+stations?|lavatory|lavatories|wc|washrooms?|loos?)\s*$", re.I)

# Beyond this, an intersection stops being a useful way to find a toilet -
# out on Castle Island the nearest one is ~1 km away, from Spectacle Island
# it's 3 km across the harbour. Such toilets are labelled by the named place
# they're in instead, when there is one.
PLACE_FALLBACK_M = 300

# Places to label by, most specific first. Administrative areas ("Boston",
# "Suffolk County") are deliberately absent: they say nothing useful.
PLACE_KINDS = [
    ("building", None),
    ("leisure", {"park", "nature_reserve", "garden", "recreation_ground", "stadium",
                 "marina", "sports_centre", "pitch", "playground"}),
    ("historic", None),
    ("tourism", {"attraction", "museum", "zoo"}),
    ("amenity", {"university", "college", "library", "hospital", "ferry_terminal"}),
    ("place", {"island", "islet"}),
    ("aeroway", {"aerodrome", "terminal"}),
]

QUERY = """[out:json][timeout:180][bbox:%(s)f,%(w)f,%(n)f,%(e)f];
nwr["amenity"="toilets"]->.t;
.t out center tags;
way(around.t:%(r)d)["highway"~"^(%(types)s)$"]["name"]->.s;
.s out body;
node(w.s);
out skel qt;"""


# ---------------------------------------------------------------- labelling

def is_generic(name):
    return bool(GENERIC_NAME.match(name or ""))


def intersections(streets, coords):
    """Nodes where two or more differently named streets meet.

    streets: [{"name": str, "highway": str, "nodes": [node ids]}]
    coords:  {node id: (lon, lat)}
    Returns [((lon, lat), [names, biggest road first])]."""
    at = {}
    for s in streets:
        rank = STREET_RANK.get(s["highway"])
        if rank is None or not s.get("name"):
            continue
        for nid in s["nodes"]:
            names = at.setdefault(nid, {})
            names[s["name"]] = min(rank, names.get(s["name"], rank))
    out = []
    for nid, names in at.items():
        if len(names) >= 2 and nid in coords:
            ordered = sorted(names, key=lambda n: (names[n], n))
            out.append((coords[nid], ordered))
    return out


def _metres(a, b):
    """Distance between two (lon, lat) points; plenty accurate at city scale."""
    kx = 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110574)


def label_for(toilet, crossings, street_points):
    """(label, how, metres) for one toilet.

    toilet:        {"name": str or None, "lon": .., "lat": ..}
    crossings:     output of intersections()
    street_points: [((lon, lat), name, rank)] for the "near Street" fallback
    """
    name = (toilet.get("name") or "").strip()
    if name and not is_generic(name):
        return name, "name", 0.0
    here = (toilet["lon"], toilet["lat"])
    if crossings:
        point, names = min(crossings, key=lambda c: _metres(here, c[0]))
        return "%s / %s" % (names[0], names[1]), "intersection", _metres(here, point)
    if street_points:
        point, street, _ = min(street_points, key=lambda p: _metres(here, p[0]))
        return "near %s" % street, "street", _metres(here, point)
    return "Public toilet", "none", 0.0


def place_name(areas):
    """The most specific useful name among the areas a point lies in.

    areas: [tags dicts], as Overpass is_in returns them."""
    for key, values in PLACE_KINDS:
        for tags in areas:
            if tags.get("name") and key in tags and (values is None or tags[key] in values):
                return tags["name"]
    return None


def places_around(points):
    """For each (lon, lat), the tags of every named area containing it.

    One Overpass query: a marker element precedes each point's areas, so the
    flat result list can be split back up by point."""
    parts = ["[out:json][timeout:120];"]
    for i, (lon, lat) in enumerate(points):
        parts.append('make marker idx=%d; out;' % i)
        parts.append('is_in(%f,%f)->.a; area.a["name"]; out tags;' % (lat, lon))
    found = [[] for _ in points]
    current = None
    for el in overpass("\n".join(parts)):
        if el.get("type") == "marker":
            current = int(el["tags"]["idx"])
        elif current is not None:
            found[current].append(el.get("tags", {}))
    return found


# -------------------------------------------------------------------- build

def fetch(bounds):
    (w, s), (e, n) = bounds
    return overpass(QUERY % {"s": s, "w": w, "n": n, "e": e, "r": SEARCH_M,
                             "types": "|".join(STREET_RANK)})


def build(elements, find_places=places_around):
    """Labelled GeoJSON features from Overpass elements, plus counts.

    find_places is injectable so tests can run without the network."""
    toilets, streets, coords = [], [], {}
    for el in elements:
        tags = el.get("tags", {})
        if tags.get("amenity") == "toilets":
            lat = el.get("lat", el.get("center", {}).get("lat"))
            lon = el.get("lon", el.get("center", {}).get("lon"))
            if lat is not None:
                toilets.append({"id": "%s/%s" % (el["type"], el["id"]), "lon": lon, "lat": lat,
                                "name": tags.get("name"), "tags": tags})
        elif el["type"] == "way" and "highway" in tags:
            streets.append({"name": tags.get("name"), "highway": tags["highway"],
                            "nodes": el.get("nodes", [])})
        elif el["type"] == "node":
            coords[el["id"]] = (el["lon"], el["lat"])

    crossings = intersections(streets, coords)
    street_points = [(coords[nid], s["name"], STREET_RANK[s["highway"]])
                     for s in streets if s["highway"] in STREET_RANK
                     for nid in s["nodes"] if nid in coords]

    public = [t for t in toilets if t["tags"].get("access") not in NOT_PUBLIC]
    skipped = len(toilets) - len(public)
    labelled = [(t,) + label_for(t, crossings, street_points) for t in public]

    # Far from any intersection: label by the place the toilet is in instead.
    far = [i for i, (t, _, how, metres) in enumerate(labelled)
           if how in ("street", "none") or (how == "intersection" and metres > PLACE_FALLBACK_M)]
    if far:
        areas = find_places([(labelled[i][0]["lon"], labelled[i][0]["lat"]) for i in far])
        for i, around in zip(far, areas):
            name = place_name(around)
            if name:
                t, label, how, metres = labelled[i]
                labelled[i] = (t, name, "place", metres)

    features = []
    counts = {"name": 0, "intersection": 0, "place": 0, "street": 0, "none": 0}
    for t, label, how, metres in labelled:
        counts[how] += 1
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(t["lon"], 6), round(t["lat"], 6)]},
            "properties": {
                "label": label,
                "label_source": how,
                # for "place" labels: how far the nearest intersection was
                "label_distance_m": round(metres),
                "name": t["name"],
                "access": t["tags"].get("access"),
                "fee": t["tags"].get("fee"),
                "wheelchair": t["tags"].get("wheelchair"),
                "opening_hours": t["tags"].get("opening_hours"),
                "osm": t["id"],
            },
        })
    features.sort(key=lambda f: f["properties"]["label"])
    return features, counts, skipped


CACHE_DIR = os.path.join(ROOT, ".cache")     # gitignored


def _cached(path, use_cache, produce, key=None):
    """Reuse a saved Overpass response if asked to and it matches `key`;
    otherwise produce a fresh one and save it. Overpass is a free shared
    service, so re-tuning labels shouldn't mean re-downloading."""
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("key") == key:
            print("    using saved %s" % os.path.relpath(path, ROOT))
            return saved["data"]
    data = produce()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"key": key, "data": data}, f)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", help="city key in shared/cities.json (default: the default city)")
    ap.add_argument("--cached", action="store_true",
                    help="reuse the last OpenStreetMap download instead of fetching again")
    args = ap.parse_args(argv)

    city = cities.get(args.city)
    print("Public toilets for %s" % city["name"])
    raw_path = os.path.join(CACHE_DIR, "toilets-%s-osm.json" % city["key"])
    places_path = os.path.join(CACHE_DIR, "toilets-%s-places.json" % city["key"])
    elements = _cached(raw_path, args.cached, lambda: fetch(city["bounds"]),
                       key=json.dumps(city["bounds"]))
    find_places = lambda points: _cached(                          # noqa: E731
        places_path, args.cached, lambda: places_around(points),
        key=json.dumps([[round(c, 6) for c in p] for p in points]))
    features, counts, skipped = build(elements, find_places)

    out = {
        "type": "FeatureCollection",
        "meta": {
            "_readme": "Generated by python -m routes.tools.build_toilets --city %s. "
                       "Do not edit by hand." % city["key"],
            "city": city["key"],
            "source": "OpenStreetMap contributors (ODbL), amenity=toilets",
            "built": datetime.date.today().isoformat(),
            "labels": counts,
            "excluded_not_public": skipped,
        },
        "features": features,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.json" % city["key"])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")

    far = [f for f in features if f["properties"]["label_source"] == "intersection"
           and f["properties"]["label_distance_m"] > PLACE_FALLBACK_M]
    print("  %d toilets labelled (%d excluded as not public)" % (len(features), skipped))
    print("  by own name: %(name)d   by intersection: %(intersection)d   by place: %(place)d   "
          "by street: %(street)d   unlabelled: %(none)d" % counts)
    if far:
        print("  %d intersection labels are over %d m away, with no named place to use instead"
              % (len(far), PLACE_FALLBACK_M))
    print("  wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
