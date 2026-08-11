#!/usr/bin/env python3
"""Start the entire standalone operating-chamber monitoring application.

One command starts:
  * the doctor portal and its normalized ingestion API;
  * one supervised uMEC12 PDS connector per enabled chamber;
  * one supervised WATO HL7/TCP listener per enabled chamber.

Configuration lives in clinical_portal/config.json. This process is separate
from run_all.py and does not start, import, or modify the laboratory analyzer
admin application.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

from clinical_portal.app import app
from clinical_portal.demo import run_demo
from clinical_portal.store import store


ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(ROOT, "clinical_portal", "config.json")
DEFAULT_PARAMETERS = "101,151,160,161,162,170,171,172,173,200,201,202"
UMEC12_PDS_PORT = 4601
CLINICAL_CAPTURES = os.path.join(ROOT, "clinical_portal", "captures")


def _load_config(path):
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    web = config.get("web") or {}
    port = int(web.get("port", 5051))
    if not 1 <= port <= 65535:
        raise ValueError("web.port must be between 1 and 65535")

    chambers = config.get("chambers") or []
    ids = [int(item.get("id", 0)) for item in chambers]
    if sorted(ids) != [1, 2, 3]:
        raise ValueError("config must contain exactly chambers 1, 2, and 3")

    local_ports = {port: "OperationBloc web server"}
    for chamber in chambers:
        cid = int(chamber["id"])
        umec = chamber.get("umec12") or {}
        if umec.get("enabled") and not str(umec.get("ip", "")).strip():
            raise ValueError(f"Chamber {cid}: enabled uMEC12 requires an IP address")
        if umec.get("enabled") and umec.get("local_port") not in (None, ""):
            local_port = int(umec["local_port"])
            if not 1 <= local_port <= 65535:
                raise ValueError(f"Chamber {cid}: invalid uMEC12 local_port")
            if local_port in local_ports:
                raise ValueError(f"Chamber {cid}: local port {local_port} is already used by {local_ports[local_port]}")
            local_ports[local_port] = f"Chamber {cid} uMEC12 source socket"
        wato = chamber.get("wato") or {}
        if wato.get("enabled"):
            listener_port = int(wato.get("listen_port", 0))
            if not 1 <= listener_port <= 65535:
                raise ValueError(f"Chamber {cid}: invalid WATO listen_port")
            if listener_port in local_ports:
                raise ValueError(f"Chamber {cid}: local port {listener_port} is already used by {local_ports[listener_port]}")
            local_ports[listener_port] = f"Chamber {cid} WATO listener"
    return config


class CollectorSupervisor:
    def __init__(self, label, command, stop_event):
        self.label = label
        self.command = command
        self.stop_event = stop_event
        self.process = None
        self.thread = threading.Thread(target=self._run, name=f"collector-{label}", daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            print(f"[{self.label}] starting")
            try:
                self.process = subprocess.Popen(
                    self.command,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in self.process.stdout:
                    print(f"[{self.label}] {line}", end="")
                    if self.stop_event.is_set():
                        break
                return_code = self.process.wait()
            except OSError as exc:
                return_code = -1
                print(f"[{self.label}] could not start: {exc}")
            finally:
                self.process = None

            if self.stop_event.is_set():
                break
            print(f"[{self.label}] stopped with code {return_code}; restarting in 5 seconds")
            self.stop_event.wait(5)

    def stop(self):
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _collector_commands(config):
    web = config.get("web") or {}
    collector_url = web.get("collector_url") or f"http://127.0.0.1:{int(web.get('port', 5051))}"
    for chamber in config["chambers"]:
        cid = int(chamber["id"])
        umec = chamber.get("umec12") or {}
        if umec.get("enabled"):
            command = [
                sys.executable, "-u", os.path.join(ROOT, "capture_umec12.py"),
                "--monitor-ip", str(umec["ip"]),
                "--monitor-port", str(UMEC12_PDS_PORT),
                "--no-udp",
                "--parameters", str(umec.get("parameters", DEFAULT_PARAMETERS)),
                "--portal-url", collector_url,
                "--chamber", str(cid),
                "--captures-dir", CLINICAL_CAPTURES,
            ]
            if umec.get("local_port") not in (None, ""):
                command.extend(["--local-port", str(int(umec["local_port"]))])
            yield f"chamber-{cid}/umec12", command

        wato = chamber.get("wato") or {}
        if wato.get("enabled"):
            command = [
                sys.executable, "-u", os.path.join(ROOT, "capture_wato.py"),
                str(int(wato["listen_port"])),
                "--portal-url", collector_url,
                "--chamber", str(cid),
                "--captures-dir", CLINICAL_CAPTURES,
            ]
            yield f"chamber-{cid}/wato", command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"clinical configuration file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--demo", action="store_true",
                        help="stream clearly-labelled fake data; do not start or contact real devices")
    args = parser.parse_args()

    try:
        config = _load_config(os.path.abspath(args.config))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"clinical configuration error: {exc}")

    web = config.get("web") or {}
    host = str(web.get("host", "0.0.0.0"))
    port = int(web.get("port", 5051))
    stop_event = threading.Event()
    store.set_demo_mode(args.demo)
    supervisors = [] if args.demo else [
        CollectorSupervisor(label, command, stop_event)
        for label, command in _collector_commands(config)
    ]
    demo_thread = None
    if args.demo:
        demo_thread = threading.Thread(
            target=run_demo,
            args=(stop_event,),
            name="clinical-demo-stream",
            daemon=True,
        )

    web_thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False,
                               use_reloader=False, threaded=True),
        name="clinical-web",
        daemon=True,
    )
    web_thread.start()

    print("\nOperationBloc Bridge started")
    if args.demo:
        print("MODE: SIMULATED DATA — real device collectors are disabled")
    print(f"Doctor portal: http://127.0.0.1:{port}/")
    print(f"Configured collectors: {len(supervisors)}" + (" (demo generator active)" if args.demo else ""))
    print("Press Ctrl+C to stop everything.\n")

    for supervisor in supervisors:
        supervisor.start()
    if demo_thread is not None:
        demo_thread.start()

    try:
        while web_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping OperationBloc Bridge...")
    finally:
        stop_event.set()
        for supervisor in supervisors:
            supervisor.stop()
        for supervisor in supervisors:
            supervisor.thread.join(timeout=4)
        if demo_thread is not None:
            demo_thread.join(timeout=3)
        print("Stopped.")


if __name__ == "__main__":
    main()
