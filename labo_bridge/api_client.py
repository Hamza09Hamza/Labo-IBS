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


def send_batch(machine: str, queued: list) -> None:
    """
    Send every result queued during one session (one ASTM batch / one HL7
    message) as a SINGLE JSON array in ONE POST call - the API requires a
    JSON array even for a single result, and multiple results captured
    together must be sent together, not as separate POSTs per result (see
    API_LABO_MACHINE_RESULT.md's "Important" section).

    `queued` is a list of dicts, each {"item": <dict from build_item()>,
    "sample_id": str, "test_code": str} - see server.py's _ingest_result/
    _flush_api_batch, which build this list as results stream in and flush
    it at the batch boundary.

    Always prints the exact outgoing JSON array before attempting the send -
    useful for validating the request shape while the coworker's API isn't
    live yet (every send currently fails with a connection error, but the
    payload itself is still visible to check against API_LABO_MACHINE_RESULT.md).
    Reports a per-item outcome when the API returns a matching "results"
    breakdown, otherwise one combined outcome for the whole batch (e.g. on a
    connection error, where no per-item breakdown exists).
    """
    if not queued:
        return

    items = [entry["item"] for entry in queued]
    print(f"[api] >> POST {ENDPOINT}  ({len(items)} result(s) in one array)\n"
          f"[api]    body: {json.dumps(items, ensure_ascii=False, indent=2)}")
    result = send_results(items)

    if not result["ok"]:
        labels = ", ".join(f"{machine}/{e['sample_id']}/{e['test_code']}" for e in queued)
        print(f"[api] << send failed for batch [{labels}]: "
              f"{result['error']} (status={result['status']})")
        return

    body = result["body"]
    per_item = body.get("results") if isinstance(body, dict) else None
    if per_item and len(per_item) == len(queued):
        for entry, r in zip(queued, per_item):
            label = f"{machine}/{entry['sample_id']}/{entry['test_code']}"
            if r.get("success"):
                print(f"[api] << accepted {label} (labo_result_id={r.get('laboResultId')})")
            else:
                print(f"[api] << REJECTED {label}: {r.get('message', r)}")
    else:
        print(f"[api] << batch response: {body}")
