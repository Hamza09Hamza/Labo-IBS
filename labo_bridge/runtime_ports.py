"""
Live port override store - lets the admin UI change a machine's listen port
WITHOUT restarting run_all.py.

Why this exists: a listening socket can't be rebound to a different port
while running - the only way to "change the port" is to close the old
socket and open a new one. server.py's _serve_one_machine() already polls
in a loop (1s accept() timeout) to check a stop_event; it now also checks
this file on every tick and rebinds if the desired port changed.

runtime_ports.json holds {machine: port} overrides on top of
server.MACHINES's baked-in defaults. Only written by the admin UI
(labo_bridge/admin/app.py), only read by the listener threads - a single
JSON file is a simple, good-enough IPC mechanism for a single-operator local
tool checking in about once a second.
"""

import json
import threading
from pathlib import Path

PORTS_FILE = Path(__file__).resolve().parent.parent / "runtime_ports.json"

_lock = threading.Lock()


def get_overrides() -> dict:
    """Return {machine: port} overrides, or {} if the file doesn't exist yet."""
    if not PORTS_FILE.exists():
        return {}
    try:
        with _lock:
            return json.loads(PORTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def set_override(machine: str, port: int) -> None:
    with _lock:
        data = get_overrides()
        data[machine] = port
        PORTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_port_for(machine: str, default_port: int) -> int:
    return get_overrides().get(machine, default_port)
