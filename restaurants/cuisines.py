"""
One cuisine vocabulary for Google place types and Yelp category aliases, so the
cuisine filter works the same whichever source a restaurant came from.
"""

# canonical key -> label shown on the page
LABELS = {
    "american": "American", "bakery": "Bakery", "breakfast": "Breakfast & brunch",
    "burgers": "Burgers", "cafe": "Cafe", "chinese": "Chinese", "french": "French",
    "greek": "Greek", "indian": "Indian", "italian": "Italian", "japanese": "Japanese",
    "korean": "Korean", "mediterranean": "Mediterranean", "mexican": "Mexican",
    "middle_eastern": "Middle Eastern", "pizza": "Pizza", "sandwiches": "Sandwiches",
    "seafood": "Seafood", "spanish": "Spanish", "steakhouse": "Steakhouse",
    "thai": "Thai", "vegetarian": "Vegetarian & vegan", "vietnamese": "Vietnamese",
}

# Google place type -> canonical key. Types ending in "_restaurant" that are
# not listed here use their prefix ("brazilian_restaurant" -> "brazilian").
GOOGLE_TYPES = {
    "american_restaurant": "american", "hamburger_restaurant": "burgers",
    "breakfast_restaurant": "breakfast", "brunch_restaurant": "breakfast",
    "ramen_restaurant": "japanese", "sushi_restaurant": "japanese",
    "steak_house": "steakhouse", "lebanese_restaurant": "middle_eastern",
    "turkish_restaurant": "middle_eastern", "vegan_restaurant": "vegetarian",
    "vegetarian_restaurant": "vegetarian", "pizza_restaurant": "pizza",
    "sandwich_shop": "sandwiches", "bakery": "bakery", "cafe": "cafe",
}

# Not cuisines.
GOOGLE_IGNORED = {"restaurant", "fast_food_restaurant", "food", "point_of_interest",
                  "establishment", "bar", "meal_takeaway", "meal_delivery"}

# Yelp category alias -> canonical key. Unlisted aliases keep their own name.
YELP_ALIASES = {
    "newamerican": "american", "tradamerican": "american", "diners": "american",
    "indpak": "indian", "mideastern": "middle_eastern", "lebanese": "middle_eastern",
    "turkish": "middle_eastern", "sushi": "japanese", "ramen": "japanese",
    "steak": "steakhouse", "spanishbasque": "spanish", "tapas": "spanish",
    "breakfast_brunch": "breakfast", "vegan": "vegetarian", "bakeries": "bakery",
    "cafes": "cafe", "coffee": "cafe", "sandwiches": "sandwiches",
}

YELP_IGNORED = {"restaurants", "bars", "food", "cocktailbars", "wine_bars", "pubs"}


def _entry(key, fallback_label=None):
    label = LABELS.get(key) or fallback_label or key.replace("_", " ").capitalize()
    return {"key": key, "label": label}


def from_google(primary_type, types):
    out, seen = [], set()
    for t in [primary_type] + list(types or []):
        if not t or t in GOOGLE_IGNORED:
            continue
        key = GOOGLE_TYPES.get(t)
        if key is None and t.endswith("_restaurant"):
            key = t[: -len("_restaurant")]
        if key and key not in seen:
            seen.add(key)
            out.append(_entry(key))
    return out


def from_yelp(categories):
    out, seen = [], set()
    for c in categories or []:
        alias = c.get("alias")
        if not alias or alias in YELP_IGNORED:
            continue
        key = YELP_ALIASES.get(alias, alias)
        if key not in seen:
            seen.add(key)
            out.append(_entry(key, c.get("title")))
    return out


def merge(*lists):
    out, seen = [], set()
    for items in lists:
        for item in items or []:
            if item["key"] not in seen:
                seen.add(item["key"])
                out.append(item)
    return out
