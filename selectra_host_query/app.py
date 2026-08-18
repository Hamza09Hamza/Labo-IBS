"""Flask API and static UI for controlled analyzer order downloads."""

from __future__ import annotations

import os
import hmac
import random
import re
from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory

from labo_bridge import mappings as bridge_mappings, pg
from cyanvision_worklist.programs import CRE_DSP8_TRIAL_SEQUENCE, WORKLIST_PROGRAM_IDS

from .protocol import build_order_records, test_abbreviation


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SAFE_VALUE = re.compile(r"^[^|\\^&\r\n]+$")
ASSAY_SUGGESTIONS = [
    "Uree uv sl", "Cholesterol", "SGOT", "SGPT", "Phosphatase Alc",
    "Phosphatase ALP", "Creatinine", "GGT", "Glucose pap sl", "Acide Urique",
    "Calcium", "CALCUIM", "Phosphore", "Triglycerides", "Cholesterol HDL",
    "LDH-L SL", "Proteines U", "CRP IP V3", "CRP IP v3", "BILI TOTAL BIO",
    "BILI DIRECT BIO", "CK NAK", "CK-NAC", "Proteine totale", "Albumine",
]

# Some clinic targets have more than one historical/inbound method label.
# When those aliases would produce different analyzer codes, use the method
# confirmed for order download instead of relying on dictionary order.
PREFERRED_ORDER_METHODS = {
    "selectra": {
        (None, 481): "Phosphatase ALP",
        (99736, 366): "CALCUIM",
    },
}

# Non-clinical placeholder demographics, used to auto-fill any field the
# operator leaves blank when staging a bench order. Only sample_id has to
# match what's typed/scanned on the Selectra; everything else here is
# throwaway test data, not real patient information.
_RANDOM_FAMILY_NAMES = [
    "BENCHOR", "TESTIER", "DEMOUX", "SAMPLARD", "VERIFONT", "STAGEAU",
    "TRIALCOT", "MOCKAIN", "PROBEDOU", "CHECKARD",
]
_RANDOM_GIVEN_NAMES = [
    "ALEX", "SASHA", "TAYLOR", "MORGAN", "JULES", "ROBIN", "CASEY",
    "REMY", "LEO", "NOA",
]


def _random_patient_id():
    return f"BENCH-{random.randint(100000, 999999)}"


def _random_birth_date():
    start = date(1945, 1, 1)
    end = date(2015, 12, 31)
    span_days = (end - start).days
    return (start + timedelta(days=random.randint(0, span_days))).isoformat()


def _random_sex():
    return random.choice(["M", "F"])


def _random_test():
    return random.choice(ASSAY_SUGGESTIONS)


def _validate_text(name, value, required=True, maximum=80):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{name} must be {maximum} characters or fewer")
    if value and not SAFE_VALUE.fullmatch(value):
        raise ValueError(f"{name} contains a reserved protocol delimiter")
    return value


def _validated_order(body):
    # Only sample_id is required from the operator. Every other field is
    # auto-filled with random non-clinical placeholder data when left blank,
    # so staging a controlled bench order doesn't require typing
    # demographics by hand each time.
    tests = body.get("tests") or []
    if isinstance(tests, str):
        tests = [part.strip() for part in tests.split(",") if part.strip()]
    tests = list(dict.fromkeys(_validate_text("test code", value, maximum=60) for value in tests))
    if not tests:
        tests = [_random_test()]
    if len(tests) > 40:
        raise ValueError("no more than 40 tests can be staged in one order")
    for test in tests:
        test_abbreviation(test)
    birth_date = str(body.get("birth_date") or "").strip() or _random_birth_date()
    try:
        date.fromisoformat(birth_date)
    except ValueError:
        raise ValueError("birth date must use YYYY-MM-DD") from None
    sex = str(body.get("sex") or "").upper() or _random_sex()
    if sex not in {"M", "F", "U"}:
        raise ValueError("sex must be M, F, or U")
    sample_id = _validate_text("sample ID", body.get("sample_id"), maximum=None)
    family_name = str(body.get("family_name") or "").strip() or random.choice(_RANDOM_FAMILY_NAMES)
    given_name = str(body.get("given_name") or "").strip() or random.choice(_RANDOM_GIVEN_NAMES)
    return {
        "sample_id": sample_id,
        "patient_id": _validate_text(
            "patient ID", body.get("patient_id") or _random_patient_id(), maximum=64
        ),
        "family_name": _validate_text("family name", family_name, maximum=80),
        "given_name": _validate_text("given name", given_name, maximum=80),
        "birth_date": birth_date,
        "sex": sex,
        "specimen_type": _validate_text("specimen type", body.get("specimen_type") or "SERUM", maximum=32),
        "tests": tests,
    }


