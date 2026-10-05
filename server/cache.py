"""
Disk cache for outbound API responses, so repeated searches do not cost money.

Entries are JSON files under .cache/<namespace>/, which is gitignored. The key
is any JSON-serialisable value - never put an API key in it.

    from server import cache
    data = cache.cached('google-search', {'lat': 42.355, 'r': 1500},
                        ttl_seconds=86400, compute=lambda: call_the_api())
"""

import hashlib
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, ".cache")

_write_lock = threading.Lock()


def _path(namespace, key):
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return os.path.join(CACHE_DIR, namespace, digest + ".json")


def get(namespace, key, ttl_seconds):
    """The cached value, or None if missing or older than ttl_seconds."""
    path = _path(namespace, key)
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - entry.get("saved_at", 0) > ttl_seconds:
        return None
    return entry.get("value")


def put(namespace, key, value):
    path = _path(namespace, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with _write_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "key": key, "value": value}, f)
        os.replace(tmp, path)


def cached(namespace, key, ttl_seconds, compute):
    """Return the cached value, or compute it, store it and return it.

    Exceptions from compute() propagate and nothing is stored, so a failed
    API call is retried next time instead of being remembered.
    """
    value = get(namespace, key, ttl_seconds)
    if value is not None:
        return value
    value = compute()
    if value is not None:
        put(namespace, key, value)
    return value
