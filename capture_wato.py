#!/usr/bin/env python3
"""
Standalone capture listener for the Mindray WATO EX-35 ONLY - split out from
capture_listener.py so this can be run solely, independent of anything for
Karl Storz (see scan_karlstorz_serial.py for that - a completely different
kind of tool, since Karl Storz's transport isn't even confirmed to be
Ethernet/TCP at all). No ASTM handshake, no matcher, no Postgres, no
clinic-API call here - it binds one TCP port, decodes HL7/MLLP, and prints/
logs every result using REAL Mindray parameter names instead of raw MDC
codes (see MDC_CODES below).

HOW TO POINT THE WATO AT THIS SCRIPT
=====================================
Confirmed directly from Mindray's official "A-Series Communication Protocol
Interface Guide" (fetched from mindray.com), pages 3-2/3-3 - these are the
REAL menu screens, not a guess:

  Ethernet (recommended - simpler, no CRC framing quirk):
    On the WATO: Setup -> System tab -> Network (left sidebar)
      -> "Network Protocol" box:
           HL7            = On
           Destination IP = <the IP of the machine running this script>
           Port           = <DEFAULT_PORT below, i.e. 6010>
           Interval       = 10 Sec (fastest option the menu offers; other
                             choices: 30 Sec, 1/5/30 Min, 1/2/6/12/24 Hour)
    -> Accept

  Serial (RS-232, via SP1 or DP1 on the WATO's rear panel):
    Setup -> System tab -> Network -> "Configure Serial" button
      -> "Serial Configuration" dialog:
           Protocol   = HL7   (NOT "MR-WATO" - the manual states that one is
                        Mindray-internal only and undocumented)
           Baud Rate  = shown as 115200 in Mindray's own example screenshot -
                        that's an EXAMPLE value from the manual, not
                        confirmed as this specific unit's live setting -
                        check the actual screen before assuming
           Data Bits  = 8 (fixed, not editable)
           Stop Bits  = 1
           Parity     = Even (also just the example value - verify live)
           Interval   = 10 sec
    -> Accept -> Accept
    Note: serial mode inserts a 4-char CRC before the closing MLLP <FS> that
    this script's parser does not strip - harmless for discovery (see
    capture_listener.py's docstring for the same note), a real decoder would
    need to strip it.

MDC_CODES below is transcribed directly from Appendix B ("B.8 Ventilator /
Anesthesia Machine Measurement IDs" and "B.9 Airway Gas Analyzer Measurement
IDs") of that same manual - nothing guessed. Standing caveat (see project
memory): that manual is scoped to the A5/A7 line, not WATO-EX-35-specific -
these codes are a strong prior for what the WATO will send, not confirmed
identical, until a real capture is compared against them. Any OBX segment
with a code NOT in this dict is still shown, just without a friendly label -
never guessed at.
"""

import argparse
import os
import socket
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labo_bridge.protocols import hl7_mllp  # noqa: E402 - pure framing helper, no side effects
from clinical_portal.publish import PortalPublisher  # noqa: E402

HOST = "0.0.0.0"
DEFAULT_PORT = 6010
CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
IDLE_TIMEOUT_SECONDS = 90

