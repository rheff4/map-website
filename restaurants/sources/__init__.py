"""
Third-party restaurant data. Each source turns its API's response into the
same "listing" shape:

    {'source': 'google'|'yelp', 'id', 'name', 'address', 'lat', 'lon',
     'price': 1-4 or None, 'cuisines': [{'key', 'label'}],
     'rating', 'review_count', 'url', 'closed': bool,
     'hours': [...] or None, 'utc_offset_minutes': int or None}

In demo mode the same parsers read saved responses from restaurants/fixtures/
instead of calling the network, so demo data exercises the real code.
"""

import json
import os

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def cache_ttl_seconds():
    try:
        hours = float(os.environ.get("CACHE_TTL_HOURS") or 168)
    except ValueError:
        hours = 168
    # Google's terms cap caching of most Places content at 30 days.
    return int(min(max(hours, 0), 720) * 3600)
