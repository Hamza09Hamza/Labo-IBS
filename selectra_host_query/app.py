"""Flask API and static UI for the isolated Selectra Host Query bench."""

from __future__ import annotations

import os
import re
from datetime import date

from flask import Flask, jsonify, request, send_from_directory

from .protocol import build_order_records


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SAFE_VALUE = re.compile(r"^[^|\\^&\r\n]+$")
ASSAY_SUGGESTIONS = [
    "Uree uv sl", "Cholesterol", "SGOT", "SGPT", "Phosphatase Alc",
    "Phosphatase ALP", "Creatinine", "GGT", "Glucose pap sl", "Acide Urique",
    "Calcium", "CALCUIM", "Phosphore", "Triglycerides", "Cholesterol HDL",
    "LDH-L SL", "Proteines U", "CRP IP V3", "CRP IP v3", "BILI TOTAL BIO",
    "BILI DIRECT BIO", "CK NAK", "CK-NAC", "Proteine totale", "Albumine",
]


def _validate_text(name, value, required=True, maximum=80):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > maximum:
        raise ValueError(f"{name} must be {maximum} characters or fewer")
    if value and not SAFE_VALUE.fullmatch(value):
        raise ValueError(f"{name} contains a reserved LIS2-A delimiter")
    return value


def _validated_order(body):
    tests = body.get("tests") or []
    if isinstance(tests, str):
        tests = [part.strip() for part in tests.split(",") if part.strip()]
    tests = list(dict.fromkeys(_validate_text("test code", value, maximum=60) for value in tests))
    if not tests:
        raise ValueError("at least one Selectra test code is required")
    if len(tests) > 40:
        raise ValueError("no more than 40 tests can be staged in one order")
    birth_date = str(body.get("birth_date") or "").strip()
    if birth_date:
        try:
            date.fromisoformat(birth_date)
        except ValueError:
            raise ValueError("birth date must use YYYY-MM-DD") from None
    sex = str(body.get("sex") or "U").upper()
    if sex not in {"M", "F", "U"}:
        raise ValueError("sex must be M, F, or U")
    sample_id = _validate_text("sample ID", body.get("sample_id"), maximum=64)
    return {
        "sample_id": sample_id,
        "patient_id": _validate_text("patient ID", body.get("patient_id") or sample_id, maximum=64),
        "family_name": _validate_text("family name", body.get("family_name"), maximum=80),
        "given_name": _validate_text("given name", body.get("given_name"), maximum=80),
        "birth_date": birth_date,
        "sex": sex,
        "specimen_type": _validate_text("specimen type", body.get("specimen_type") or "SERUM", maximum=32),
        "tests": tests,
    }


def create_app(store, service):
    app = Flask(__name__, static_folder=None)

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_file(filename):
        return send_from_directory(STATIC_DIR, filename)

    @app.get("/api/status")
    def status():
        return jsonify({**service.status(), "orders": len(store.list_orders())})

    @app.get("/api/assays")
    def assays():
        return jsonify({"assays": ASSAY_SUGGESTIONS, "validated_for_order_download": False})

    @app.get("/api/orders")
    def orders():
        return jsonify({"orders": store.list_orders()})

    @app.post("/api/orders")
    def stage_order():
        try:
            order = _validated_order(request.get_json(silent=True) or {})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        saved = store.upsert_order(order)
        return jsonify({"ok": True, "order": saved, "response_preview": build_order_records(saved)}), 201

    @app.post("/api/simulate-query")
    def simulate_query():
        sample_id = str((request.get_json(silent=True) or {}).get("sample_id") or "").strip()
        if not sample_id:
            return jsonify({"error": "sample ID is required"}), 400
        try:
            records = service.preview(sample_id, simulated=True)
        except KeyError:
            return jsonify({"error": "no staged order exactly matches that sample ID"}), 404
        return jsonify({"ok": True, "sample_id": sample_id, "response_records": records})

    @app.post("/api/live-responses")
    def live_responses():
        body = request.get_json(silent=True) or {}
        armed = body.get("armed") is True
        if armed and body.get("confirmation") != "ARM SELECTRA":
            return jsonify({"error": "explicit ARM SELECTRA confirmation is required"}), 400
        service.set_armed(armed)
        return jsonify({"ok": True, "armed": service.armed})

    @app.get("/api/events")
    def events():
        try:
            after = max(0, int(request.args.get("after", "0")))
        except ValueError:
            return jsonify({"error": "after must be an integer"}), 400
        return jsonify({"events": store.list_events(after=after)})

    return app
