"""
Shared TCP server + per-connection dispatch.

Two protocol styles are supported behind one accept-loop:

  ASTM/LIS2-A (xn330, ismart, selectra): ENQ/ACK/STX-framing, records decoded
      one line at a time by the machine's decode_record().
  HL7/MLLP    (cyanvision): whole segments decoded by decode_segment(), and a
      full HL7 ACK message is sent back per message.

Each machine's config (protocol, decoder, port, extras) is declared in MACHINES.
Every machine gets its OWN port, since the wire protocols don't self-identify
enough to share one socket - run_all() starts one listener thread per machine
in a single process so all four analyzers can stay connected simultaneously.
"""

import os
import socket
import threading
from datetime import datetime

from .protocols import astm, hl7_mllp
from .decoders import xn330, ismart, selectra, cyanvision
from . import storage, matcher, pg, api_client, config

HOST = "0.0.0.0"

# Every session (one ASTM batch / one HL7 message) gets its raw records and
# parsed results saved here automatically - always, for every machine,
# regardless of match status or API config. This is the reliable way to go
# back and inspect exactly what an analyzer sent (e.g. to find which field
# actually carries a value a decoder is reading from the wrong place)
# instead of relying on live terminal output nobody captured.
RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "results"))

# machine -> config. Each machine listens on its own fixed port.
# "selectra" is the chemistry analyzer's real machine name (ELITech is the
# software/protocol stack it runs, not the machine itself).
MACHINES = {
    "xn330":      {"protocol": "astm", "decode_record": xn330.decode_record,
                   "initial_ack": False, "port": 6001},
    "ismart":     {"protocol": "astm", "decode_record": ismart.decode_record,
                   "initial_ack": True, "port": 6002},
    "selectra":   {"protocol": "astm", "decode_record": selectra.decode_record,
                   "initial_ack": False, "port": 6003},
    "cyanvision": {"protocol": "hl7",  "decode_segment": cyanvision.decode_segment,
                   "initial_ack": False, "port": 6004},
}


def _ingest_result(session, sample_id, rec):
    """
    Store a result locally and stage its match. If the match is confident
    (curated), stage it for the downstream push:
      - clinic API path (api_client.py): QUEUED onto session.api_batch and
        flushed as ONE combined JSON array at the batch/message boundary
        (_flush_api_batch) - never one POST call per result, per
        API_LABO_MACHINE_RESULT.md's "body must be a JSON array" rule.
      - clinic Postgres staging table path (pg.py, temporary): written
        immediately, one row per result - plain SQL inserts don't need
        batching the way the HTTP API does.
    Controlled by config.USE_MACHINE_RESULT_API. Pending/unmatched results
    never leave the local SQLite db either way. Shared by both protocol paths.
    """
    machine, conn_db, quiet = session.machine, session.db, session.quiet
    storage.store_result(conn_db, machine, sample_id, rec)
    m = matcher.match(machine, rec.get("test_code", ""))
    storage.store_match(conn_db, machine, sample_id, rec, m)

    if m["method"] == "curated":
        # param_id is None for non-composed exams - service_tarification_id
        # alone is the complete match in that case (see mappings.py).
        target = (f"labo_param.id={m['param_id']}" if m["param_id"]
                  else f"service_tarification.id={m['service_tarification_id']}")
        if config.USE_MACHINE_RESULT_API:
            item = api_client.build_item(
                sample_id=sample_id.strip(),
                result_value=rec.get("value", ""),
                unit=rec.get("unit") or None,
                param_id=m.get("param_id"),
                service_tarification_id=m.get("service_tarification_id")
                                         if not m.get("param_id") else None,
                machine=machine,
            )
            session.api_batch.append({"item": item, "sample_id": sample_id,
                                       "test_code": rec.get("test_code", "")})
            tag = (f"matched -> {target} ({m['abbrev']} / {m['name']}) "
                   f"[queued for batched clinic API send]")
        else:
            sent_ok = pg.write_matched_result(machine, sample_id, session.specimen,
                                              rec.get("test_code", ""), m, rec)
            tag = (f"matched -> {target} ({m['abbrev']} / {m['name']}) "
                   f"{'[written to clinic PG staging table]' if sent_ok else '[clinic PG staging table write skipped, see warning above]'}")
    else:
        tag = "PENDING (no curated match, needs manual review, local only)"

    line = (f"WROTE result  sample={sample_id!r:14} "
            f"test={rec.get('test_code',''):10s} value={rec.get('value',''):>10s} "
            f"unit={rec.get('unit',''):8s} | {tag}")
    session.parsed_lines.append(line)
    if not quiet:
        print(f"[{machine}] {line}")


