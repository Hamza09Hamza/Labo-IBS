#!/usr/bin/env python3
"""Run the isolated Selectra Host Query test bench."""

import argparse
import os

from selectra_host_query.app import create_app
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore


ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(ROOT, "selectra_host_query", "data", "host_query.db")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=5052)
    parser.add_argument("--instrument-host", default="0.0.0.0")
    parser.add_argument("--instrument-port", type=int, default=6103)
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument(
        "--arm-live-responses", action="store_true",
        help="allow H/P/O/L order records to be sent to a connected Selectra after an exact-ID query",
    )
    args = parser.parse_args()
    for name, port in (("web port", args.web_port), ("instrument port", args.instrument_port)):
        if not 1 <= port <= 65535:
            parser.error(f"{name} must be between 1 and 65535")
    if args.web_port == args.instrument_port:
        parser.error("web port and instrument port must differ")

    store = BenchStore(args.data)
    service = SelectraHostQueryServer(
        store, host=args.instrument_host, port=args.instrument_port,
        armed=args.arm_live_responses,
    )
    app = create_app(store, service)
    service.start()
    print("\nSelectra Host Query Bench started")
    print(f"Web console: http://127.0.0.1:{args.web_port}/")
    print(f"Selectra test endpoint: <this-computer-IP>:{args.instrument_port}")
    print(f"Live order responses: {'ARMED' if args.arm_live_responses else 'DISARMED (observation + simulator only)'}")
    print("This process does not read or write the clinic database.\n")
    try:
        app.run(host=args.web_host, port=args.web_port, debug=False, use_reloader=False, threaded=True)
    finally:
        service.stop()


if __name__ == "__main__":
    main()

