"""
Deciding when two records are the same restaurant.

Google, Yelp and the hand-curated recognition list all spell names and place
pins differently ("The Gilded Spoon" vs "Gilded Spoon", pins 30 m apart), so a
match needs BOTH a similar name AND a nearby location. Either alone is not
enough: two cafes share a building, and "Toro" exists in many cities.

A merged place looks like:
    {'key': 'g:<google id>', 'google': listing|None, 'yelp': listing|None,
     'osm': listing|None, 'stub': listing|None, 'recognition': [entry, ...]}
"""

import difflib
import math
import re
import unicodedata

# Two records further apart than this are never the same restaurant.
MATCH_RADIUS_M = 100

# Words that carry no identity: "The Gilded Spoon Restaurant" == "Gilded Spoon".
STOPWORDS = {"the", "a", "an", "and", "restaurant", "boston", "co", "inc", "llc"}

# difflib ratio above which two normalised names count as the same,
# e.g. "nonna pinas kitchen" vs "nonna pina kitchen".
FUZZY_THRESHOLD = 0.85


def normalize_name(name):
    """'The Lantern & Ladle!' -> 'lantern ladle'."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ").replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(t for t in text.split() if t not in STOPWORDS)


def names_match(a, b):
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # One name is the other plus extra words: "Nonna Pina's" vs
    # "Nonna Pina's Kitchen". A lone short word ("bar") is too generic.
    small, big = sorted((set(na.split()), set(nb.split())), key=len)
    if small <= big and (len(small) >= 2 or len(next(iter(small))) >= 4):
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= FUZZY_THRESHOLD


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def same_place(a, b, radius_m=MATCH_RADIUS_M):
    """a and b are dicts with name, lat, lon."""
    if None in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        return False
    close = distance_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= radius_m
    return close and names_match(a["name"], b["name"])


# The data sources a place can combine, in order of preference for display.
SLOTS = ("google", "yelp", "osm", "stub")
_PREFIX = {"google": "g", "yelp": "y", "osm": "o", "stub": "r"}


def listings(place):
    return [place[slot] for slot in SLOTS if place.get(slot)]


def primary(place):
    """The listing that represents a place: Google, then Yelp, then OSM."""
    found = listings(place)
    return found[0] if found else None


def new_place(**by_slot):
    slot = next(s for s in SLOTS if by_slot.get(s))
    place = {s: by_slot.get(s) for s in SLOTS}
    place.update(key="%s:%s" % (_PREFIX[slot], by_slot[slot]["id"]), recognition=[])
    return place


def _absorb(places, incoming, slot, radius_m):
    """Pair each incoming listing with the closest existing place it matches."""
    for listing in incoming:
        best, best_d = None, None
        for place in places:
            if place[slot] is not None:
                continue
            for other in listings(place):
                if same_place(other, listing, radius_m):
                    d = distance_m(other["lat"], other["lon"], listing["lat"], listing["lon"])
                    if best is None or d < best_d:
                        best, best_d = place, d
        if best is not None:
            best[slot] = listing
        else:
            places.append(new_place(**{slot: listing}))


def merge_listings(google_listings, yelp_listings, osm_listings=(), radius_m=MATCH_RADIUS_M):
    """Combine Google, Yelp and OSM results into places, pairing duplicates."""
    places = []
    _absorb(places, google_listings, "google", radius_m)
    _absorb(places, yelp_listings, "yelp", radius_m)
    _absorb(places, osm_listings, "osm", radius_m)
    return places


def find_place_for_entry(places, entry, radius_m=MATCH_RADIUS_M):
    """The place a recognition entry refers to, or None.

    An entry resolved to a Google place id or an OSM id (see
    tools/enrich_recognition.py) matches exactly; otherwise fall back to
    name + proximity.
    """
    for slot, key in (("google", "place_id"), ("osm", "osm_id")):
        if entry.get(key):
            for place in places:
                if place[slot] and place[slot]["id"] == entry[key]:
                    return place

    if entry.get("lat") is None:
        return None
    best, best_d = None, None
    for place in places:
        for listing in listings(place):
            if same_place(listing, entry, radius_m):
                d = distance_m(listing["lat"], listing["lon"], entry["lat"], entry["lon"])
                if best is None or d < best_d:
                    best, best_d = place, d
    return best


def attach_recognition(places, entries, radius_m=MATCH_RADIUS_M):
    """Attach each recognition entry to its place. Returns the unmatched entries."""
    unmatched = []
    for entry in entries:
        place = find_place_for_entry(places, entry, radius_m)
        if place is None:
            unmatched.append(entry)
        else:
            place["recognition"].append(entry)
    return unmatched
