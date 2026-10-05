"""
Local score - how strongly trusted sources vouch for a restaurant.

THIS IS THE FILE TO TUNE. Every weight and threshold is a constant below, and
every point a restaurant earns is recorded in a breakdown that the page shows,
so you can see exactly why something ranks where it does.

How a score is built
--------------------
1. Recognition (dominant). Each curated entry in data/recognition/<city>.json
   names a source. Sources from the same publisher form a group, and only the
   best `GROUP_LIMITS[group]` entries in a group count - a Michelin star and a
   Michelin "selected" mention do not both score.
2. Agreement bonus. Every extra *independent* publisher that vouches for the
   place adds AGREEMENT_BONUS. Two critics agreeing is a much stronger signal
   than one critic shouting.
3. Ratings (supporting). Google and Yelp ratings are shrunk towards a typical
   rating in proportion to how few reviews there are (a Bayesian average), so
   4.9 from 14 reviews does not beat 4.6 from 900. Only the part above
   `threshold` scores, capped at `max_points`, so ratings can never outweigh
   a top recognition on their own.

Chains on the blocklist (data/chains.json) are removed before scoring.

A restaurant is "recognised" if at least one curated source vouches for it.
The page shows recognised restaurants only by default; ratings alone get a
place in the list only when that filter is switched off.
"""

import json
import os

from restaurants.matching import normalize_name

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAINS_PATH = os.path.join(ROOT, "data", "chains.json")

# ---------------------------------------------------------------------------
# 1. Recognition sources
#
# `source` values allowed in the recognition files. To add a source, add a
# line here and use its id in the data file.
# ---------------------------------------------------------------------------
SOURCES = {
    # id                      label shown on the page       weight  publisher group
    "michelin_star":         {"label": "Michelin star",          "weight": 10, "group": "michelin"},
    "michelin_bib_gourmand": {"label": "Michelin Bib Gourmand",  "weight": 8,  "group": "michelin"},
    "michelin_selected":     {"label": "Michelin Guide",         "weight": 6,  "group": "michelin"},
    "eater_38":              {"label": "Eater 38",               "weight": 8,  "group": "eater"},
    "eater_heatmap":         {"label": "Eater Heatmap",          "weight": 4,  "group": "eater"},
    "city_guide":            {"label": "City food guide",        "weight": 4,  "group": "city_guide"},
    "thefork":               {"label": "TheFork",                "weight": 2,  "group": "thefork"},
    "zomato":                {"label": "Zomato",                 "weight": 2,  "group": "zomato"},
    "influencer":            {"label": "Local food creator",     "weight": 2,  "group": "influencer"},
}

# How many entries from one publisher group can count. Default 1. Several
# separate influencer lists or tourism-board guides naming a place is more
# meaningful than one, but each is weaker than a critic.
GROUP_LIMITS = {"influencer": 3, "city_guide": 2}

# ---------------------------------------------------------------------------
# 2. Agreement between independent publishers
# ---------------------------------------------------------------------------
AGREEMENT_BONUS = 2   # per publisher group beyond the first

# ---------------------------------------------------------------------------
# 3. Ratings
#
# prior_mean / prior_reviews: the Bayesian average pulls a rating towards
#   prior_mean as if it had prior_reviews extra "average" reviews. Yelp
#   ratings run lower than Google's, hence the different priors.
# threshold: the adjusted rating must beat this to score at all.
# points_per_star: points per star above the threshold.
# max_points: cap per platform.
# min_reviews: below this, the rating is shown but never scores.
# ---------------------------------------------------------------------------
RATINGS = {
    "google": {"label": "Google", "prior_mean": 4.3, "prior_reviews": 150,
               "threshold": 4.4, "points_per_star": 10, "max_points": 3, "min_reviews": 50},
    "yelp":   {"label": "Yelp",   "prior_mean": 3.9, "prior_reviews": 75,
               "threshold": 4.0, "points_per_star": 8,  "max_points": 3, "min_reviews": 50},
}


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------

_chains_cache = {}


def load_chains(path=CHAINS_PATH):
    """Normalised chain names from data/chains.json (re-read when it changes)."""
    mtime = os.path.getmtime(path)
    cached = _chains_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        names = {normalize_name(n) for n in json.load(f)["names"]}
    names.discard("")
    _chains_cache[path] = (mtime, names)
    return names


def is_chain(name, chains):
    """'Chipotle Mexican Grill' matches blocklist entry 'Chipotle'."""
    n = normalize_name(name)
    return any(n == c or n.startswith(c + " ") for c in chains)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def bayesian_rating(rating, reviews, prior_mean, prior_reviews):
    return (reviews * rating + prior_reviews * prior_mean) / (reviews + prior_reviews)


def recognition_points(entries):
    """(points, breakdown rows, publisher groups that counted)."""
    by_group = {}
    for entry in entries:
        source = SOURCES.get(entry["source"])
        if source:
            by_group.setdefault(source["group"], []).append((source, entry))

    points, rows = 0.0, []
    for group, items in by_group.items():
        items.sort(key=lambda item: -item[0]["weight"])
        limit = GROUP_LIMITS.get(group, 1)
        for i, (source, entry) in enumerate(items):
            counted = i < limit
            if counted:
                points += source["weight"]
            rows.append({
                "kind": "recognition",
                "source": entry["source"],
                "text": source["label"],
                "url": entry.get("source_url") or None,
                "note": entry.get("note") or None,
                "placeholder": bool(entry.get("placeholder")),
                "points": source["weight"] if counted else 0,
                "why": None if counted else "already counted from this publisher",
            })

    rows.sort(key=lambda r: -r["points"])
    return points, rows, list(by_group)


def rating_points(platform, listing):
    """(points, breakdown row) for one platform's rating, or (0, None)."""
    rating, reviews = listing.get("rating"), listing.get("review_count") or 0
    if rating is None:
        return 0.0, None

    cfg = RATINGS[platform]
    adjusted = bayesian_rating(rating, reviews, cfg["prior_mean"], cfg["prior_reviews"])
    if reviews < cfg["min_reviews"]:
        points, why = 0.0, "fewer than %d reviews" % cfg["min_reviews"]
    else:
        raw = (adjusted - cfg["threshold"]) * cfg["points_per_star"]
        points = max(0.0, min(cfg["max_points"], raw))
        why = None if points > 0 else "adjusted rating %.2f is below %.1f" % (adjusted, cfg["threshold"])

    return points, {
        "kind": "rating",
        "source": platform,
        "text": "%s %.1f · %s reviews" % (cfg["label"], rating, format(reviews, ",")),
        "url": listing.get("url"),
        "rating": rating,
        "reviews": reviews,
        "adjusted": round(adjusted, 2),
        "strong": points > 0,
        "points": round(points, 1),
        "why": why,
    }


def score_place(place):
    """Score one merged place (see matching.py). Returns score, flags, breakdown."""
    total, rec_rows, groups = recognition_points(place.get("recognition", []))
    breakdown = list(rec_rows)

    if len(groups) > 1:
        bonus = AGREEMENT_BONUS * (len(groups) - 1)
        total += bonus
        breakdown.append({"kind": "agreement",
                          "text": "%d independent publishers agree" % len(groups),
                          "points": bonus})

    for platform in ("google", "yelp"):
        listing = place.get(platform)
        if listing:
            points, row = rating_points(platform, listing)
            if row:
                total += points
                breakdown.append(row)

    return {
        "score": round(total, 1),
        "recognised": bool(groups),
        "breakdown": breakdown,
    }
