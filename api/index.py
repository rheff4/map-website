"""
Vercel entry point: every /api/* request lands here (see vercel.json) and is
answered by the same handlers as the local server. Vercel serves the static
files itself.

Set ORS_API_KEY in the Vercel project's environment variables. TRUST_PROXY
and CACHE_DIR are set below because they are always right on Vercel.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Vercel's proxy reports the visitor's address; only /tmp is writable.
# Both must be set before the features import server.cache.
os.environ.setdefault("TRUST_PROXY", "1")
os.environ.setdefault("CACHE_DIR", "/tmp/map-website-cache")

from server import app  # noqa: E402
from server.router import Router  # noqa: E402

_router = Router()
app.load_features(_router)
app.Handler.router = _router


class handler(app.Handler):  # noqa: N801 - the name Vercel looks for
    pass
