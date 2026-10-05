"""
Loads the curated recognition file for a city (path set in shared/cities.json).

Problems in the file - an unknown source id, an entry with no location - are
returned as warnings rather than raised, so one bad line never takes the
whole feature down. The page shows them so they get fixed.
"""

import json

from restaurants.scoring import SOURCES
from server import cities

FIELDS = ("name", "address", "source", "source_url", "note", "placeholder",
          "place_id", "lat", "lon")


def _number(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def load(city, include_placeholders):
    """{'entries': [...], 'warnings': [...], 'placeholders': n, 'total': n}"""
    path = cities.data_path(city["recognition"])
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f).get("entries", [])
    except FileNotFoundError:
        return {"entries": [], "warnings": ["No recognition file at %s." % city["recognition"]],
                "placeholders": 0, "total": 0}
    except ValueError as e:
        return {"entries": [], "warnings": ["%s is not valid JSON: %s" % (city["recognition"], e)],
                "placeholders": 0, "total": 0}

    entries, warnings, placeholders = [], [], 0
    for i, item in enumerate(raw, 1):
        entry = {field: item.get(field) for field in FIELDS}
        entry["placeholder"] = bool(entry["placeholder"])
        entry["lat"], entry["lon"] = _number(entry["lat"]), _number(entry["lon"])
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
        if entry["lat"] is None and not entry["place_id"]:
            warnings.append("%s has no location yet - run "
                            "python -m restaurants.tools.enrich_recognition." % label)
            continue
        entries.append(entry)

    return {"entries": entries, "warnings": warnings,
            "placeholders": placeholders, "total": len(raw)}
