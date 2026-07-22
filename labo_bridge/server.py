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
from .decoders import xn330, ismart, selectra, cyanvision, minividas
from . import matcher, pg, api_client, config, runtime_ports, live_status

HOST = "0.0.0.0"

# How long a connection can sit completely silent before it's treated as
# gone (see the settimeout() call in _serve_one_machine). Analyzers pause
# between results/batches but never for minutes at a stretch while still
# genuinely connected, so this is generous enough to never cut off a real,
# still-active session.
CONNECTION_IDLE_TIMEOUT_SECONDS = 90

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
# "xs500i" (Sysmex XS-500i) is the clinic's main, highest-volume hematology
# analyzer (being supplemented/replaced by the xn330, but both stay in
# active use) - never connected to this bridge before. Unlike the other
# machines, it has no network/host-settings interface of its own - it's
# wired directly into a Windows PC, so there's no analyzer-side menu to
# point at a host IP/port; whatever that PC is configured to do (e.g. a
# serial-to-network bridge, or running its own forwarding software) is what
# determines how/whether data reaches us here - confirm with the person
# managing that PC before assuming this port is even reachable.
# It reuses xn330's decoder as a starting point: both are Sysmex hematology
# analyzers, and the clinic DB already tags the FNS exam's technique as
# "SYSMEX XS 500i" (see mappings.py), so the same ASTM field layout and CBC
# test codes are expected. If its real wire format differs,
# _write_session_file's raw byte dump (results/xs500i_<timestamp>.txt) will
# show exactly what's different so xn330.decode_record can be adjusted or a
# dedicated decoder written.
MACHINES = {
    "xn330":      {"protocol": "astm", "decode_record": xn330.decode_record,
                   "initial_ack": False, "port": 6001},
    "ismart":     {"protocol": "astm", "decode_record": ismart.decode_record,
                   "initial_ack": True, "port": 6002},
    "selectra":   {"protocol": "astm", "decode_record": selectra.decode_record,
                   "initial_ack": False, "port": 6003},
    "cyanvision": {"protocol": "hl7",  "decode_segment": cyanvision.decode_segment,
                   "initial_ack": False, "port": 6004},
    "xs500i":     {"protocol": "astm", "decode_record": xn330.decode_record,
                   "initial_ack": False, "port": 6005},
    # bioMerieux Mini VIDAS, via an FCT-201-F serial-to-Ethernet adapter in
    # TCP Client mode (dials out to us). Confirmed by real capture
    # (2026-07-21) to use the same ENQ/ACK/STX/EOT handshake as ASTM, but a
    # different frame body: RS (\x1e)-separated "<tag><value>" fields
    # instead of '|'-delimited CR-terminated lines - see decoders/minividas.py
    # for the full format. Needs its own connection handler (_handle_minividas)
    # since astm.strip_frame/split_records assume CR/LF framing this doesn't use.
    "minividas":  {"protocol": "minividas", "decode_frame": minividas.decode_frame,
                   "initial_ack": False, "port": 6006},
}

# TEMPORARY TEST HOOK flag - see _handle_astm. Remove alongside it once the
# Selectra host-send test is done.
_selectra_test_sent = False


def _get_machine_id(machine: str):
    """
    Look up the clinic labo_machine.id configured for this machine, from
    labo_bridge.machine_config (editable live via the admin UI - see pg.py's
    module docstring). Returns None if unset or Postgres is unreachable -
    build_item() then falls back to sending the machine name string
    instead, per the API's "machine (name only, no machine_id)" path.
    """
    cfg = pg.get_machine_config(machine)
    return cfg.get("machine_id") if cfg else None