# {MDC code name: (friendly label, expected unit)} - source: Appendix B,
# A-Series Communication Protocol Interface Guide, both tables in full.
MDC_CODES = {
    # B.8 Ventilator / Anesthesia Machine Measurement IDs
    "MDC_RATIO_IE": ("I:E Ratio", ""),
    "MDC_RATIO_IE_SETTING": ("I:E Ratio (setting)", ""),
    "MDC_VENT_PRESS_MAX": ("Peak Airway Pressure", "cmH2O"),
    "MDC_PRESS_AWAY_INSP_MEAN": ("Mean Airway Pressure", "cmH2O"),
    "MDC_PRESS_RESP_PLAT": ("Plateau Pressure", "cmH2O"),
    "MDC_VENT_PRESS_AWAY_END_EXP_POS": ("PEEP", "cmH2O"),
    "MDC_PRESS_AWAY_END_EXP_POS_SETTING": ("PEEP (setting)", "cmH2O"),
    "MDC_VOL_MINUTE_AWAY": ("Minute Volume", "l/min"),
    "MDC_VOL_AWAY_TIDAL": ("Expiratory Tidal Volume", "mL"),
    "MDC_VOL_AWAY_TIDAL_SETTING": ("Tidal Volume (setting)", "mL"),
    "MDC_VENT_RESP_RATE": ("Respiratory Rate", "rpm"),
    "MDC_VENT_RESP_RATE_SETTING": ("Respiratory Rate (setting)", "rpm"),
    "MDC_RES_AWAY": ("Airway Resistance", "cmH2O/l/s"),
    "MDC_COMPL_LUNG": ("Lung Compliance", "ml/cmH2O"),
    "MDC_FLOW_O2_FG": ("O2 Fresh Gas Flow", "l/min"),
    "MDC_FLOW_N2O_FG": ("N2O Fresh Gas Flow", "l/min"),
    "MDC_FLOW_AIR_FG": ("AIR Fresh Gas Flow", "l/min"),
    # B.9 Airway Gas Analyzer Measurement IDs (all require the external/
    # internal AG module to be physically installed - see project memory)
    "MDC_CONC_AWAY_O2_ET": ("EtO2", "%"),
    "MDC_CONC_AWAY_O2_INSP": ("FiO2", "%"),
    "MDC_CONC_AWAY_CO2_ET": ("EtCO2", "mmHg"),
    "MDC_CONC_AWAY_CO2_INSP": ("FiCO2", "mmHg"),
    "MDC_CO2_RESP_RATE": ("Respiratory Rate (from CO2)", "rpm"),
    "MDC_CONC_AWAY_N2O_ET": ("EtN2O", "%"),
    "MDC_CONC_AWAY_N2O_INSP": ("FiN2O", "%"),
    "MDC_CONC_AWAY_AGENT_ET": ("Exp Anesthetic Agent (unspecified)", "%"),
    "MDC_CONC_AWAY_AGENT_INSP": ("Insp Anesthetic Agent (unspecified)", "%"),
    "MDC_CONC_AWAY_HALOTH_ET": ("Exp Halothane", "%"),
    "MDC_CONC_AWAY_HALOTH_INSP": ("Insp Halothane", "%"),
    "MDC_VOL_DELIV_HALOTH_LIQUID_CASE": ("Cumulative Halothane Usage", "mL"),
    "MDC_CONC_AWAY_ENFL_ET": ("Exp Enflurane", "%"),
    "MDC_CONC_AWAY_ENFL_INSP": ("Insp Enflurane", "%"),
    "MDC_VOL_DELIV_ENFL_LIQUID_CASE": ("Cumulative Enflurane Usage", "mL"),
    "MDC_CONC_AWAY_ISOFL_ET": ("Exp Isoflurane", "%"),
    "MDC_CONC_AWAY_ISOFL_INSP": ("Insp Isoflurane", "%"),
    "MDC_VOL_DELIV_ISOFL_LIQUID_CASE": ("Cumulative Isoflurane Usage", "mL"),
    "MDC_CONC_AWAY_SEVOFL_ET": ("Exp Sevoflurane", "%"),
    "MDC_CONC_AWAY_SEVOFL_INSP": ("Insp Sevoflurane", "%"),
    "MDC_VOL_DELIV_SEVOFL_LIQUID_CASE": ("Cumulative Sevoflurane Usage", "mL"),
    "MDC_CONC_AWAY_DESFL_ET": ("Exp Desflurane", "%"),
    "MDC_CONC_AWAY_DESFL_INSP": ("Insp Desflurane", "%"),
    "MDC_VOL_DELIV_DESFL_LIQUID_CASE": ("Cumulative Desflurane Usage", "mL"),
    "MDC_CONC_MAC": ("MAC (Minimum Alveolar Concentration)", ""),
}


def _readable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 or b in (9, 10, 13) else f"\\x{b:02x}" for b in data)


def _annotate_obx(segment: str):
    """Return 'Friendly Label = value unit' for a recognized MDC code, or
    None (segment printed as raw HL7 only) for anything not in MDC_CODES -
    never guessed."""
    fields = segment.split("|")
    if not fields or fields[0] != "OBX" or len(fields) < 6:
        return None
    code_parts = fields[3].split("^")
    mdc_name = code_parts[1] if len(code_parts) > 1 else ""
    entry = MDC_CODES.get(mdc_name)
    if not entry:
        return None
    label, unit = entry
    value = fields[5].replace("^", " ").strip()  # SN-type (e.g. I:E "^4^:^1") -> "4 : 1"
    return f"{label} = {value}{(' ' + unit) if unit else ''}"


def _portal_reading(segment: str):
    """Normalize only a numeric OBX; never guess a value from coded text."""
    fields = segment.split("|")
    if not fields or fields[0] != "OBX" or len(fields) < 6:
        return None
    try:
        float(fields[5].strip())
    except (TypeError, ValueError):
        return None

    identifier = fields[3].split("^")
    code = next((part for part in identifier if part.startswith("MDC_")), identifier[0])
    known = MDC_CODES.get(code)
    label = known[0] if known else (identifier[1] if len(identifier) > 1 else code)
    unit = ""
    if len(fields) > 6 and fields[6]:
        unit = fields[6].split("^", 1)[0]
    if not unit and known:
        unit = known[1]
    return {"code": code, "label": label, "value": fields[5].strip(), "unit": unit}


def _portal_patient(segments):
    for segment in segments:
        fields = segment.split("|")
        if fields and fields[0] == "PID":
            return {
                "id": fields[3].strip() if len(fields) > 3 else "",
                "name": fields[5].replace("^", " ").strip() if len(fields) > 5 else "",
            }
    return None


