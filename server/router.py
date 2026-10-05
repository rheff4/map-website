"""
How a feature adds API endpoints.

Each feature folder may contain api.py with a register(router) function:

    from server.router import ApiError

    def register(router):
        router.get('/api/routes/generate', generate)

    def generate(request):
        km = request.number('km', default=5, low=1, high=42)
        ...
        return {'route': geojson}          # sent as JSON, status 200

Raise ApiError(400, 'message') for bad input; the message reaches the browser.
Any other exception becomes a 500 with the details only in the server log.
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    def __init__(self, method, path, query, body):
        self.method = method
        self.path = path
        self.query = query    # {name: last value}, all strings
        self.body = body      # parsed JSON for POST, else None

    def number(self, name, default=None, low=None, high=None):
        """A numeric query parameter, validated. Missing -> default."""
        raw = self.query.get(name)
        if raw is None or raw == "":
            if default is None:
                raise ApiError(400, "Missing parameter '%s'." % name)
            return default
        try:
            value = float(raw)
        except ValueError:
            raise ApiError(400, "Parameter '%s' must be a number." % name)
        if value != value or value in (float("inf"), float("-inf")):
            raise ApiError(400, "Parameter '%s' must be a number." % name)
        if low is not None and value < low:
            raise ApiError(400, "Parameter '%s' must be at least %s." % (name, low))
        if high is not None and value > high:
            raise ApiError(400, "Parameter '%s' must be at most %s." % (name, high))
        return value


class Router:
    def __init__(self):
        self.routes = {}

    def get(self, path, handler):
        self._add("GET", path, handler)

    def post(self, path, handler):
        self._add("POST", path, handler)

    def _add(self, method, path, handler):
        if not path.startswith("/api/"):
            raise ValueError("API paths must start with /api/: %s" % path)
        if (method, path) in self.routes:
            raise ValueError("Route already registered: %s %s" % (method, path))
        self.routes[(method, path)] = handler

    def find(self, method, path):
        return self.routes.get((method, path))
