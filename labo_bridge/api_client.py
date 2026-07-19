"""
Client for the real clinic "Labo Machine Result" API (see
API_LABO_MACHINE_RESULT.md at the repo root - written by the coworker who
owns the clinic app).

This is the PERMANENT intended write path: the coworker's API derives the
real appointment/exam from sample_id itself (first 8 digits -> appointment
number "LAB-XXXXXXXX", last 2 digits validated against the exam's configured
tube) and writes directly into labo.labo_result. It replaces our temporary
labo_bridge.labo_bridge_results staging table (see pg.py) once confirmed
live - config.USE_MACHINE_RESULT_API switches between them.

Not live yet as of 2026-07-16 (coworker still building/deploying it). This
client is ready to go the moment it is - just flip the config flag.
"""

import json
import urllib.request
import urllib.error

from . import config

ENDPOINT = "http://localhost:8080/labo/api/machine/result"
API_TOKEN = "labo@@2025"


def send_results(items: list) -> dict:
    """
    POST a batch of machine results to the clinic API.
    `items` is a list of dicts, each with sample_id, result_value, unit, and
    EITHER param_id OR service_tarification_id, plus optional machine/machine_id.
    Returns {'ok': bool, 'status': int|None, 'body': dict|str, 'error': str|None}.
    Never raises - network/API failures are reported, not thrown, so a
    machine result batch never crashes the listener.
    """
    if not items:
        return {"ok": False, "status": None, "body": None, "error": "no items to send"}

    payload = json.dumps(items).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-API-TOKEN": API_TOKEN},
    )

    try:
        with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "status": resp.status, "body": body, "error": None}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = e.reason
        return {"ok": False, "status": e.code, "body": body, "error": str(e)}
    except Exception as e:
        # includes urllib.error.URLError (connection refused, DNS, timeout, etc.)
        return {"ok": False, "status": None, "body": None, "error": str(e)}


def build_item(sample_id, result_value, unit=None, param_id=None,
               service_tarification_id=None, machine=None, machine_id=None):
    """
    Build one request item per the API contract: sample_id + result_value
    required, exactly one of param_id/service_tarification_id required.
    """
    if param_id is None and service_tarification_id is None:
        raise ValueError("build_item requires param_id or service_tarification_id")

    item = {"sample_id": sample_id, "result_value": result_value}
    if unit:
        item["unit"] = unit
    if param_id is not None:
        item["param_id"] = param_id
    if service_tarification_id is not None:
        item["service_tarification_id"] = service_tarification_id
    if machine_id is not None:
        item["machine_id"] = machine_id
    elif machine:
        item["machine"] = machine
    return item


def write_matched_result(machine, sample_id, specimen, test_code, match, rec):
    """
    Same call signature as pg.write_matched_result, so server.py can swap
    between the two via config.USE_MACHINE_RESULT_API without branching logic
    elsewhere. Sends ONE result per call (the API also accepts batches via
    send_results() directly, for future batching if useful).
    Returns True if the API accepted the result, False otherwise.
    """
    item = build_item(
        sample_id=sample_id.strip(),
        result_value=rec.get("value", ""),
        unit=rec.get("unit") or None,
        param_id=match.get("param_id"),
        service_tarification_id=match.get("service_tarification_id")
                                 if not match.get("param_id") else None,
        machine=machine,
    )
    result = send_results([item])

    if not result["ok"]:
        print(f"[api] WARNING: failed to send {machine}/{sample_id}/{test_code} "
              f"to clinic API: {result['error']} (status={result['status']})")
        return False

    body = result["body"]
    if isinstance(body, dict) and body.get("failed", 0):
        first = (body.get("results") or [{}])[0]
        print(f"[api] REJECTED {machine}/{sample_id}/{test_code}: "
              f"{first.get('message', body)}")
        return False

    return True
