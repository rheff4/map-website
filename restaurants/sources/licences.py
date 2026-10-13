"""
Official food licences from a city's open-data portal - free, no key.

Boston publishes every active food establishment licence on data.boston.gov
(CKAN), public domain, updated daily: business name, trade name ("dba"),
address and coordinates. Configured per city under "licences" in
shared/cities.json; the data only covers places inside the city's legal
boundary ("boundary"), so outside it we say nothing.

Used to confirm a restaurant is real and still operating - not for ranking.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from restaurants import matching
from server import cache, cities
from server.fetch import USER_AGENT, UpstreamError

CACHE_TTL_S = 24 * 3600        # the city republishes daily
MATCH_RADIUS_M = 150           # licence coordinates are geocoded addresses
ADDRESS_RADIUS_M = 200         # same street address, a little further apart

_STREET_WORDS = re.compile(r"\b(street|st|avenue|ave|road|rd|boulevard|blvd|place|pl|square|sq|"
                           r"drive|dr|court|ct|wharf|row|highway|hwy|terrace|ter|parkway|pkwy|way)\b\.?")
CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# City boundary
# ---------------------------------------------------------------------------

_boundaries = {}


def _rings(city):
    path = city.get("boundary")
    if not path:
        return None
    if path not in _boundaries:
        with open(cities.data_path(path), encoding="utf-8") as f:
            geom = json.load(f)["features"][0]["geometry"]
        _boundaries[path] = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return _boundaries[path]


def _in_ring(ring, lon, lat):
    inside = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[i - 1][:2]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_city_limits(city, lon, lat):
    """True/False, or None when the city has no boundary configured."""
    polygons = _rings(city)
    if polygons is None:
        return None
    for poly in polygons:
        if _in_ring(poly[0], lon, lat) and not any(_in_ring(hole, lon, lat) for hole in poly[1:]):
            return True
    return False


# ---------------------------------------------------------------------------
# Licences
# ---------------------------------------------------------------------------

def _fetch_all(cfg):
    url = "%s/api/3/action/datastore_search?%s" % (
        cfg["ckan_url"].rstrip("/"), urllib.parse.urlencode({"resource_id": cfg["resource_id"], "limit": 50000}))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            records = json.load(resp)["result"]["records"]
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        raise UpstreamError("%s licence data unavailable: %s" % (cfg["label"], getattr(e, "reason", e)))

    out = []
    for r in records:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (TypeError, ValueError, KeyError):
            continue
        if (r.get("licstatus") or "").lower() != "active":
            continue
        names = [n for n in (r.get("dbaname"), r.get("businessname")) if n]
        if names:
            out.append({"names": names, "lat": lat, "lon": lon,
                        "address": ", ".join(p for p in (r.get("address"), r.get("city")) if p),
                        "kind": r.get("descript"), "since": (r.get("license_add_dt_tm") or "")[:10]})
    return out


def load(city):
    """Active licences for the city (cached a day), or [] if it has none configured."""
    cfg = city.get("licences")
    if not cfg:
        return []
    return cache.cached("licences", {"v": CACHE_VERSION, "city": city["key"], "rid": cfg["resource_id"]},
                        CACHE_TTL_S, lambda: _fetch_all(cfg))


def address_key(address):
    """'1704 Washington St., Boston' -> '1704 washington'; None if no house number.
    Licences are often under a legal name ("XYZ Hospitality LLC"), so the
    street address is the second way to recognise the same restaurant."""
    m = re.match(r"\s*(\d+)[\-\d]*\s+(.+?)(?:,|$)", address or "")
    if not m:
        return None
    street = re.sub(r"[^a-z0-9]+", " ", _STREET_WORDS.sub("", m.group(2).lower())).strip()
    return "%s %s" % (m.group(1), street) if street else None


class Index:
    """Licences bucketed by ~200 m cells so matching 600 places stays fast."""

    CELL = 0.002

    def __init__(self, licences):
        self.cells = {}
        self.by_address = {}
        for lic in licences:
            self.cells.setdefault(self._cell(lic["lat"], lic["lon"]), []).append(lic)
            key = address_key(lic["address"])
            if key:
                self.by_address.setdefault(key, []).append(lic)

    def _cell(self, lat, lon):
        return int(lat // self.CELL), int(lon // self.CELL)

    def find(self, name, lat, lon, address=None, radius_m=MATCH_RADIUS_M):
        """The active licence for this restaurant - same name nearby, or else
        the same street address - or None."""
        by_name = self._find_by_name(name, lat, lon, radius_m)
        if by_name:
            return by_name
        for lic in self.by_address.get(address_key(address), ()):
            if matching.distance_m(lat, lon, lic["lat"], lic["lon"]) <= ADDRESS_RADIUS_M:
                return lic
        return None

    def _find_by_name(self, name, lat, lon, radius_m):
        ci, cj = self._cell(lat, lon)
        best, best_d = None, None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for lic in self.cells.get((ci + di, cj + dj), ()):
                    d = matching.distance_m(lat, lon, lic["lat"], lic["lon"])
                    if d <= radius_m and (best is None or d < best_d) and \
                            any(matching.names_match(name, n) for n in lic["names"]):
                        best, best_d = lic, d
        return best
