"""
Map Website - local server.

Serves the static site and each feature's JSON API from one process, so API
keys are read from .env on the server and never reach the browser.

    python server/app.py               # http://localhost:8000/
    set PORT=9000 & python server/app.py

Features plug in without editing this file: if <feature>/api.py exists and
defines register(router), it is loaded at startup. See server/router.py.
Standard library only - nothing to install.
"""

import importlib
import json
import os
import sys
import traceback
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import env  # noqa: E402
from server.router import ApiError, Request, Router  # noqa: E402

FEATURES = ("restaurants", "routes")

# Only these folders are served as files. Everything else - .env, .cache/,
# data/, server/ - stays private.
STATIC_DIRS = ("shared", "restaurants", "routes")
BLOCKED_SUFFIXES = (".py", ".pyc")

MAX_BODY_BYTES = 1024 * 1024


def is_public(url_path):
    parts = [p for p in urllib.parse.unquote(url_path).split("/") if p]
    if not parts or parts[0] not in STATIC_DIRS:
        return False
    if any(p.startswith((".", "__")) or "\\" in p for p in parts):
        return False
    return not parts[-1].lower().endswith(BLOCKED_SUFFIXES)


class Handler(SimpleHTTPRequestHandler):
    router = Router()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            return self.handle_api("GET", path)
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/shared/index.html")
            return self.end_headers()
        if not is_public(path):
            return self.send_error(404)
        return super().do_GET()

    def do_HEAD(self):
        if not is_public(urllib.parse.urlparse(self.path).path):
            return self.send_error(404)
        return super().do_HEAD()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            return self.handle_api("POST", path)
        return self.send_error(405)

    def end_headers(self):
        # Development server: always serve the latest JS/CSS after an edit.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def handle_api(self, method, path):
        handler = self.router.find(method, path)
        if handler is None:
            return self.send_json(404, {"error": "No API endpoint %s %s" % (method, path)})

        query = {k: v[-1] for k, v in urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query).items()}

        try:
            body = self.read_body() if method == "POST" else None
            result = handler(Request(method, path, query, body))
            status = 200
        except ApiError as e:
            status, result = e.status, {"error": e.message}
        except Exception:  # noqa: BLE001 - never leak a stack trace to the page
            traceback.print_exc()
            status, result = 500, {"error": "Internal server error - see the server log."}
        self.send_json(status, result)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "Request body too large.")
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError(400, "Request body must be JSON.")

    def send_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def load_features(router):
    for name in FEATURES:
        if not os.path.exists(os.path.join(ROOT, name, "api.py")):
            continue
        try:
            module = importlib.import_module(name + ".api")
            module.register(router)
            print("  loaded %s/api.py" % name)
        except Exception:  # noqa: BLE001 - one broken feature must not stop the other
            print("  FAILED to load %s/api.py:" % name)
            traceback.print_exc()


def main():
    loaded = env.load(os.path.join(ROOT, ".env"))
    print("Map Website server")
    print("  .env: %s" % (", ".join(loaded) if loaded else "not found or empty"))

    router = Router()
    load_features(router)
    Handler.router = router

    port = int(os.environ.get("PORT") or 8000)
    # Localhost only: this is a development server.
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("  open http://localhost:%d/" % port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
