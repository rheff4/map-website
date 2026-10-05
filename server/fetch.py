"""Outbound JSON requests to third-party APIs. Standard library only."""

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "map-website/0.1 (student project)"


class UpstreamError(Exception):
    """A third-party API failed. The message is safe to show in the UI."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _error_detail(body):
    # Google and Yelp both return {"error": {"message"|"description": ...}}.
    try:
        err = json.loads(body).get("error", {})
        return err.get("message") or err.get("description") or ""
    except (ValueError, AttributeError):
        return ""


def request_json(method, url, headers=None, body=None, timeout=20):
    data = None
    headers = dict(headers or {})
    headers.setdefault("User-Agent", USER_AGENT)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    host = urllib.parse.urlparse(url).netloc
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _error_detail(e.read().decode("utf-8", "replace"))
        raise UpstreamError(
            "%s returned HTTP %d%s" % (host, e.code, (": " + detail[:200]) if detail else ""),
            status=e.code,
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise UpstreamError("Could not reach %s (%s)" % (host, getattr(e, "reason", e)))
