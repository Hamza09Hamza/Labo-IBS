"""Standalone Flask application for operating-chamber monitoring."""

import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from clinical_portal.configuration import public_config, update_machine
from clinical_portal.store import WINDOWS, store


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
