"""
Restart the server whenever Python code changes.

A running Python process keeps the code it started with, so after a
`git pull`, a branch switch or an edit, an already-running server silently
serves old code. server/app.py therefore runs as a small watcher by default:
it starts the real server as a child process and restarts it when any .py
file in the repo changes. Data files (JSON) are read on every request and
need no restart.

    python server/app.py               # watcher + server (default)
    python server/app.py --no-reload   # just the server
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD_ENV = "MAPWEBSITE_SERVER_CHILD"
SKIP_DIRS = {".git", ".cache", "__pycache__", "node_modules", ".venv", "venv", "spikes"}

# Exit code the server uses when its port is taken: the watcher stops too,
# instead of restarting forever.
EXIT_PORT_IN_USE = 3


def is_child():
    return os.environ.get(CHILD_ENV) == "1"


def _snapshot():
    mtimes = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                try:
                    mtimes[path] = os.stat(path).st_mtime
                except OSError:
                    pass
    return mtimes


def _start():
    env = dict(os.environ, **{CHILD_ENV: "1"})
    # -u: unbuffered, so the server's startup lines appear immediately.
    return subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "server", "app.py")], env=env)


def _stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def run():
    """Watch the code and keep a fresh server running. Returns an exit code."""
    snapshot = _snapshot()
    child = _start()
    crashed = False
    try:
        while True:
            time.sleep(1)
            if child.poll() is not None and not crashed:
                if child.returncode == EXIT_PORT_IN_USE:
                    return 1
                if child.returncode != 0:
                    crashed = True
                    print("\n  The server stopped with an error (above). Fix the code and save -"
                          " it restarts automatically.\n", flush=True)
            current = _snapshot()
            if current != snapshot:
                changed = sorted(set(current) ^ set(snapshot) |
                                 {p for p in current if snapshot.get(p) != current[p]})
                names = ", ".join(os.path.relpath(p, ROOT) for p in changed[:3])
                more = " and %d more" % (len(changed) - 3) if len(changed) > 3 else ""
                print("\n  Code changed (%s%s) - restarting the server...\n" % (names, more), flush=True)
                snapshot = current
                _stop(child)
                child = _start()
                crashed = False
    except KeyboardInterrupt:
        return 0
    finally:
        _stop(child)
