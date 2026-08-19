#!/usr/bin/env python3
"""
Start EVERYTHING in one process: all analyzer listeners (each on its own
port), the admin web console at http://127.0.0.1:5050, the analyzer order
console at http://127.0.0.1:5052, and the OperationBloc Bridge (operating
block patient monitors + anesthesia machines) at http://127.0.0.1:5051. This
is the one command to run.

Ports (see labo_bridge/server.py MACHINES for the source of truth):
    xn330      -> 6001
    ismart     -> 6002
    selectra   -> 6003  (chemistry analyzer; runs the ELITech/LIS2-A software)
    cyanvision -> 6004
    xs500i     -> 6005  (via IPU on the machine's own PC)
    minividas  -> 6006
    admin UI   -> http://127.0.0.1:5050
    Selectra + CYANVision order UI -> http://127.0.0.1:5052
    OperationBloc Bridge (clinical_portal) -> http://127.0.0.1:5051

OperationBloc Bridge only starts real device collectors for machines enabled
in clinical_portal/config.json - it never runs demo/fake data here. Its 10s
readings history (latest value plus that window's mean/min/max/count) is
saved continuously to clinical_portal/data/history.db. If its configuration
cannot be loaded, everything else in this process still starts normally.

Every line printed is prefixed with the machine name, and every result
actually written to the local database (labo_bridge.db) is printed alongside
whether it was matched to a clinic labo_param or left pending for review.

Ctrl+C stops the listeners and every web interface together.
"""
import threading
import os

from labo_bridge import server
from labo_bridge.admin import app as admin_app
from selectra_host_query.app import create_app as create_selectra_query_app
from selectra_host_query.order_api_auth import load_or_create_order_api_token
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore
from cyanvision_worklist.service import CyanVisionWorklistService
from clinical_portal.app import app as clinical_app
from clinical_portal.history import recorder as clinical_history_recorder
from clinical_portal.store import store as clinical_store
import run_clinical


ROOT = os.path.dirname(os.path.abspath(__file__))
SELECTRA_QUERY_DATA = os.path.join(ROOT, "selectra_host_query", "data", "host_query.db")
ORDER_API_TOKEN_PATH = os.path.join(
    ROOT, "selectra_host_query", "data", "order_api_token.txt",
)
ORDER_API_TOKEN = load_or_create_order_api_token(ORDER_API_TOKEN_PATH)

# Manual diagnostic reply modes start disarmed on every process restart.
# Authenticated API orders have a persisted per-order ready flag. Selectra
# orders start inactive and must be armed from the local 5052 console.
selectra_query_store = BenchStore(SELECTRA_QUERY_DATA)
selectra_query_service = SelectraHostQueryServer(
    selectra_query_store, host="0.0.0.0", port=6003, armed=False, embedded=True,
)
cyanvision_worklist_service = CyanVisionWorklistService(
    selectra_query_store, port=6004,
)
selectra_query_app = create_selectra_query_app(
    selectra_query_store,
    selectra_query_service,
    cyanvision_worklist_service,
    order_api_token=ORDER_API_TOKEN,
)
server.configure_selectra_host_query(selectra_query_service)
server.configure_cyanvision_worklist(cyanvision_worklist_service)


def _run_admin():
    # use_reloader=False: Flask's debug reloader forks a second process,
    # which would duplicate every listener thread too - not compatible with
    # running everything in one process.
    # threaded=True: without it, Flask serves ONE request at a time - with
    # analyzers actively streaming (Postgres writes on every result) plus
    # the admin UI's own 2s polling loop, single-threaded mode lets requests
    # queue up behind each other for a long time under real load, making a
    # save look like it's "hanging" when it's really just waiting its turn.
    admin_app.app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False, threaded=True)


def _run_selectra_query_ui():
    selectra_query_app.run(
        host="0.0.0.0", port=5052, debug=False, use_reloader=False, threaded=True,
    )


def _start_clinical_portal(stop_event):
    """Start the OperationBloc Bridge (doctor portal + device collectors + history)
    alongside the lab bridge, sharing this same process. Real device collectors
    only start for machines enabled in clinical_portal/config.json - never demo
    data. Returns the list of started CollectorSupervisor instances, or [] if
    the clinical configuration could not be loaded.
    """
    try:
        clinical_config = run_clinical._load_config(run_clinical.DEFAULT_CONFIG)
    except (OSError, ValueError) as exc:
        print(f"[clinical] OperationBloc Bridge disabled: {exc}")
        return []

    clinical_store.set_demo_mode(False)
    web = clinical_config.get("web") or {}
    host = str(web.get("host", "0.0.0.0"))
    port = int(web.get("port", 5051))

    web_thread = threading.Thread(
        target=lambda: clinical_app.run(
            host=host, port=port, debug=False, use_reloader=False, threaded=True,
        ),
        name="clinical-web", daemon=True,
    )
    web_thread.start()

    history_thread = threading.Thread(
        target=clinical_history_recorder.run_loop, args=(stop_event,),
        name="clinical-history", daemon=True,
    )
    history_thread.start()

    supervisors = [
        run_clinical.CollectorSupervisor(label, command, stop_event)
        for label, command in run_clinical._collector_commands(clinical_config)
    ]
    for supervisor in supervisors:
        supervisor.start()

    print(f"[clinical] OperationBloc Bridge running at http://127.0.0.1:{port}")
    print(f"[clinical] {len(supervisors)} device collector(s) enabled; "
          f"10s history snapshots are being saved to clinical_portal/data/history.db\n")
    return supervisors


if __name__ == "__main__":
    admin_thread = threading.Thread(target=_run_admin, name="admin-ui", daemon=True)
    admin_thread.start()
    print("[admin] Labo Bridge Admin running at http://127.0.0.1:5050\n")

    selectra_query_thread = threading.Thread(
        target=_run_selectra_query_ui, name="selectra-query-ui", daemon=True,
    )
    selectra_query_thread.start()
    print("[orders] Selectra + CYANVision order console running at http://127.0.0.1:5052")
    print("[selectra] Exact-ID replies and the continuous wildcard probe start DISARMED; instrument traffic remains on port 6003.\n")
    print("[cyanvision] One-load worklist starts DISARMED; queries and results remain on port 6004.\n")
    print("[xn330] Result receiving remains active on port 6001; Host Query order download is disabled.\n")
    print("[orders-api] ENABLED for Selectra and CYANVision orders.")
    print(f"[orders-api] The private token is stored locally at {ORDER_API_TOKEN_PATH}.\n")

    clinical_stop_event = threading.Event()
    clinical_supervisors = _start_clinical_portal(clinical_stop_event)

    try:
        server.run_all()
    finally:
        clinical_stop_event.set()
        for supervisor in clinical_supervisors:
            supervisor.stop()
        for supervisor in clinical_supervisors:
            supervisor.thread.join(timeout=4)