def _handle_connection(conn, addr, portal_publisher=None):
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(CAPTURES_DIR, f"wato_ex35_{ts}_{addr[0]}.log")
    raw_path = os.path.join(CAPTURES_DIR, f"wato_ex35_{ts}_{addr[0]}.raw")
    print(f"[wato_ex35] connected by {addr[0]}:{addr[1]}\n"
          f"[wato_ex35]   readable log -> {log_path}\n"
          f"[wato_ex35]   raw bytes    -> {raw_path}")

    log = open(log_path, "a", encoding="utf-8")
    raw = open(raw_path, "ab")

    def _log(text):
        log.write(text)
        log.flush()

    _log(f"=== capture started {datetime.now().isoformat()} from {addr[0]}:{addr[1]} ===\n")
    _log(f"raw bytes for this session also saved to {os.path.basename(raw_path)}\n")

    buffer = b""
    total_bytes = 0
    mllp_count = 0
    conn.settimeout(IDLE_TIMEOUT_SECONDS)
    try:
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                print(f"[wato_ex35] no data for {IDLE_TIMEOUT_SECONDS}s, still connected, waiting...")
                continue
            except ConnectionResetError:
                break
            if not data:
                break

            raw.write(data)  # exact bytes, before anything below looks at them
            raw.flush()

            total_bytes += len(data)
            buffer += data
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            chunk_block = (f"[{stamp}] RECV {len(data)} bytes\n"
                            f"  hex: {data.hex()}\n"
                            f"  txt: {_readable(data)}\n")
            print(f"[wato_ex35] {chunk_block}", end="")
            _log(chunk_block)

            for message, remainder in hl7_mllp.iter_messages(buffer):
                buffer = remainder
                mllp_count += 1
                segments = hl7_mllp.split_segments(message)
                block_lines = [f"  ---- MLLP message #{mllp_count} decoded "
                               f"({len(segments)} segments) ----"]
                for seg in segments:
                    annotation = _annotate_obx(seg)
                    line = f"    {seg}"
                    if annotation:
                        line += f"\n        --> {annotation}"
                    block_lines.append(line)
                block_lines.append("  ---- end message ----")
                block = "\n".join(block_lines) + "\n"
                print(f"[wato_ex35]\n{block}", end="")
                _log(block)

                if portal_publisher is not None:
                    readings = [reading for reading in
                                (_portal_reading(segment) for segment in segments)
                                if reading is not None]
                    patient = _portal_patient(segments)
                    if readings or patient:
                        portal_publisher.publish(readings=readings, patient=patient)

                control_id = "0"
                for seg in segments:
                    fields = seg.split("|")
                    if fields and fields[0] == "MSH" and len(fields) > 9:
                        control_id = fields[9]
                        break
                try:
                    conn.sendall(hl7_mllp.build_ack(control_id))
                    print(f"[wato_ex35] << sent HL7 ACK (control_id={control_id!r})")
                    _log(f"  << sent HL7 ACK (control_id={control_id!r})\n")
                except OSError as e:
                    print(f"[wato_ex35] failed to send ACK: {e}")
    finally:
        _log(f"=== capture ended {datetime.now().isoformat()} "
             f"({total_bytes} bytes total, {mllp_count} MLLP message(s) decoded) ===\n")
        log.close()
        raw.close()
        conn.close()
        print(f"[wato_ex35] disconnected {addr[0]}:{addr[1]} - {total_bytes} bytes, "
              f"{mllp_count} MLLP message(s) decoded.\n"
              f"[wato_ex35]   readable log -> {log_path}\n"
              f"[wato_ex35]   raw bytes    -> {raw_path}")


def main():
    global CAPTURES_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT,
                        help=f"Mac TCP listener port (default: {DEFAULT_PORT})")
    parser.add_argument("--portal-url",
                        help="publish normalized readings to this local portal, e.g. http://127.0.0.1:5050")
    parser.add_argument("--chamber", type=int, choices=(1, 2, 3),
                        help="operating chamber containing this WATO")
    parser.add_argument("--captures-dir",
                        help="directory for raw/readable captures (default: project captures/)")
    args = parser.parse_args()
    if bool(args.portal_url) != bool(args.chamber):
        parser.error("--portal-url and --chamber must be supplied together")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.captures_dir:
        CAPTURES_DIR = os.path.abspath(args.captures_dir)
    port = args.port
    portal_publisher = (
        PortalPublisher(args.portal_url, args.chamber, "wato")
        if args.portal_url else None
    )
    os.makedirs(CAPTURES_DIR, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(5)
    sock.settimeout(1.0)

    print(f"WATO EX-35 capture listener - listening on {HOST}:{port}")
    print(f"Point the WATO's Destination IP at this machine and Port at {port} "
          f"(see this file's docstring for the exact on-device menu path).")
    print(f"Captures saved under {CAPTURES_DIR}/\n")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            try:
                _handle_connection(conn, addr, portal_publisher=portal_publisher)
            except Exception as e:
                print(f"[wato_ex35] error handling connection from {addr}: {e}")
            print("[wato_ex35] ready for next connection.")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
