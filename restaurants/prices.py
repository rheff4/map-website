"""
What a place's $-$$$$ level means in dollars.

The same symbol means different money on different sources, so the level and
the dollar text always come from the same source:

1. Google's priceRange - a real range for that restaurant ("$20-30").
2. Yelp's level, read with Yelp's per-person bands.
3. Michelin's level (from the recognition file), read with Michelin's bands
   for a full meal without drinks.
4. Google's level alone - Google publishes no dollar amounts for it.

Bands: https://guide.michelin.com/en/restaurants-price-ranges-index (Michelin)
and Yelp's long-standing per-person guide.
"""

BANDS = {
    "yelp": {1: "under $10", 2: "$11–30", 3: "$31–60", 4: "over $60"},
    "michelin": {1: "under $25", 2: "$25–50", 3: "$50–75", 4: "over $75"},
}

NOTES = {
    "google": "Google's price range for this restaurant, per person",
    "yelp": "Yelp's band, per person",
    "michelin": "MICHELIN Guide's band, a full meal without drinks",
}

CURRENCY_SIGNS = {"USD": "$"}


def _amount(money):
    """A google.type.Money -> whole units as an int, or None."""
    try:
        return int(money["units"]) if money and money.get("units") is not None else None
    except (TypeError, ValueError):
        return None


def range_text(price_range):
    """Google's {'startPrice', 'endPrice'} -> '$20–30' or '$100+', or None."""
    if not price_range:
        return None
    start = price_range.get("startPrice") or {}
    low, high = _amount(start), _amount(price_range.get("endPrice"))
    if low is None:
        return None
    code = start.get("currencyCode") or "USD"
    sign = CURRENCY_SIGNS.get(code, code + " ")
    return "%s%d–%d" % (sign, low, high) if high is not None else "%s%d+" % (sign, low)


def describe(place, listings):
    """{'price': 1-4 or None, 'price_text': str or None, 'price_note': str or None}."""
    google = place.get("google")
    yelp = place.get("yelp")
    michelin = [e["price"] for e in place["recognition"]
                if e.get("price") and e["source"].startswith("michelin")]

    if google and google.get("price_text"):
        return {"price": google.get("price"), "price_text": google["price_text"],
                "price_note": NOTES["google"]}
    if yelp and yelp.get("price"):
        return {"price": yelp["price"], "price_text": BANDS["yelp"][yelp["price"]],
                "price_note": NOTES["yelp"]}
    if michelin:
        level = max(michelin)
        return {"price": level, "price_text": BANDS["michelin"][level],
                "price_note": NOTES["michelin"]}
    level = next((l["price"] for l in listings if l.get("price")), None)
    return {"price": level, "price_text": None, "price_note": None}
