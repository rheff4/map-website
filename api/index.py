"""
Vercel entry point: every /api/* request lands here (see vercel.json) and is
answered by the same handlers as the local server. Vercel serves the static
files itself.

Set ORS_API_KEY in the Vercel project's environment variables. TRUST_PROXY
and CACHE_DIR are set below because they are always right on Vercel.

Nothing at module level may be called `app`: Vercel's Python runtime treats a
variable of that name as a WSGI/ASGI app and would run it instead of
`handler`. So the local server module is imported under another name.
"""

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Vercel's proxy reports the visitor's address; only /tmp is writable.
# Both must be set before the features import server.cache.
os.environ.setdefault("TRUST_PROXY", "1")
os.environ.setdefault("CACHE_DIR", "/tmp/map-website-cache")

try:
    from server import app as local_server
    from server.router import Router

    _router = Router()
    local_server.load_features(_router)
    local_server.Handler.router = _router
    _Base = local_server.Handler
    _startup_error = None
except Exception as e:  # noqa: BLE001 - say why on every request, not just in the log
    traceback.print_exc()
    _Base = BaseHTTPRequestHandler
    _startup_error = "%s: %s" % (type(e).__name__, e)


class handler(_Base):  # noqa: N801 - the name Vercel looks for
    if _startup_error:
        def do_GET(self):
            data = json.dumps({"error": "Server failed to start - " + _startup_error}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET
