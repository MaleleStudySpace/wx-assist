"""Small, dependency-free status snapshots for RAG settings and progress."""

import copy
import threading
import time

_lock = threading.Lock()
_state = {"phase": "idle", "error": "", "hardware": {}, "jobs": {}}


def reset(phase="idle"):
    with _lock:
        _state.update(phase=phase, error="", hardware={}, jobs={})


def update(**values):
    with _lock:
        _state.update(values)


def job(name, *, total=None, completed=None, state="running", error="", unit="条"):
    with _lock:
        previous = _state["jobs"].get(name, {})
        started = previous.get("started", time.time())
        if total is not None and completed == 0:
            started = time.time()
        _state["jobs"][name] = {
            "total": previous.get("total", 0) if total is None else total,
            "completed": previous.get("completed", 0) if completed is None else completed,
            "state": state, "error": error, "unit": unit, "started": started,
            "finished": time.time() if state in ("done", "error", "paused") else None,
        }


def snapshot():
    with _lock:
        result = copy.deepcopy(_state)
    for value in result["jobs"].values():
        value["elapsed_seconds"] = max(0, int((value.pop("finished") or time.time()) - value.pop("started")))
    return result