def _require_ascii(order, analyzer):
    values = []
    for value in order.values():
        values.extend(value if isinstance(value, list) else [value])
    if any(
        any(ord(character) < 32 or ord(character) > 126 for character in str(value or ""))
        for value in values
    ):
        raise ValueError(f"{analyzer} fields must use printable ASCII characters")


def _optional_positive_id(field, value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a positive integer") from None
    if parsed <= 0 or str(value).strip() != str(parsed):
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _resolve_machine_test(machine, requested):
    """Reverse a clinic target into one exact curated analyzer method."""
    if not isinstance(requested, dict):
        raise ValueError(
            f"{machine} test must contain param_id and/or service_tarification_id"
        )
    param_id = _optional_positive_id("param_id", requested.get("param_id"))
    service_id = _optional_positive_id(
        "service_tarification_id", requested.get("service_tarification_id"),
    )
    if param_id is None and service_id is None:
        raise ValueError("each test must contain param_id and/or service_tarification_id")

    candidates = []
    for method, targets in bridge_mappings.MAPS.get(machine, {}).items():
        for target_param, target_service, *_ in targets:
            if param_id is not None and target_param != param_id:
                continue
            if service_id is not None and target_service != service_id:
                continue
            wire_code = test_abbreviation(method) if machine == "selectra" else method
            candidates.append({
                "machine_test": method,
                "machine_code": wire_code,
                "param_id": target_param,
                "service_tarification_id": target_service,
            })

    if not candidates:
        raise ValueError(
            f"no curated {machine} mapping for param_id={param_id}, "
            f"service_tarification_id={service_id}"
        )

    target_pairs = {
        (candidate["param_id"], candidate["service_tarification_id"])
        for candidate in candidates
    }
    if len(target_pairs) == 1:
        preferred = PREFERRED_ORDER_METHODS.get(machine, {}).get(next(iter(target_pairs)))
        if preferred:
            candidates = [
                candidate for candidate in candidates
                if candidate["machine_test"] == preferred
            ]

    # Multiple labels that generate the same wire code are harmless aliases.
    by_wire_code = {}
    for candidate in candidates:
        by_wire_code.setdefault(candidate["machine_code"], candidate)
    if len(by_wire_code) != 1:
        choices = ", ".join(sorted(by_wire_code))
        raise ValueError(
            f"ambiguous {machine} mapping for param_id={param_id}, "
            f"service_tarification_id={service_id}; matching machine codes: {choices}. "
            "Send both identifiers or correct the curated mapping."
        )
    return next(iter(by_wire_code.values()))


class SelectraTestsRejected(ValueError):
    def __init__(self, message, rejected_tests):
        super().__init__(message)
        self.rejected_tests = rejected_tests


def _rejected_selectra_test(index, value, error):
    requested = value if isinstance(value, (dict, str, int, float, bool)) or value is None else str(value)
    return {
        "index": index,
        "requested": requested,
        "reason": str(error),
    }


def _resolve_selectra_tests(values):
    if not isinstance(values, list) or not values:
        raise ValueError("Selectra tests must be a non-empty JSON array")
    resolved = []
    rejected = []
    machine_codes = set()
    for index, value in enumerate(values):
        try:
            if isinstance(value, dict):
                item = _resolve_machine_test("selectra", value)
            else:
                # Backward compatibility for the local bench and existing clients.
                method = _validate_text("Selectra test", value, maximum=60)
                item = {
                    "machine_test": method,
                    "machine_code": test_abbreviation(method),
                    "param_id": None,
                    "service_tarification_id": None,
                }
            if item["machine_code"] in machine_codes:
                continue
            if len(resolved) >= 40:
                raise ValueError("no more than 40 Selectra tests can be staged")
            resolved.append(item)
            machine_codes.add(item["machine_code"])
        except ValueError as exc:
            rejected.append(_rejected_selectra_test(index, value, exc))
    if not resolved:
        detail = rejected[0]["reason"] if rejected else "no test details were supplied"
        raise SelectraTestsRejected(
            "none of the requested Selectra tests could be mapped; "
            f"order was not staged: {detail}",
            rejected,
        )
    return resolved, rejected


def _validated_selectra_api_order(body):
    resolved_tests, rejected_tests = _resolve_selectra_tests(body.get("tests"))
    tests = [item["machine_test"] for item in resolved_tests]
    birth_date = str(body.get("birth_date") or "").strip()
    try:
        date.fromisoformat(birth_date)
    except ValueError:
        raise ValueError("Selectra birth date must use YYYY-MM-DD") from None
    sex = str(body.get("sex") or "").strip().upper()
    if sex not in {"M", "F", "U"}:
        raise ValueError("Selectra sex must be M, F, or U")
    order = {
        # Do not impose the manual's historical 12-character O-3 limit at
        # the bridge boundary. Store and match the exact value Selectra sends
        # in Q-2; never truncate an identifier or guess a prefix match.
        "sample_id": _validate_text("Selectra sample ID", body.get("sample_id"), maximum=None),
        "patient_id": _validate_text("Selectra patient ID", body.get("patient_id"), maximum=64),
        "family_name": _validate_text("Selectra family name", body.get("family_name"), maximum=80),
        "given_name": _validate_text("Selectra given name", body.get("given_name"), maximum=80),
        "birth_date": birth_date,
        "sex": sex,
        "specimen_type": _validate_text(
            "Selectra specimen type", body.get("specimen_type"), required=False, maximum=32,
        ),
        "outbound_specimen_type": _validate_text(
            "Selectra specimen type", body.get("specimen_type"), required=False, maximum=32,
        ),
        "ordering_physician": _validate_text(
            "Selectra ordering physician", body.get("ordering_physician"),
            required=False, maximum=20,
        ),
        "comment": _validate_text(
            "Selectra comment", body.get("comment"), required=False, maximum=100,
        ),
        "tests": tests,
        "external_order_id": _validate_text(
            "external order ID", body.get("external_order_id"), required=False, maximum=100,
        ) or None,
    }
    patient_name = " ".join((order["family_name"], order["given_name"]))
    if len(patient_name) > 20:
        raise ValueError("Selectra family name plus given name must be 20 characters or fewer")
    _require_ascii(order, "Selectra")
    return order, resolved_tests, rejected_tests


def _validated_cyanvision_order(body):
    for value in (
        body.get("sample_id"), body.get("given_name"), body.get("family_name"),
        body.get("test_code"),
    ):
        if "~" in str(value or ""):
            raise ValueError("CYANVision values cannot contain HL7 delimiters")
    sample_id = _validate_text("CYANVision sample ID", body.get("sample_id"), maximum=20)
    birth_date = str(body.get("birth_date") or "").strip()
    if not birth_date:
        raise ValueError("CYANVision birth date is required")
    try:
        date.fromisoformat(birth_date)
    except ValueError:
        raise ValueError("CYANVision birth date must use YYYY-MM-DD") from None
    sex = str(body.get("sex") or "").strip().upper()
    if sex not in {"M", "F"}:
        raise ValueError("CYANVision sex must be M or F")
    order = {
        "sample_id": sample_id,
        "given_name": _validate_text(
            "CYANVision given name", body.get("given_name"), maximum=80,
        ),
        "family_name": _validate_text(
            "CYANVision family name", body.get("family_name"), maximum=80,
        ),
        "birth_date": birth_date,
        "sex": sex,
        "test_code": _validate_text(
            "CYANVision result code", body.get("test_code"), maximum=60,
        ),
    }
    if any(
        any(ord(character) < 32 or ord(character) > 126 for character in str(value))
        for value in order.values()
    ):
        raise ValueError("CYANVision fields must use printable ASCII characters for this protocol")
    return order


def _validated_cyanvision_api_order(body):
    order = _validated_cyanvision_order(body)
    order["external_order_id"] = _validate_text(
        "external order ID", body.get("external_order_id"), required=False, maximum=100,
    ) or None
    return order


def _cyanvision_test_options():
    """CYANVision result codes with a field-observed outbound ProgramID."""
    choices = {
        code: {
            "code": code,
            "program_id": program_id,
            "name": code,
            "observed": True,
            "mapped": False,
            "last_seen": None,
        }
        for code, program_id in WORKLIST_PROGRAM_IDS.items()
    }
    for code, targets in bridge_mappings.MAPS.get("cyanvision", {}).items():
        if code not in choices:
            continue
        primary = targets[0]
        choices[code]["name"] = primary[4] or primary[3] or primary[2] or code
        choices[code]["mapped"] = True
    for row in pg.list_observed_test_codes("cyanvision"):
        code = str(row.get("code") or "").strip()
        if code not in choices:
            continue
        entry = choices[code]
        entry["observed"] = True
        entry["last_seen"] = row.get("last_seen")
        if entry["name"] == code and row.get("display_name"):
            entry["name"] = row["display_name"]
    return sorted(choices.values(), key=lambda item: item["code"].casefold())


def _cyanvision_cre_trial_order(candidate):
    return {
        "sample_id": candidate["sample_id"],
        "given_name": candidate.get("given_name", "TRIAL"),
        "family_name": candidate.get("family_name", candidate["label"]),
        "birth_date": candidate.get("birth_date", "1980-01-01"),
        "sex": candidate.get("sex", "M"),
        "test_code": candidate["test_code"],
        "external_order_id": f"CYAN-CRE-TRIAL-{candidate['sequence']:02d}",
    }


def _cyanvision_cre_trial_status(store, cyanvision_service):
    entries = []
    for candidate in CRE_DSP8_TRIAL_SEQUENCE:
        saved = store.get_cyanvision_order(candidate["sample_id"])
        order = _cyanvision_cre_trial_order(candidate)
        entries.append({
            **candidate,
            "patient_name": f"{order['given_name']} {order['family_name']}",
            "status": saved["status"] if saved else "not_staged",
            "ready": bool(saved and saved["ready"]),
            "query_count": saved["query_count"] if saved else 0,
        })
    current = next((entry for entry in entries if entry["ready"]), None)
    service_status = cyanvision_service.status()
    return {
        "entries": entries,
        "current": current,
        "ready_count": sum(1 for entry in entries if entry["ready"]),
        "pending_ack": service_status["pending_ack"],
    }


def create_app(store, service, cyanvision_service=None, order_api_token=None):
    app = Flask(__name__, static_folder=None)
    configured_order_api_token = (
        order_api_token if order_api_token is not None
        else os.environ.get("LABO_ORDER_API_TOKEN", "").strip()
    )

    @app.before_request
    def protect_order_api():
        if not request.path.startswith("/api/v1/orders/"):
            return None
        if not configured_order_api_token:
            return jsonify({
                "error": "order API is disabled; configure LABO_ORDER_API_TOKEN"
            }), 503
        supplied = request.headers.get("X-API-TOKEN", "")
        if not hmac.compare_digest(supplied, configured_order_api_token):
            return jsonify({"error": "invalid or missing X-API-TOKEN"}), 401
        return None

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
        cyanvision = cyanvision_service.status() if cyanvision_service else None
        active_orders = [
            order for order in store.list_orders()
            if order.get("status") != "cancelled"
        ]
        return jsonify({
            **service.status(),
            "orders": len(active_orders),
            "api_armed_orders": len([
                order for order in active_orders
                if order.get("source") == "api" and order.get("ready")
            ]),
            "api_auto_arm": store.selectra_auto_arm_enabled(),
            "selectra_outbound_fields": store.selectra_outbound_fields(),
            "cyanvision": cyanvision,
        })

    @app.get("/api/cyanvision/worklist")
    def cyanvision_worklist_status():
        if not cyanvision_service:
            # The standalone Selectra bench uses the same static page without
            # installing a CYANVision listener. Let that UI hide this panel
            # instead of aborting all of its polling during initialization.
            return jsonify({"available": False})
        status_value = cyanvision_service.status()
        preview = cyanvision_service.preview(status_value["order"]) if status_value["order"] else []
        return jsonify({"available": True, **status_value, "response_preview": preview})

    @app.get("/api/cyanvision/tests")
    def cyanvision_tests():
        if not cyanvision_service:
            return jsonify({"available": False, "tests": []})
        return jsonify({"available": True, "tests": _cyanvision_test_options()})

    @app.post("/api/cyanvision/worklist")
    def stage_cyanvision_worklist():
        if not cyanvision_service:
            return jsonify({"error": "CYANVision worklist service is unavailable"}), 503
        body = request.get_json(silent=True) or {}
        if body.get("confirmation") != "ARM CYANVISION WORKLIST":
            return jsonify({"error": "explicit ARM CYANVISION WORKLIST confirmation is required"}), 400
        try:
            order = _validated_cyanvision_order(body)
            allowed_codes = {item["code"] for item in _cyanvision_test_options()}
            if order["test_code"] not in allowed_codes:
                raise ValueError(
                    "Select a CYANVision test with a field-observed outbound Program ID"
                )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        status_value = cyanvision_service.stage_and_arm(order)
        return jsonify({
            "ok": True,
            **status_value,
            "response_preview": cyanvision_service.preview(order),
        }), 201

    @app.delete("/api/cyanvision/worklist")
    def disarm_cyanvision_worklist():
        if not cyanvision_service:
            return jsonify({"error": "CYANVision worklist service is unavailable"}), 503
        return jsonify({"ok": True, **cyanvision_service.disarm()})

    @app.get("/api/cyanvision/cre-trials")
    def cyanvision_cre_trials():
        if not cyanvision_service:
            return jsonify({"available": False})
        return jsonify({
            "available": True,
            **_cyanvision_cre_trial_status(store, cyanvision_service),
        })

    @app.post("/api/cyanvision/cre-trials")
    def stage_cyanvision_cre_trials():
        if not cyanvision_service:
            return jsonify({"error": "CYANVision worklist service is unavailable"}), 503
        body = request.get_json(silent=True) or {}
        if body.get("confirmation") != "STAGE CYANVISION CRE TRIALS":
            return jsonify({
                "error": "explicit STAGE CYANVISION CRE TRIALS confirmation is required"
            }), 400
        if cyanvision_service.status()["pending_ack"]:
            return jsonify({
                "error": "wait for the current CYANVision connection to close before staging trials"
            }), 409
        trial_ids = {candidate["sample_id"] for candidate in CRE_DSP8_TRIAL_SEQUENCE}
        other_ready = [
            order for order in store.list_ready_cyanvision_orders()
            if order["sample_id"] not in trial_ids
        ]
        if other_ready:
            return jsonify({
                "error": "disarm or remove existing CYANVision worklist orders before staging the CRE trial"
            }), 409
        for candidate in CRE_DSP8_TRIAL_SEQUENCE:
            store.upsert_cyanvision_order(
                _cyanvision_cre_trial_order(candidate), source="trial", ready=True,
            )
        store.add_event(
            "local", "cyanvision_cre_trials_staged", None,
            "Staged the exact CY014 GLUC control plus 7 uniquely named Creatinine DSP.8 candidates",
        )
        return jsonify({
            "ok": True,
            **_cyanvision_cre_trial_status(store, cyanvision_service),
        }), 201

    @app.post("/api/cyanvision/cre-trials/advance")
    def advance_cyanvision_cre_trials():
        if not cyanvision_service:
            return jsonify({"error": "CYANVision worklist service is unavailable"}), 503
        body = request.get_json(silent=True) or {}
        if body.get("confirmation") != "ADVANCE CYANVISION CRE TRIAL":
            return jsonify({
                "error": "explicit ADVANCE CYANVISION CRE TRIAL confirmation is required"
            }), 400
        if cyanvision_service.status()["pending_ack"]:
            return jsonify({
                "error": "wait for CYANVision to close the current connection before advancing"
            }), 409
        status_value = _cyanvision_cre_trial_status(store, cyanvision_service)
        current = status_value["current"]
        if not current:
            return jsonify({"error": "no ready CYANVision CRE trial candidate remains"}), 409
        store.cancel_cyanvision_order(current["sample_id"])
        next_status = _cyanvision_cre_trial_status(store, cyanvision_service)
        next_candidate = next_status["current"]
        store.add_event(
            "local", "cyanvision_cre_trial_advanced", current["sample_id"],
            (
                f"Marked {current['patient_name']} / DSP.8 {current['dsp8'] or '(blank)'} checked; "
                + (f"{next_candidate['patient_name']} is next" if next_candidate else "trial sequence complete")
            ),
        )
        return jsonify({"ok": True, **next_status})

    @app.delete("/api/cyanvision/cre-trials")
    def clear_cyanvision_cre_trials():
        if not cyanvision_service:
            return jsonify({"error": "CYANVision worklist service is unavailable"}), 503
        if cyanvision_service.status()["pending_ack"]:
            return jsonify({
                "error": "wait for CYANVision to close the current connection before clearing trials"
            }), 409
        removed = 0
        for candidate in CRE_DSP8_TRIAL_SEQUENCE:
            saved = store.get_cyanvision_order(candidate["sample_id"])
            if saved and saved["ready"] and store.cancel_cyanvision_order(candidate["sample_id"]):
                removed += 1
        store.add_event(
            "local", "cyanvision_cre_trials_cleared", None,
            f"Cleared {removed} remaining Creatinine trial candidate(s)",
        )
        return jsonify({
            "ok": True,
            **_cyanvision_cre_trial_status(store, cyanvision_service),
        })

    @app.post("/api/v1/orders/selectra")
    def api_stage_selectra_order():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400
        try:
            order, resolved_tests, rejected_tests = _validated_selectra_api_order(body)
        except SelectraTestsRejected as exc:
            return jsonify({
                "error": str(exc),
                "rejected_tests": exc.rejected_tests,
            }), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        order["validation_warnings"] = rejected_tests
        saved = store.upsert_order(
            order, source="api", ready=store.selectra_auto_arm_enabled(),
        )
        response = {
            "ok": True,
            "analyzer": "selectra",
            "sample_id": saved["sample_id"],
            "state": "armed" if saved["ready"] else "staged",
        }
        if rejected_tests:
            response.update({
                "warning": (
                    f"Order kept with {len(resolved_tests)} valid test(s); "
                    f"{len(rejected_tests)} requested test(s) rejected"
                ),
                "accepted_test_count": len(resolved_tests),
                "rejected_tests": rejected_tests,
            })
        return jsonify(response), 201

    @app.post("/api/selectra/auto-arm")
    def set_selectra_auto_arm():
        body = request.get_json(silent=True) or {}
        enabled = body.get("enabled") is True
        if enabled and body.get("confirmation") != "ENABLE SELECTRA AUTO ARM":
            return jsonify({
                "error": "explicit ENABLE SELECTRA AUTO ARM confirmation is required"
            }), 400
        changed = store.set_selectra_auto_arm(enabled)
        store.add_event(
            "local", "selectra_auto_arm_enabled" if enabled else "selectra_auto_arm_disabled",
            None,
            (
                f"Selectra API auto-arm enabled; {changed} waiting order(s) armed"
                if enabled else
                f"Selectra API auto-arm disabled; {changed} waiting order(s) disarmed"
            ),
        )
        return jsonify({
            "ok": True,
            "enabled": enabled,
            "updated_orders": changed,
        })

    @app.post("/api/selectra/outbound-fields")
    def set_selectra_outbound_fields():
        body = request.get_json(silent=True) or {}
        if body.get("reset") is True:
            fields = store.reset_selectra_outbound_fields()
            store.add_event(
                "local", "selectra_outbound_fields_reset", None,
                "Selectra API output returned to minimal name, sample ID, and tests",
            )
            return jsonify({"ok": True, "fields": fields})
        field = str(body.get("field") or "").strip()
        enabled = body.get("enabled") is True
        if enabled and body.get("confirmation") != "ENABLE SELECTRA OUTBOUND FIELD":
            return jsonify({
                "error": "explicit ENABLE SELECTRA OUTBOUND FIELD confirmation is required"
            }), 400
        try:
            fields = store.set_selectra_outbound_field(field, enabled)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        store.add_event(
            "local", "selectra_outbound_field_changed", None,
            f"Selectra API outbound field {field} {'enabled' if enabled else 'disabled'}",
        )
        return jsonify({"ok": True, "fields": fields})

    @app.get("/api/v1/orders/selectra/<sample_id>")
    def api_get_selectra_order(sample_id):
        order = store.get_order(sample_id)
        if not order or order.get("source") != "api":
            return jsonify({"error": "Selectra API order not found"}), 404
        response = {
            "analyzer": "selectra",
            "sample_id": order["sample_id"],
            "state": order["status"],
            "armed": order["ready"],
            "query_count": order["query_count"],
            "updated_at": order["updated_at"],
            "last_error": order["last_error"],
        }
        if order.get("validation_warnings"):
            response["rejected_tests"] = order["validation_warnings"]
        return jsonify(response)

    @app.delete("/api/v1/orders/selectra/<sample_id>")
    def api_cancel_selectra_order(sample_id):
        order = store.get_order(sample_id)
        if not order or order.get("source") != "api":
            return jsonify({"error": "Selectra API order not found"}), 404
        store.cancel_order(sample_id)
        store.add_event("api", "order_cancelled", sample_id, "Selectra API order cancelled")
        return jsonify({"ok": True, "analyzer": "selectra", "sample_id": sample_id, "state": "cancelled"})

    @app.post("/api/v1/orders/cyanvision")
    def api_stage_cyanvision_order():
        if not cyanvision_service:
            return jsonify({"error": "CYANVision worklist service is unavailable"}), 503
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400
        try:
            resolved_test = None
            if "test" in body:
                resolved_test = _resolve_machine_test("cyanvision", body["test"])
                body = {**body, "test_code": resolved_test["machine_code"]}
            order = _validated_cyanvision_api_order(body)
            allowed_codes = {item["code"] for item in _cyanvision_test_options()}
            if order["test_code"] not in allowed_codes:
                raise ValueError(
                    "Select a CYANVision test with a field-observed outbound Program ID"
                )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        saved = store.upsert_cyanvision_order(order, source="api", ready=True)
        return jsonify({
            "ok": True,
            "analyzer": "cyanvision",
            "state": "ready",
            "sample_id": saved["sample_id"],
        }), 201

    @app.get("/api/v1/orders/cyanvision/<sample_id>")
    def api_get_cyanvision_order(sample_id):
        order = store.get_cyanvision_order(sample_id)
        if not order or order.get("source") != "api":
            return jsonify({"error": "CYANVision API order not found"}), 404
        return jsonify({
            "analyzer": "cyanvision",
            "sample_id": order["sample_id"],
            "state": order["status"],
            "ready": order["ready"],
            "query_count": order["query_count"],
            "updated_at": order["updated_at"],
            "last_error": order["last_error"],
        })

    @app.delete("/api/v1/orders/cyanvision/<sample_id>")
    def api_cancel_cyanvision_order(sample_id):
        order = store.get_cyanvision_order(sample_id)
        if not order or order.get("source") != "api":
            return jsonify({"error": "CYANVision API order not found"}), 404
        store.cancel_cyanvision_order(sample_id)
        store.add_event("api", "cyanvision_order_cancelled", sample_id, "CYANVision API order cancelled")
        return jsonify({"ok": True, "analyzer": "cyanvision", "sample_id": sample_id, "state": "cancelled"})

    @app.get("/api/assays")
    def assays():
        return jsonify({"assays": ASSAY_SUGGESTIONS, "validated_for_order_download": False})

    @app.get("/api/orders")
    def orders():
        return jsonify({
            "orders": [
                order for order in store.list_orders()
                if order.get("status") != "cancelled"
            ],
        })

    @app.post("/api/orders/<sample_id>/arm")
    def arm_api_order(sample_id):
        body = request.get_json(silent=True) or {}
        if body.get("confirmation") != "ARM SELECTRA ORDER":
            return jsonify({"error": "explicit ARM SELECTRA ORDER confirmation is required"}), 400
        order = store.get_order(sample_id)
        if not order or order.get("source") != "api":
            return jsonify({"error": "staged API order not found"}), 404
        try:
            saved = store.set_order_ready(sample_id, True)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        store.add_event(
            "local", "api_order_armed", sample_id,
            "API order manually armed; waiting for an exact Selectra query",
        )
        return jsonify({"ok": True, "sample_id": sample_id, "state": "armed", "armed": saved["ready"]})

    @app.delete("/api/orders/<sample_id>/arm")
    def disarm_api_order(sample_id):
        order = store.get_order(sample_id)
        if not order or order.get("source") != "api":
            return jsonify({"error": "staged API order not found"}), 404
        saved = store.set_order_ready(sample_id, False)
        store.add_event(
            "local", "api_order_disarmed", sample_id,
            "API order manually disarmed; Selectra queries will not receive it",
        )
        return jsonify({"ok": True, "sample_id": sample_id, "state": "staged", "armed": saved["ready"]})

    @app.delete("/api/orders/<sample_id>")
    def remove_staged_order(sample_id):
        order = store.get_order(sample_id)
        if not order or order.get("status") == "cancelled":
            return jsonify({"error": "staged order not found"}), 404
        store.cancel_order(sample_id)
        store.add_event(
            "local", "order_removed", sample_id,
            "Order removed from the active staging queue",
        )
        return jsonify({"ok": True, "sample_id": sample_id, "state": "cancelled"})

    @app.post("/api/orders/bulk-remove")
    def remove_staged_orders():
        body = request.get_json(silent=True) or {}
        if body.get("confirmation") != "REMOVE SELECTRA ORDERS":
            return jsonify({
                "error": "explicit REMOVE SELECTRA ORDERS confirmation is required"
            }), 400
        values = body.get("sample_ids")
        if not isinstance(values, list) or not values:
            return jsonify({"error": "sample_ids must be a non-empty JSON array"}), 400
        if len(values) > 200:
            return jsonify({"error": "no more than 200 orders can be removed at once"}), 400
        sample_ids = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                return jsonify({"error": "every sample ID must be a non-empty string"}), 400
            sample_id = value.strip()
            if sample_id not in sample_ids:
                sample_ids.append(sample_id)
        removed = store.cancel_orders(sample_ids)
        if removed:
            store.add_event(
                "local", "orders_bulk_removed", None,
                f"Removed {len(removed)} selected order(s) from the active staging queue",
                "\n".join(removed),
            )
        return jsonify({
            "ok": True,
            "removed_count": len(removed),
            "removed_sample_ids": removed,
        })

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

    @app.post("/api/continuous-probe")
    def continuous_probe():
        body = request.get_json(silent=True) or {}
        armed = body.get("armed") is True
        if armed and body.get("confirmation") != "ARM CONTINUOUS PROBE":
            return jsonify({"error": "explicit ARM CONTINUOUS PROBE confirmation is required"}), 400
        service.set_probe_armed(armed)
        return jsonify({**service.status(), "ok": True})

    @app.get("/api/events")
    def events():
        try:
            after = max(0, int(request.args.get("after", "0")))
        except ValueError:
            return jsonify({"error": "after must be an integer"}), 400
        return jsonify({"events": store.list_events(after=after)})

    return app
