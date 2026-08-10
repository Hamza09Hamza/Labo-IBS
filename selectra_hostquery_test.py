#!/usr/bin/env python3
"""
Standalone test for Selectra Host Query (bi-directional) mode - NOT wired
into the labo_bridge pipeline. Same throwaway-discovery spirit as
capture_listener.py: prove the wire protocol works with a hardcoded fake
reply BEFORE writing anything real into server.py/matcher.py.

Context: the Selectra currently only ever sends US results (R records) -
server.py's _handle_astm only ever reads and ACKs, never replies. The
coworker confirmed Host Query mode keeps the SAME connection direction
(the Selectra still connects out to us on the port it's configured with
today - same IP:port already used for receiving results, per user
confirmation 2026-08-10) - it just adds a Q record onto that stream after
the operator scans a tube, and expects a P + O reply from us before it
will run anything.

This script listens exactly like the real Selectra listener does, but
when it sees a Q record it also sends back ONE hardcoded fake P (patient)
+ O (order) frame, so you can watch on the Selectra's own touchscreen
whether the fake order actually appears in its worklist - that's the only
thing this proves. Real order data (from wherever the clinic's actual
schedule lives) is a separate, later problem.

BEFORE USING: on the Selectra TouchPro itself,
  1. Settings -> LIS/Host Interface -> switch Uni-directional -> Bi-directional
     (Host Query), Ethernet (TCP/IP).
  2. Note the exact test-code short names from the Selectra's own assay
     settings (NOT the generic GLU/CREA/UREA examples from the vendor doc) -
     edit FAKE_TEST_CODES below to match.
  3. Confirm which port it's configured to use (same one it already uses
     to send results today - check config.json's "selectra" port, or the
     TouchPro's own Network/LIS settings screen).

Usage:
    python selectra_hostquery_test.py            # port from labo_bridge config.json
    python selectra_hostquery_test.py 6003        # explicit port override
"""

import os
import socket
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labo_bridge.protocols import astm  # noqa: E402 - reused read-only, same framing as server.py

HOST = "0.0.0.0"

# Real test-code strings this Selectra has actually sent, taken from
# labo_bridge/data/config.json's "mappings"."selectra" keys (built from real
# captured R/result records, not guessed) - NOT confirmed to be the same
# format the O/order record expects (the coworker's vendor example used a
# different, shorter style like "GLU"/"CREA" - real LIS2-A systems sometimes
# use a distinct order-code vocabulary from the result-name field). Still
# the best real evidence available; verify against the Selectra's own assay
# settings screen on-site before relying on this.
FAKE_TEST_CODES = ["Glucose pap sl", "Creatinine"]
FAKE_SAMPLE_ID = "TEST998877"
FAKE_PATIENT_ID = "TESTPATIENT"
FAKE_PATIENT_NAME = "TEST^FAKE"


def _default_port() -> int:
    try:
        from labo_bridge import server
        return server.MACHINES["selectra"]["port"]
    except Exception as e:
        print(f"could not read selectra's configured port from labo_bridge ({e}); "
              f"pass the port explicitly, e.g. python {sys.argv[0]} 6003")
        raise SystemExit(1)


def _build_patient_frame() -> bytes:
    # P|1|||<patient_id>||<name>
    text = f"P|1|||{FAKE_PATIENT_ID}||{FAKE_PATIENT_NAME}"
    return astm.build_frame(2, text)


def _build_order_frame() -> bytes:
    # O|1|<sample_id>||^^^TEST1\^^^TEST2|R
    tests = "\\".join(f"^^^{code}" for code in FAKE_TEST_CODES)
    text = f"O|1|{FAKE_SAMPLE_ID}||{tests}|R"
    return astm.build_frame(3, text)


def _handle_connection(conn, addr):
    print(f"connected by {addr[0]}:{addr[1]}")
    buffer = b""
    conn.settimeout(90)
    while True:
        try:
            data = conn.recv(4096)
        except (ConnectionResetError, socket.timeout):
            break
        if not data:
            break
        buffer += data
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{stamp}] RECV {len(data)} bytes: {data!r}")

        while buffer:
            b0 = buffer[0]
            if b0 == astm.ENQ:
                conn.sendall(astm.B_ACK)
                print("  << ACK (ENQ)")
                buffer = buffer[1:]
            elif b0 == astm.EOT:
                buffer = buffer[1:]
                print("  EOT - batch complete")
            elif b0 == astm.STX:
                idx = buffer.find(bytes([astm.LF]))
                if idx == -1:
                    break
                frame = buffer[:idx + 1]
                buffer = buffer[idx + 1:]
                conn.sendall(astm.B_ACK)
                text = astm.strip_frame(frame)
                print(f"  << ACK (STX frame): {text!r}")
                for rec in astm.split_records(text):
                    rtype = rec[0] if rec else ""
                    print(f"    record: {rec!r}")
                    if rtype == "Q":
                        print("    *** Q (query) record seen - sending fake "
                              "P + O reply ***")
                        p_frame = _build_patient_frame()
                        o_frame = _build_order_frame()
                        conn.sendall(p_frame)
                        print(f"    >> sent P frame: {p_frame!r}")
                        conn.sendall(o_frame)
                        print(f"    >> sent O frame: {o_frame!r}")
                        print(f"    Now check the Selectra's own worklist screen "
                              f"for sample_id={FAKE_SAMPLE_ID!r} - that's the "
                              f"whole test.")
            else:
                buffer = buffer[1:]


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else _default_port()
    print(f"Selectra Host Query test - listening on {HOST}:{port}")
    print(f"Will reply to any Q record with a fake order: "
          f"sample_id={FAKE_SAMPLE_ID!r}, tests={FAKE_TEST_CODES!r}")
    print("This does NOT touch Postgres, the clinic API, or matcher.py - "
          "wire-protocol proof only.\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(5)
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            conn, addr = sock.accept()
            try:
                _handle_connection(conn, addr)
            except Exception as e:
                print(f"error handling connection: {e}")
            finally:
                conn.close()
            print("ready for next connection.\n")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
