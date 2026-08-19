"""Standalone Flask application for operating-chamber monitoring."""

import logging
import os
import socket
import time

from flask import Flask, jsonify, request, send_from_directory

from clinical_portal.configuration import public_config, update_machine
from clinical_portal.history import recorder
from clinical_portal.store import SOURCES, WINDOWS, store


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=None)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    return _no_cache(send_from_directory(STATIC_DIR, "index.html"))


@app.route("/<path:filename>")
def static_files(filename):
    return _no_cache(send_from_directory(STATIC_DIR, filename))


def _window_argument():
    try:
        window = int(request.args.get("window", "10"))
    except ValueError:
        return None
    return window if window in WINDOWS else None


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "application": "operationbloc-bridge"})


def _configured_snapshot(snapshot):
    """Attach editable block/machine metadata to a live store snapshot."""
    configuration = public_config()
    by_id = {item["id"]: item for item in configuration["blocks"]}
    chambers = snapshot.get("chambers") if "chambers" in snapshot else [snapshot]
    for chamber_item in chambers:
        block = by_id.get(chamber_item["id"])
        if not block:
            continue
        chamber_item["name"] = block["name"]
        chamber_item["code"] = block["code"]
        chamber_item["configuration"] = block
        for device in chamber_item.get("devices") or []:
            machine = block["machines"].get(device["source"])
            if machine:
                device["label"] = machine["label"]
                device["kind"] = machine["kind"]
                device["configuration"] = machine
    snapshot["application_name"] = configuration["application_name"]
    snapshot["restart_required"] = configuration["restart_required"]
    return snapshot


@app.route("/api/config")
def configuration():
    return jsonify(public_config())


@app.route("/api/blocks/<int:block_id>/machines/<source>/config", methods=["PUT"])
def save_machine_configuration(block_id, source):
    if request.form:
        fields = request.form
        photo = request.files.get("photo")
    else:
        fields = request.get_json(silent=True) or {}
        photo = None
    try:
        result = update_machine(block_id, source, fields, photo)
    except KeyError:
        return jsonify({"error": "unknown block or machine"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        app.logger.exception("could not save OperationBloc configuration")
        return jsonify({"error": f"could not save configuration: {exc}"}), 500
    return jsonify({"ok": True, "restart_required": True, "block": result})


@app.route("/api/chambers")
def chambers():
    window = _window_argument()
    if window is None:
        return jsonify({"error": "window must be one of 0, 10, 20, 30, 60 seconds"}), 400
    return jsonify(_configured_snapshot(store.overview(window)))


@app.route("/api/chambers/<int:chamber_id>")
def chamber(chamber_id):
    window = _window_argument()
    if window is None:
        return jsonify({"error": "window must be one of 0, 10, 20, 30, 60 seconds"}), 400
    try:
        return jsonify(_configured_snapshot(store.chamber(chamber_id, window)))
    except KeyError:
        return jsonify({"error": "unknown chamber"}), 404


UMEC12_PDS_PORT = 4601


def _resolve_machine(block_id, source_name):
    """Look up a machine by block ID and machine name (e.g. "umec12", "WATO").

    Raises ValueError with a caller-facing message if either is unknown.
    """
    source = str(source_name or "").strip().lower()
    if source not in SOURCES:
        raise ValueError(f"unknown machine name '{source_name}'; use one of {sorted(SOURCES)}")
    configuration = public_config()
    block = next((item for item in configuration["blocks"] if item["id"] == block_id), None)
    if block is None:
        raise ValueError("unknown operation block")
    return block, block["machines"][source]


@app.route("/api/machines/<int:block_id>/<source>")
def machine_latest(block_id, source):
    """Latest reading name/value/unit for one machine, by block ID and machine name."""
    try:
        block, machine = _resolve_machine(block_id, source)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    try:
        chamber_snapshot = store.chamber(block_id, 10)
    except KeyError:
        return jsonify({"error": "unknown operation block"}), 404
    device = next((item for item in chamber_snapshot["devices"] if item["source"] == source), None)
    readings = [
        {"name": parameter["label"], "value": parameter["latest"], "unit": parameter["unit"]}
        for parameter in (device["parameters"] if device else [])
    ]
    return jsonify({
        "block_id": block_id,
        "block_name": block["name"],
        "source": source,
        "machine_name": machine["label"],
        "state": device["state"] if device else "offline",
        "last_seen": device["last_seen"] if device else None,
        "readings": readings,
    })


@app.route("/api/machines/<int:block_id>/<source>/history")
def machine_history(block_id, source):
    try:
        _resolve_machine(block_id, source)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return jsonify({"error": "limit must be a number"}), 400
    limit = max(1, min(limit, 1000))
    code = request.args.get("code") or None
    rows = recorder.recent(block_id, source, code, limit)
    return jsonify({"block_id": block_id, "source": source, "rows": rows})


def _tcp_ping(ip, port, timeout=2.0):
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
        return True, round((time.monotonic() - started) * 1000, 1), None
    except OSError as exc:
        return False, None, str(exc)


@app.route("/api/machines/<int:block_id>/<source>/ping", methods=["POST"])
def machine_ping(block_id, source):
    try:
        block, machine = _resolve_machine(block_id, source)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    base = {
        "block_id": block_id,
        "block_name": block["name"],
        "source": source,
        "machine_name": machine["label"],
    }
    if source == "umec12":
        ip = machine["ip"]
        if not ip:
            return jsonify({**base, "pingable": False, "reason": "no monitor IP configured"}), 400
        ok, latency_ms, error = _tcp_ping(ip, UMEC12_PDS_PORT)
        return jsonify({
            **base, "pingable": True, "ok": ok, "ip": ip, "port": UMEC12_PDS_PORT,
            "latency_ms": latency_ms, "error": error,
        })
    # WATO only connects outbound to us (it's an HL7 listener on our side);
    # we never learn the machine's own IP, so there is nothing to dial. The
    # closest honest signal is whether it has actually been talking to us.
    try:
        chamber_snapshot = store.chamber(block_id, 10)
    except KeyError:
        chamber_snapshot = None
    device = next(
        (item for item in (chamber_snapshot["devices"] if chamber_snapshot else [])
         if item["source"] == source),
        None,
    )
    return jsonify({
        **base, "pingable": False,
        "reason": "WATO is a listener; the bridge cannot dial out to it. "
                  "Reporting its last known connection state instead.",
        "device_state": device["state"] if device else "offline",
        "last_seen": device["last_seen"] if device else None,
    })


@app.route("/api/chambers/<int:chamber_id>/readings", methods=["POST"])
def ingest(chamber_id):
    # Device collectors are child processes of run_clinical.py and publish
    # through localhost. LAN users may read the portal but cannot inject
    # measurements into it.
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "reading ingestion is restricted to this host"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    try:
        accepted = store.ingest(chamber_id, payload)
    except ValueError as exc:
        status = 404 if str(exc) == "unknown chamber" else 400
        return jsonify({"error": str(exc)}), status
    return jsonify({"ok": True, "accepted": accepted})