def _flush_api_batch(session):
    """
    Send every result queued this batch/message as ONE JSON array - called
    at the natural batch boundary (ASTM EOT, or end of one HL7 message).
    No-op if nothing was queued (API path off, or nothing matched).
    """
    if session.api_batch:
        api_client.send_batch(session.machine, session.api_batch)
        session.api_batch = []


def _write_session_file(session):
    """
    Save this session's raw records (exactly as received, in order) and
    parsed results to results/<machine>_<timestamp>.txt - called at the same
    batch boundary as _flush_api_batch. Always written, whether or not
    anything matched or the API path is on, so nothing is ever only visible
    in a terminal someone forgot to capture.
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    fname = os.path.join(RESULTS_DIR, f"{session.machine}_{ts}.txt")
    with open(fname, "w") as f:
        f.write(f"=== {session.machine} session ===\n")
        f.write(f"Captured: {datetime.now().isoformat()}\n")
        f.write(f"Source IP: {session.source_ip}\n")
        f.write(f"Analyzer: {session.analyzer_model}\n")
        f.write(f"Patient: {session.patient_name or '(none)'}  "
                f"(ID: {session.patient_id or '(none)'})\n")
        f.write(f"Sample ID: {session.sample_id}\n")
        f.write("\n-- FULL raw bytes received from the machine, unprocessed "
                "(includes STX/ETX/ENQ/EOT/checksums, everything, no filtering) --\n")
        f.write(repr(session.raw_bytes) + "\n")
        f.write("\n-- Same bytes, readable (control chars shown as \\xNN, nothing hidden) --\n")
        readable = "".join(
            chr(b) if 32 <= b < 127 or b in (9, 10, 13) else f"\\x{b:02x}"
            for b in session.raw_bytes
        )
        f.write(readable + "\n")
        f.write("\n-- Raw records, split one per line (decoder's view after framing "
                "is stripped - cross-check against the FULL bytes above) --\n")
        for raw_line in session.raw_lines:
            f.write(raw_line + "\n")
        f.write("\n-- Parsed results --\n")
        for parsed_line in session.parsed_lines:
            f.write(parsed_line + "\n")
    if not session.quiet:
        print(f"[{session.machine}] saved session log to {fname}")


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
        self.specimen = {}  # year/month/sequence/paillasse, when parseable
        self.result_count = 0
        self.api_batch = []  # queued clinic-API items, flushed at batch end
        self.raw_bytes = b""  # every byte received this batch, BEFORE any framing/decoding
        self.raw_lines = []  # every record's raw text, in order - see _write_session_file
        self.parsed_lines = []  # formatted result summary lines, see _write_session_file

    def handle_event(self, ev):
        kind = ev.get("kind")
        if ev.get("raw"):
            self.raw_lines.append(ev["raw"])
        if kind == "header":
            self.analyzer_model = ev.get("analyzer_model", "")
            if not self.quiet:
                print(f"[{self.machine}] HEADER analyzer={self.analyzer_model}")
        elif kind == "patient":
            self.patient_id = ev.get("patient_id", "")
            self.patient_name = ev.get("patient_name", "")
            if not self.quiet:
                print(f"[{self.machine}] PATIENT {self.patient_name or '(none)'} "
                      f"(ID: {self.patient_id or '(none)'})")
        elif kind == "order":
            self.sample_id = ev.get("sample_id", "") or self.sample_id
            paillasse = ev.get("paillasse")
            self.specimen = {k: ev[k] for k in ("year", "month", "sequence", "paillasse") if k in ev}
            storage.store_sample(self.db, self.machine, self.sample_id or "",
                                 self.analyzer_model, self.patient_name,
                                 self.patient_id, self.source_ip, paillasse)
            if not self.quiet:
                bench = f" paillasse={paillasse}" if paillasse else ""
                print(f"[{self.machine}] WROTE sample  sample={self.sample_id!r}{bench}")
        elif kind == "result":
            sid = self.sample_id or self.patient_id or ""
            if self.sample_id is None:
                # result before any order - stage under best-known id
                storage.store_sample(self.db, self.machine, sid,
                                     self.analyzer_model, self.patient_name,
                                     self.patient_id, self.source_ip)
                self.sample_id = sid
            _ingest_result(self, self.sample_id, ev)
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
        session.raw_bytes += data  # exact bytes as received, before any framing/decoding

        while buffer:
            b0 = buffer[0]
            if b0 == astm.ENQ:
                conn.sendall(astm.B_ACK)
                buffer = buffer[1:]
            elif b0 == astm.EOT:
                buffer = buffer[1:]
                _flush_api_batch(session)
                _write_session_file(session)
                # A single connection can carry multiple ENQ..EOT batches -
                # reset per-batch accumulators so each file reflects only
                # its own batch, not every batch seen on this connection.
                session.raw_bytes = b""
                session.raw_lines = []
                session.parsed_lines = []
                if not quiet:
                    print(f"[{machine}] batch complete ({session.result_count} results written)")
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
            # Reconstruct the exact bytes as received, including the MLLP
            # envelope (VT/FS/CR) that iter_messages strips off for parsing.
            session.raw_bytes = hl7_mllp.B_VT + message + hl7_mllp.B_FS + bytes([hl7_mllp.CR])
            control_id = ""
            for seg in hl7_mllp.split_segments(message):
                fields = seg.split("|")
                ev = cfg["decode_segment"](fields)
                if ev.get("kind") == "header":
                    control_id = ev.get("control_id", "")
                session.handle_event(ev)
            _flush_api_batch(session)
            _write_session_file(session)
            if not quiet:
                print(f"[{machine}] message complete ({session.result_count} results written)")
            conn.sendall(hl7_mllp.build_ack(control_id or "0"))


def _serve_one_machine(machine: str, quiet: bool, stop_event: threading.Event):
    """Bind this machine's dedicated port and accept connections forever."""
    cfg = MACHINES[machine]
    port = cfg["port"]
    conn_db = storage.connect()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(5)
    sock.settimeout(1.0)  # so we can notice stop_event without blocking forever
    print(f"[{machine}] listening on {HOST}:{port} ({cfg['protocol'].upper()}). "
          f"DB: {storage.DB_PATH}")

    handler = _handle_hl7 if cfg["protocol"] == "hl7" else _handle_astm
    try:
        while not stop_event.is_set():
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            print(f"[{machine}] connected by {addr[0]}:{addr[1]} at "
                  f"{datetime.now().isoformat(timespec='seconds')}")
            try:
                handler(conn, addr, cfg, machine, conn_db, quiet)
            except Exception as e:
                print(f"[{machine}] error handling connection: {e}")
            finally:
                conn.close()
            print(f"[{machine}] ready for next connection.")
    finally:
        sock.close()
        conn_db.close()


def run(machine: str, quiet: bool = False):
    """Run a single machine's listener in the current thread (blocks)."""
    if machine not in MACHINES:
        raise SystemExit(f"unknown machine {machine!r}; "
                         f"choose from {sorted(MACHINES)}")
    stop_event = threading.Event()
    try:
        _serve_one_machine(machine, quiet, stop_event)
    except KeyboardInterrupt:
        print(f"\n[{machine}] stopped.")


def run_all(quiet: bool = False):
    """
    Run all machines' listeners simultaneously, each on its own port, in one
    process. This is the normal deployment mode: every analyzer stays
    connected to this same server at once.
    """
    stop_event = threading.Event()
    threads = []
    for machine in MACHINES:
        t = threading.Thread(target=_serve_one_machine, args=(machine, quiet, stop_event),
                             name=f"listener-{machine}", daemon=True)
        t.start()
        threads.append(t)

    print(f"\nAll {len(threads)} analyzer listeners running. Ports: " +
          ", ".join(f"{m}={cfg['port']}" for m, cfg in MACHINES.items()))
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nStopping all listeners...")
        stop_event.set()
        for t in threads:
            t.join(timeout=2)
        print("Stopped.")
