"""
Shared TCP server + per-connection dispatch.

Two protocol styles are supported behind one accept-loop:

  ASTM/LIS2-A (xn330, ismart, elitech): ENQ/ACK/STX-framing, records decoded
      one line at a time by the machine's decode_record().
  HL7/MLLP    (cyanvision): whole segments decoded by decode_segment(), and a
      full HL7 ACK message is sent back per message.

Each machine's config (protocol, decoder, extras) is declared in MACHINES.
"""

import socket
from datetime import datetime

from .protocols import astm, hl7_mllp
from .decoders import xn330, ismart, elitech, cyanvision
from . import storage, matcher

HOST = "0.0.0.0"
PORT = 6000

# machine -> config
MACHINES = {
    "xn330":      {"protocol": "astm", "decode_record": xn330.decode_record,
                   "initial_ack": False},
    "ismart":     {"protocol": "astm", "decode_record": ismart.decode_record,
                   "initial_ack": True},
    "elitech":    {"protocol": "astm", "decode_record": elitech.decode_record,
                   "initial_ack": False},
    "cyanvision": {"protocol": "hl7",  "decode_segment": cyanvision.decode_segment,
                   "initial_ack": False},
}


def _ingest_result(conn_db, machine, sample_id, rec, quiet):
    """Store a result and stage its match. Shared by both protocol paths."""
    storage.store_result(conn_db, machine, sample_id, rec)
    m = matcher.match(machine, rec.get("test_code", ""))
    storage.store_match(conn_db, machine, sample_id, rec, m)
    if not quiet:
        tag = (f"-> param {m['param_id']} ({m['abbrev']})"
               if m["param_id"] else "-> PENDING (no curated match)")
        print(f"    {rec.get('test_code',''):10s} {rec.get('value',''):>10s} "
              f"{rec.get('unit',''):8s} {tag}")


class _Session:
    """Tracks header/patient/order state across records within a connection."""

    def __init__(self, machine, source_ip, conn_db, quiet):
        self.machine = machine
        self.source_ip = source_ip
        self.db = conn_db
        self.quiet = quiet
        self.analyzer_model = ""
        self.patient_name = ""
        self.patient_id = ""
        self.sample_id = None
        self.result_count = 0

    def handle_event(self, ev):
        kind = ev.get("kind")
        if kind == "header":
            self.analyzer_model = ev.get("analyzer_model", "")
            if not self.quiet:
                print(f"  [HEADER] {self.analyzer_model}")
        elif kind == "patient":
            self.patient_id = ev.get("patient_id", "")
            self.patient_name = ev.get("patient_name", "")
            if not self.quiet:
                print(f"  [PATIENT] {self.patient_name or '(none)'} "
                      f"(ID: {self.patient_id or '(none)'})")
        elif kind == "order":
            self.sample_id = ev.get("sample_id", "") or self.sample_id
            storage.store_sample(self.db, self.machine, self.sample_id or "",
                                 self.analyzer_model, self.patient_name,
                                 self.patient_id, self.source_ip)
            if not self.quiet:
                print(f"  [ORDER] sample {self.sample_id}")
        elif kind == "result":
            sid = self.sample_id or self.patient_id or ""
            if self.sample_id is None:
                # result before any order - stage under best-known id
                storage.store_sample(self.db, self.machine, sid,
                                     self.analyzer_model, self.patient_name,
                                     self.patient_id, self.source_ip)
                self.sample_id = sid
            _ingest_result(self.db, self.machine, self.sample_id, ev, self.quiet)
            self.result_count += 1


def _handle_astm(conn, addr, cfg, machine, conn_db, quiet):
    session = _Session(machine, addr[0], conn_db, quiet)
    buffer = b""
    if cfg.get("initial_ack"):
        conn.sendall(astm.B_ACK)

    while True:
        try:
            data = conn.recv(4096)
        except ConnectionResetError:
            break
        if not data:
            break
        buffer += data

        while buffer:
            b0 = buffer[0]
            if b0 == astm.ENQ:
                conn.sendall(astm.B_ACK)
                buffer = buffer[1:]
            elif b0 == astm.EOT:
                buffer = buffer[1:]
                if not quiet:
                    print(f"  >> batch complete ({session.result_count} results)")
            elif b0 == astm.STX:
                idx = buffer.find(bytes([astm.LF]))
                if idx == -1:
                    break
                frame = buffer[:idx + 1]
                buffer = buffer[idx + 1:]
                conn.sendall(astm.B_ACK)
                for rec_line in astm.split_records(astm.strip_frame(frame)):
                    session.handle_event(cfg["decode_record"](rec_line))
            else:
                buffer = buffer[1:]


def _handle_hl7(conn, addr, cfg, machine, conn_db, quiet):
    buffer = b""
    while True:
        try:
            data = conn.recv(4096)
        except ConnectionResetError:
            break
        if not data:
            break
        buffer += data

        for message, remainder in hl7_mllp.iter_messages(buffer):
            buffer = remainder
            session = _Session(machine, addr[0], conn_db, quiet)
            control_id = ""
            for seg in hl7_mllp.split_segments(message):
                fields = seg.split("|")
                ev = cfg["decode_segment"](fields)
                if ev.get("kind") == "header":
                    control_id = ev.get("control_id", "")
                session.handle_event(ev)
            if not quiet:
                print(f"  >> message complete ({session.result_count} results)")
            conn.sendall(hl7_mllp.build_ack(control_id or "0"))


def run(machine: str, quiet: bool = False):
    if machine not in MACHINES:
        raise SystemExit(f"unknown machine {machine!r}; "
                         f"choose from {sorted(MACHINES)}")
    cfg = MACHINES[machine]
    conn_db = storage.connect()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(5)
    print(f"[{machine}] listening on {HOST}:{PORT} "
          f"({cfg['protocol'].upper()}). DB: {storage.DB_PATH}")
    print("Waiting for the analyzer... Ctrl+C to stop.\n")

    handler = _handle_hl7 if cfg["protocol"] == "hl7" else _handle_astm
    try:
        while True:
            conn, addr = sock.accept()
            print(f"Connected by {addr[0]}:{addr[1]} at "
                  f"{datetime.now().isoformat(timespec='seconds')}")
            try:
                handler(conn, addr, cfg, machine, conn_db, quiet)
            except Exception as e:
                print(f"  error handling connection: {e}")
            finally:
                conn.close()
            print("Ready for next connection.\n")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()
        conn_db.close()
