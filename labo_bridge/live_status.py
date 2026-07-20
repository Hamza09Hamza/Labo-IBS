"""
In-memory live connection status per machine - "listening" vs "connected".

Only meaningful when the admin UI runs in the SAME process as the listeners
(the normal case via run_all.py, which starts both). If the admin UI is run
standalone (python -m labo_bridge.admin, listeners elsewhere/not running),
every machine correctly shows as unknown/not-listening here since there's no
shared listener thread updating it.

Thread-safe via a simple lock - updated by server.py's accept loop (one
writer per machine's own thread), read by the admin UI's Flask routes
(readers only, any thread).
"""

import threading

_lock = threading.Lock()
_status = {}  # machine -> {"state": "listening"|"connected", "since": iso str, "source_ip": str|None}


def set_listening(machine: str, since: str) -> None:
    with _lock:
        _status[machine] = {"state": "listening", "since": since, "source_ip": None}


def set_connected(machine: str, since: str, source_ip: str) -> None:
    with _lock:
        _status[machine] = {"state": "connected", "since": since, "source_ip": source_ip}


def get(machine: str) -> dict:
    with _lock:
        return dict(_status.get(machine, {"state": "unknown", "since": None, "source_ip": None}))


def get_all() -> dict:
    with _lock:
        return {m: dict(v) for m, v in _status.items()}
