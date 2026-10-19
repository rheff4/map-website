"""
Copy the slow-to-fetch cache entries into data/cache-seed/, which ships with
the site, so a fresh serverless instance (Vercel) can answer at once.

    python -m server.seed_cache

Run it after a local restaurant search has filled .cache/, then commit
data/cache-seed/. Re-run and redeploy to refresh what a deploy starts with.
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import cache  # noqa: E402

# Overpass's city-wide food snapshot takes minutes; the licence register ~30 s.
NAMESPACES = ("osm-food-bbox", "licences")


def main():
    copied = 0
    for namespace in NAMESPACES:
        src = os.path.join(cache.CACHE_DIR, namespace)
        if not os.path.isdir(src):
            print("  %s: nothing cached yet - run a restaurant search first" % namespace)
            continue
        dst = os.path.join(cache.SEED_DIR, namespace)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.tmp"))
        n = len(os.listdir(dst))
        copied += n
        print("  %s: %d entr%s" % (namespace, n, "y" if n == 1 else "ies"))
    return 0 if copied else 1


if __name__ == "__main__":
    sys.exit(main())