def _ingest_result(session, sample_id, rec):
    """
    Match a result and persist it straight to Postgres - no local SQLite,
    Postgres is the only store (see pg.py):
      - confidently matched (curated) -> labo_bridge.labo_bridge_results,
        and ALSO queued for the clinic API batch if config.USE_MACHINE_RESULT_API
        is on (api_client.py, sent as one combined JSON array per batch per
        API_LABO_MACHINE_RESULT.md's "body must be a JSON array" rule)
      - not matched -> labo_bridge.pending_params, the mapping backlog (one
        row per unmapped test code, not per result - see pg.py) surfaced in
        the admin UI's Mappings section for a human to map
    If Postgres is unreachable, the write is skipped (pg.py warns) - results
    genuinely aren't captured anywhere else while it's down; this is a
    deliberate tradeoff now that Postgres is the sole persistence layer.
    """
    machine, quiet = session.machine, session.quiet

    # ASTM's result-status field (R record's 8th field) marks "R" = repeat/
    # retransmission of an already-reported result - e.g. re-sending the same
    # patient's result a second time, whether deliberately (operator re-runs
    # a report) or automatically (the analyzer resending after not getting a
    # clean ACK). "F" (final) is the normal case; only "R" means "this exact
    # result was already reported once" - skip it so sample detail doesn't
    # show doubled results for the same test. Confirmed via real capture
    # (2026-07-21, I-Smart 30 PRO): the same Na+/K+/Cl-/Ca2+ values, resent
    # ~3 min later, differed ONLY in this field (F -> R).
    if rec.get("status") == "R":
        line = (f"SKIPPED result (status=R, already reported) "
                f"sample={sample_id!r:14} test={rec.get('test_code',''):10s} "
                f"value={rec.get('value',''):>10s}")
        session.parsed_lines.append(line)
        if not quiet:
            print(f"[{machine}] {line}")
        return

    # The analyzer itself flags a measurement it doesn't trust with the
    # literal value "REJECT" (a failed QC check, out-of-range absorbance,
    # etc. - confirmed via real Selectra capture, 2026-07-21) rather than a
    # number. That's not a usable clinical value, so it must never be filed
    # into labo_bridge_results (which would make a rejected reading look
    # like a real Créatinémie/SGOT/etc. result) or sent to the clinic API -
    # skip it entirely, same as a retransmission.
    if rec.get("value", "").strip().upper() == "REJECT":
        line = (f"SKIPPED result (analyzer marked REJECT - failed QC) "
                f"sample={sample_id!r:14} test={rec.get('test_code',''):10s}")
        session.parsed_lines.append(line)
        if not quiet:
            print(f"[{machine}] {line}")
        return

    m = matcher.match(machine, rec.get("test_code", ""))

    if m["method"] == "curated":
        # param_id is None for non-composed exams - service_tarification_id
        # alone is the complete match in that case (see mappings.py).
        target = (f"labo_param.id={m['param_id']}" if m["param_id"]
                  else f"service_tarification.id={m['service_tarification_id']}")
        # ALWAYS write locally, whether or not the API path is on - this is
        # the only way the admin UI (sample detail, mapped-table "last
        # value") can see a result that was sent live to the clinic API;
        # api_sent gets flipped to True by _flush_api_batch once the API
        # actually confirms it, so this starts False when queued for send.
        sent_ok = pg.write_matched_result(machine, sample_id, session.specimen,
                                          rec.get("test_code", ""), m, rec)
        if config.USE_MACHINE_RESULT_API:
            item = api_client.build_item(
                sample_id=sample_id.strip(),
                result_value=rec.get("value", ""),
                unit=rec.get("unit") or None,
                param_id=m.get("param_id"),
                service_tarification_id=m.get("service_tarification_id")
                                         if not m.get("param_id") else None,
                machine=machine,
                machine_id=_get_machine_id(machine),
            )
            session.api_batch.append({"item": item, "sample_id": sample_id,
                                       "test_code": rec.get("test_code", "")})
            tag = (f"matched -> {target} ({m['abbrev']} / {m['name']}) "
                   f"[queued for batched clinic API send]")
        else:
            tag = (f"matched -> {target} ({m['abbrev']} / {m['name']}) "
                   f"{'[written to Postgres]' if sent_ok else '[Postgres write skipped, see warning above]'}")
    else:
        sent_ok = pg.write_pending_param(machine, rec)
        tag = (f"PENDING (no curated match, needs manual review) "
               f"{'[written to Postgres]' if sent_ok else '[Postgres write skipped, see warning above]'}")

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

    After the send, update the local labo_bridge_results rows (already
    written by _ingest_result before queueing) with whether the clinic API
    actually accepted each one - purely local bookkeeping so the admin UI
    can show it; does not affect or delay the send itself.
    """
    if session.api_batch:
        outcomes = api_client.send_batch(session.machine, session.api_batch)
        for o in outcomes:
            if o["api_sent"]:
                pg.mark_api_sent(session.machine, o["sample_id"], o["test_code"], o["api_result_id"])
        session.api_batch = []


def _write_session_file(session):
    """
    Disabled on deployed/production servers: writing one file per session
    (every sample, every calibration cycle, every retransmission) grows
    results/ unboundedly under real continuous machine traffic. Re-enable
    (delete this early return) only for local debugging of a specific
    machine's raw wire format.
    """
    return
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

    def __init__(self, machine, source_ip, quiet):
        self.machine = machine
        self.source_ip = source_ip
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
        # Set when a decoder reports kind="calibration" (currently only
        # ismart.py, for the I-Smart 30 PRO's automatic electrode
        # calibration/QC cycle) - once set, every "result" in the rest of
        # this batch is skipped too (no pending_param, no labo_bridge_results
        # row), since a calibration run's O record is always followed only
        # by its own diagnostic R records, never a mix with a real sample.
        self.is_calibration = False

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
        elif kind == "calibration":
            self.is_calibration = True
            if not self.quiet:
                print(f"[{self.machine}] CALIBRATION run detected - skipping "
                      f"(no sample/result written, this is the machine's own "
                      f"electrode QC cycle, not a patient test)")
        elif kind == "order":
            if self.is_calibration:
                return
            self.sample_id = ev.get("sample_id", "") or self.sample_id
            paillasse = ev.get("paillasse")
            self.specimen = {k: ev[k] for k in ("year", "month", "sequence", "paillasse") if k in ev}
            pg.write_sample(self.machine, self.sample_id or "",
                            self.analyzer_model, self.patient_name,
                            self.patient_id, self.source_ip, paillasse)
            if not self.quiet:
                bench = f" paillasse={paillasse}" if paillasse else ""
                print(f"[{self.machine}] WROTE sample  sample={self.sample_id!r}{bench}")
        elif kind == "result":
            if self.is_calibration:
                return
            sid = self.sample_id or self.patient_id or ""
            if self.sample_id is None:
                # result before any order - stage under best-known id
                pg.write_sample(self.machine, sid, self.analyzer_model,
                                self.patient_name, self.patient_id, self.source_ip)
                self.sample_id = sid
            _ingest_result(self, self.sample_id, ev)
            self.result_count += 1


def _handle_astm(conn, addr, cfg, machine, quiet):
    session = _Session(machine, addr[0], quiet)
    buffer = b""
    if cfg.get("initial_ack"):
        conn.sendall(astm.B_ACK)

    # TEMPORARY TEST HOOK - remove once the Selectra host-send test is done.
    # Sends one harmless ASTM-framed test message the first time Selectra
    # connects after this process started, purely to see whether the
    # Selectra reacts at all to bytes sent FROM labo_bridge (it currently
    # only ever pushes data to us; this checks if the link is bidirectional).
    global _selectra_test_sent
    if machine == "selectra" and not _selectra_test_sent:
        _selectra_test_sent = True
        test_frame = astm.build_frame(1, "hi")
        conn.sendall(test_frame)
        print(f"[{machine}] TEST: sent host message {test_frame!r} - "
              f"watching for any reply/reaction...")

    while True:
        try:
            data = conn.recv(4096)
        except (ConnectionResetError, socket.timeout):
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


def _handle_minividas(conn, addr, cfg, machine, quiet):
    """
    Mini VIDAS connection handler - same ENQ/ACK/STX/EOT control-byte
    handshake as _handle_astm, but frames end in \\x1d<2-char seq> (GS +
    sequence tag) then ETX rather than CR + checksum + CR/LF, and the body
    is RS (\\x1e)-separated tag/value fields rather than '|'-delimited
    lines - so frame boundaries are found by ETX, not LF, and the body is
    handed to minividas.decode_frame() (not split into per-line records).
    """
    session = _Session(machine, addr[0], quiet)
    buffer = b""

    while True:
        try:
            data = conn.recv(4096)
        except (ConnectionResetError, socket.timeout):
            break
        if not data:
            break
        buffer += data
        session.raw_bytes += data

        while buffer:
            b0 = buffer[0]
            if b0 == astm.ENQ:
                conn.sendall(astm.B_ACK)
                buffer = buffer[1:]
            elif b0 == astm.EOT:
                buffer = buffer[1:]
                _flush_api_batch(session)
                _write_session_file(session)
                session.raw_bytes = b""
                session.raw_lines = []
                session.parsed_lines = []
                if not quiet:
                    print(f"[{machine}] batch complete ({session.result_count} results written)")
            elif b0 == astm.STX:
                idx = buffer.find(bytes([astm.ETX]))
                if idx == -1:
                    break
                frame = buffer[1:idx]  # drop leading STX, up to (excl.) ETX
                buffer = buffer[idx + 1:]
                conn.sendall(astm.B_ACK)
                # frame ends "...\x1d<seq>" - drop the GS + trailing sequence
                # tag, which isn't part of the field data itself.
                if "\x1d" in frame.decode("utf-8", errors="replace"):
                    body = frame.decode("utf-8", errors="replace").split("\x1d")[0]
                else:
                    body = frame.decode("utf-8", errors="replace")
                for ev in cfg["decode_frame"](body):
                    session.handle_event(ev)
            else:
                buffer = buffer[1:]


def _handle_hl7(conn, addr, cfg, machine, quiet):
    buffer = b""
    while True:
        try:
            data = conn.recv(4096)
        except (ConnectionResetError, socket.timeout):
            break
        if not data:
            break
        buffer += data

        for message, remainder in hl7_mllp.iter_messages(buffer):
            buffer = remainder
            session = _Session(machine, addr[0], quiet)
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


def _bind_socket(machine: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(5)
    sock.settimeout(1.0)  # so the accept loop can notice stop_event/port changes
    return sock


def _serve_one_machine(machine: str, quiet: bool, stop_event: threading.Event):
    """
    Bind this machine's dedicated port and accept connections forever.
    Also checks runtime_ports on every loop tick (~1s) so the admin UI can
    change the port live - closes the old socket and rebinds to the new one
    without needing to restart this process. A machine actively mid-connection
    finishes that connection on the old socket before the rebind is noticed
    (checked between connections, not mid-transfer).
    """
    cfg = MACHINES[machine]
    port = runtime_ports.get_port_for(machine, cfg["port"])

    sock = _bind_socket(machine, port)
    print(f"[{machine}] listening on {HOST}:{port} ({cfg['protocol'].upper()}). "
          f"Storage: Postgres (labo_bridge schema)")
    live_status.set_listening(machine, datetime.now().isoformat(timespec="seconds"))

    if cfg["protocol"] == "hl7":
        handler = _handle_hl7
    elif cfg["protocol"] == "minividas":
        handler = _handle_minividas
    else:
        handler = _handle_astm
    try:
        while not stop_event.is_set():
            desired_port = runtime_ports.get_port_for(machine, cfg["port"])
            if desired_port != port:
                print(f"[{machine}] port changed {port} -> {desired_port}, rebinding...")
                sock.close()
                try:
                    sock = _bind_socket(machine, desired_port)
                    port = desired_port
                    print(f"[{machine}] now listening on {HOST}:{port}.")
                except OSError as e:
                    print(f"[{machine}] failed to bind port {desired_port} ({e}); "
                          f"staying on {port}.")
                    sock = _bind_socket(machine, port)
                live_status.set_listening(machine, datetime.now().isoformat(timespec="seconds"))
                continue

            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            # Without a read timeout, conn.recv() blocks forever if the peer
            # vanishes without a clean TCP close (power loss, a flaky serial-
            # to-Ethernet adapter, a machine that just stops sending) - the
            # handler would never return, so live_status would stay stuck on
            # "connected" indefinitely even though the machine is long gone.
            # A generous timeout (analyzers go quiet between results/batches,
            # but never for minutes) lets each recv() loop notice the machine
            # is unresponsive and correctly fall back to "listening".
            conn.settimeout(CONNECTION_IDLE_TIMEOUT_SECONDS)
            now_iso = datetime.now().isoformat(timespec="seconds")
            print(f"[{machine}] connected by {addr[0]}:{addr[1]} at {now_iso}")
            live_status.set_connected(machine, now_iso, addr[0])
            try:
                handler(conn, addr, cfg, machine, quiet)
            except Exception as e:
                print(f"[{machine}] error handling connection: {e}")
            finally:
                conn.close()
            print(f"[{machine}] ready for next connection.")
            live_status.set_listening(machine, datetime.now().isoformat(timespec="seconds"))
    finally:
        sock.close()


# Threads started by run_all(), keyed by machine - kept so register_machine()
# can add a listener for a brand-new analyzer without restarting the process
# (mirrors the port-rebind mechanism: change live state, no restart needed).
_running_threads = {}
_running_stop_event = None
_running_quiet = False


def register_machine(machine: str, cfg: dict) -> None:
    """
    Add a new machine to MACHINES and start its listener thread immediately,
    if run_all() is already running in this process. Called by the admin
    UI's Add Analyzer flow right after machines_editor.add_machine() writes
    the same entry into server.py's source file - this makes it live now,
    the on-disk edit makes it survive the next restart.
    """
    MACHINES[machine] = cfg
    if _running_stop_event is None:
        return  # run_all() isn't running in this process (e.g. admin-only mode)
    t = threading.Thread(target=_serve_one_machine,
                         args=(machine, _running_quiet, _running_stop_event),
                         name=f"listener-{machine}", daemon=True)
    t.start()
    _running_threads[machine] = t


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
    global _running_stop_event, _running_quiet
    stop_event = threading.Event()
    _running_stop_event = stop_event
    _running_quiet = quiet
    for machine in MACHINES:
        t = threading.Thread(target=_serve_one_machine, args=(machine, quiet, stop_event),
                             name=f"listener-{machine}", daemon=True)
        t.start()
        _running_threads[machine] = t

    print(f"\nAll {len(_running_threads)} analyzer listeners running. Ports: " +
          ", ".join(f"{m}={cfg['port']}" for m, cfg in MACHINES.items()))
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            # snapshot as a list - register_machine() may add new threads
            # (and thus mutate _running_threads) while we're iterating
            for t in list(_running_threads.values()):
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nStopping all listeners...")
        stop_event.set()
        for t in list(_running_threads.values()):
            t.join(timeout=2)
        print("Stopped.")
