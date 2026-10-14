"""
Loads the curated recognition file for a city (path set in shared/cities.json).
Demo mode loads fictional entries from restaurants/fixtures/ instead, so the
real file only ever holds real recognition.

Problems in the file - an unknown source id, an entry with no location - are
returned as warnings rather than raised, so one bad line never takes the
whole feature down. The page shows them so they get fixed.
"""

import json
import os

from restaurants.scoring import SOURCES
from server import cities

FIELDS = ("name", "address", "source", "source_url", "note", "placeholder",
          "place_id", "osm_id", "lat", "lon", "price", "cuisine")

DEMO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "recognition_demo.json")


def _number(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def load(city, demo=False):
    """{'entries': [...], 'warnings': [...], 'placeholders': n, 'total': n}

    Placeholder entries are only used in demo mode; anywhere else they are
    counted and skipped, so a leftover placeholder can never pass as real.
    """
    path = DEMO_PATH if demo else cities.data_path(city["recognition"])
    include_placeholders = demo
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f).get("entries", [])
    except FileNotFoundError:
        return {"entries": [], "warnings": ["No recognition file at %s." % city["recognition"]],
                "placeholders": 0, "total": 0}
    except ValueError as e:
        return {"entries": [], "warnings": ["%s is not valid JSON: %s" % (city["recognition"], e)],
                "placeholders": 0, "total": 0}

    entries, warnings, placeholders, unlocated = [], [], 0, []
    for i, item in enumerate(raw, 1):
        entry = {field: item.get(field) for field in FIELDS}
        entry["placeholder"] = bool(entry["placeholder"])
        entry["lat"], entry["lon"] = _number(entry["lat"]), _number(entry["lon"])
        price = _number(entry["price"])
        entry["price"] = int(price) if price in (1, 2, 3, 4) else None
        label = "Entry %d (%s)" % (i, entry["name"] or "no name")

        if entry["placeholder"]:
            placeholders += 1
            if not include_placeholders:
                continue
        if not entry["name"]:
            warnings.append("%s has no name - skipped." % label)
            continue
        if entry["source"] not in SOURCES:
            warnings.append("%s has unknown source '%s' - skipped. Known: %s."
                            % (label, entry["source"], ", ".join(SOURCES)))
            continue
        if entry["lat"] is None and not entry["place_id"] and not entry["osm_id"]:
            unlocated.append(entry["name"])
            continue
        entries.append(entry)

    if unlocated:
        names = sorted(set(unlocated))
        warnings.append("%d entries (%d restaurants) have no location yet, so they cannot be "
                        "matched - run: python -m restaurants.tools.enrich_recognition. "
                        "First few: %s." % (len(unlocated), len(names), ", ".join(names[:5])))

    return {"entries": entries, "warnings": warnings,
            "placeholders": placeholders, "total": len(raw)}
