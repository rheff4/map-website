"""
Reads secrets from .env into the environment.

.env is gitignored; .env.example lists the names. Real environment variables
win over .env, so a key set in the shell is never silently replaced.
"""

import os

# Values copied unchanged from .env.example are treated as "not set".
PLACEHOLDER_PREFIXES = ("your-", "changeme")


def load(path):
    """Load KEY=VALUE lines from path. Returns the names that were set."""
    if not os.path.exists(path):
        return []

    loaded = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded


def secret(name):
    """The value of an API key, or None if it is missing or still a placeholder."""
    value = (os.environ.get(name) or "").strip()
    if not value or value.lower().startswith(PLACEHOLDER_PREFIXES):
        return None
    return value
