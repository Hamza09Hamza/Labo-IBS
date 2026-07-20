#!/usr/bin/env python3
"""
Start EVERYTHING in one process: all five analyzer listeners (each on its
own port) AND the admin web console (mapping editor, machine settings,
API settings) at http://127.0.0.1:5050. This is the one command to run -
no separate `python -m labo_bridge.admin` needed alongside it.

Ports (see labo_bridge/server.py MACHINES for the source of truth):
    xn330      -> 6001
    ismart     -> 6002
    selectra   -> 6003  (chemistry analyzer; runs the ELITech/LIS2-A software)
    cyanvision -> 6004
    xs500i     -> 6005  (via IPU on the machine's own PC)
    admin UI   -> http://127.0.0.1:5050

Every line printed is prefixed with the machine name, and every result
actually written to the local database (labo_bridge.db) is printed alongside
whether it was matched to a clinic labo_param or left pending for review.

Ctrl+C stops both the listeners and the admin UI together.
"""
import threading

from labo_bridge import server
from labo_bridge.admin import app as admin_app


def _run_admin():
    # use_reloader=False: Flask's debug reloader forks a second process,
    # which would duplicate every listener thread too - not compatible with
    # running everything in one process.
    admin_app.app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)


if __name__ == "__main__":
    admin_thread = threading.Thread(target=_run_admin, name="admin-ui", daemon=True)
    admin_thread.start()
    print("[admin] Labo Bridge Admin running at http://127.0.0.1:5050\n")

    server.run_all()
