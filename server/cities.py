"""
City config, read from shared/cities.json - the one place a city is defined.

    city = cities.get()            # the default city
    city = cities.get('boston')
    city['center']                 # [lon, lat]
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CITIES_PATH = os.path.join(ROOT, "shared", "cities.json")


def load():
    with open(CITIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def get(key=None):
    """The config for one city, with its key added. KeyError if unknown."""
    data = load()
    key = key or data["default"]
    if key not in data["cities"]:
        raise KeyError("Unknown city '%s'. Known: %s" % (key, ", ".join(sorted(data["cities"]))))
    return dict(data["cities"][key], key=key)


def contains(city, lon, lat):
    """True if the point is inside the city's bounds."""
    (west, south), (east, north) = city["bounds"]
    return west <= lon <= east and south <= lat <= north


def data_path(relative):
    """Resolve a repo-relative data path from the config, refusing to leave the repo."""
    path = os.path.normpath(os.path.join(ROOT, relative))
    if os.path.commonpath([path, ROOT]) != ROOT:
        raise ValueError("Data path escapes the repo: %s" % relative)
    return path
