#!/usr/bin/env python3
"""
Capture realtime data from a Mindray uMEC12 using Mindray's Patient Data
Share (PDS) protocol.

The uMEC12 does not push its measurements to an arbitrary TCP listener. It
broadcasts an online ADT^A01 notification once per second on UDP port 4600.
A client must then connect to the monitor on TCP port 4601, send a QRY^R02
request, and exchange an ORU^R01/106 echo every second. Realtime values arrive
as HL7 ORU^R01 messages over MLLP after the query is accepted.

This is a discovery/capture tool only. It does not write to Postgres or send
anything to the clinic API.

Usage:
    python3 -u capture_umec12.py --monitor-ip 192.168.1.113
    python3 -u capture_umec12.py --monitor-ip 192.168.1.113 --parameters 160,161,162
    python3 -u capture_umec12.py --monitor-ip 192.168.1.113 --verbose
    python3 -u capture_umec12.py --discover-only
"""

import argparse
import ipaddress
import os
import socket
import threading
import time
from datetime import datetime

from labo_bridge.protocols import hl7_mllp


CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
DEFAULT_MONITOR_IP = "192.168.1.113"
DEFAULT_MONITOR_PORT = 4601
UDP_PORTS = (4600, 3501)
DEFAULT_PARAMETER_IDS = (
    "101",              # HR
    "151",              # respiration rate
    "160", "161", "162",  # SpO2, pulse rate, perfusion index
    "170", "171", "172", "173",  # NIBP
    "200", "201", "202",  # temperature
)

MLLP_START = b"\x0b"
MLLP_END = b"\x1c\x0d"

# Mindray PDS Appendix B defaults for the parameters most commonly available
# on a uMEC monitor. The OBX text sent by the monitor is always preferred; the
# map mainly supplies a useful unit because PDS normally omits OBX-6.
DEFAULT_UNITS = {
    "101": "bpm",     # HR
    "151": "rpm",     # RR
    "160": "%",       # SpO2
    "161": "bpm",     # PR
    "162": "%",       # perfusion index
    "170": "mmHg",    # NIBP systolic
    "171": "mmHg",    # NIBP diastolic
    "172": "mmHg",    # NIBP mean
    "173": "bpm",     # NIBP pulse rate
    "200": "degC",    # T1
    "201": "degC",    # T2
    "202": "degC",    # temperature difference
}


def _mllp(message: str) -> bytes:
    return MLLP_START + message.encode("iso-8859-1") + MLLP_END


def _query_message(parameter_ids=DEFAULT_PARAMETER_IDS) -> bytes:
    """Request selected parameters plus physiological and technical alarms.

    Explicit IDs are the compatibility-oriented default. Mindray documents
    send-all queries, but this uMEC12 accepted such a session without emitting
    periodic control=204 values even while SpO2 was visible on screen. Explicit
    QRF lists avoid that older-firmware behavior. Mindray limits each list to
    fewer than five IDs, so IDs are split into groups of four.
    """
    stamp = datetime.now().strftime("%Y%m%d%H%M%S") + "000"
    query_id = datetime.now().strftime("Q%H%M%S")
    message = [
        "MSH|^~\\&|||||||QRY^R02|1203|P|2.3.1\r"
        f"QRD|{stamp}|R|I|{query_id}|||||RES\r"
    ]
    if parameter_ids is None:
        message.append("QRF|MON||||0&0^1^1^1^\r")
    else:
        ids = [str(code).strip() for code in parameter_ids if str(code).strip()]
        for start in range(0, len(ids), 4):
            code_list = "&".join(ids[start:start + 4])
            message.append(f"QRF|MON||||0&0^1^1^0^{code_list}\r")
    message.extend([
        "QRF|MON||||0&0^3^1^1^\r",  # all physiological alarms
        "QRF|MON||||0&0^4^1^1^\r",  # all technical alarms
    ])
    return _mllp("".join(message))


def _echo_message() -> bytes:
    return _mllp("MSH|^~\\&|||||||ORU^R01|106|P|2.3.1|\r")


def _strip_mllp(data: bytes) -> bytes:
    if data.startswith(MLLP_START):
        data = data[1:]
    if data.endswith(MLLP_END):
        data = data[:-2]
    elif data.endswith(b"\x1c"):
        data = data[:-1]
    return data


def _segments(data: bytes):
    text = _strip_mllp(data).decode("iso-8859-1", errors="replace")
    return [segment for segment in text.split("\r") if segment.strip()]


def _message_summary(data: bytes):
    """Return readable summary lines and structured OBX values."""
    segments = _segments(data)
    message_type = ""
    control_id = ""
    results = []
    patient = {}

    for segment in segments:
        fields = segment.split("|")
        if fields[0] == "MSH":
            message_type = fields[8] if len(fields) > 8 else ""
            control_id = fields[9] if len(fields) > 9 else ""
        elif fields[0] == "PID":
            patient["id"] = fields[3] if len(fields) > 3 else ""
            patient["name"] = fields[5].replace("^", " ").strip() if len(fields) > 5 else ""
        elif fields[0] == "OBX" and len(fields) > 5:
            identifier = fields[3].split("^", 1)
            code = identifier[0]
            name = identifier[1] if len(identifier) > 1 else ""
            value = fields[5]
            unit = ""
            if len(fields) > 6 and fields[6]:
                unit = fields[6].split("^", 1)[0]
            if not unit:
                unit = DEFAULT_UNITS.get(code, "")
            results.append({
                "code": code,
                "name": name,
                "module": fields[4] if len(fields) > 4 else "",
                "value": value,
                "unit": unit,
            })

    return {
        "message_type": message_type,
        "control_id": control_id,
        "patient": patient,
        "results": results,
        "segments": segments,
    }


def _broadcast_tcp_endpoint(data: bytes, source_ip: str):
    """Extract monitor IP/TCP port from the ADT PV1 location when available."""
    for segment in _segments(data):
        fields = segment.split("|")
        if not fields or fields[0] != "PV1" or len(fields) <= 3:
            continue
        components = fields[3].split("^")
        if len(components) < 3:
            continue
        subcomponents = components[2].split("&")
        if len(subcomponents) < 4:
            continue
        try:
            advertised_ip = str(ipaddress.IPv4Address(int(subcomponents[2])))
            advertised_port = int(subcomponents[3])
            return advertised_ip, advertised_port
        except (ValueError, ipaddress.AddressValueError):
            continue
    return source_ip, DEFAULT_MONITOR_PORT


def _print_message(prefix: str, data: bytes, log):
    parsed = _message_summary(data)
    heading = (f"{prefix} {parsed['message_type'] or 'unknown'} "
               f"control={parsed['control_id'] or '-'}")
    print(heading)
    log.write(heading + "\n")

    if parsed["patient"].get("id") or parsed["patient"].get("name"):
        line = (f"  PATIENT id={parsed['patient'].get('id', '')!r} "
                f"name={parsed['patient'].get('name', '')!r}")
        print(line)
        log.write(line + "\n")

    if parsed["results"]:
        for result in parsed["results"]:
            label = result["name"] or result["code"]
            suffix = f" {result['unit']}" if result["unit"] else ""
            line = (f"  OBX {result['code']:>5} {label:<24} = "
                    f"{result['value']}{suffix}  module={result['module'] or '-'}")
            print(line)
            log.write(line + "\n")
    else:
        for segment in parsed["segments"]:
            line = f"  {segment}"
            print(line)
            log.write(line + "\n")
    log.flush()


def _print_clinical_message(prefix: str, data: bytes, log) -> bool:
    """Print only patient changes, measurements, and active alarms.

    Returns True when something was displayed. The untouched byte stream is
    saved separately regardless, so filtering terminal noise cannot lose data.
    """
    parsed = _message_summary(data)
    control_id = parsed["control_id"]

    if control_id == "103":
        patient = parsed["patient"]
        if not (patient.get("id") or patient.get("name")):
            return False
        line = (f"{prefix} PATIENT id={patient.get('id', '')!r} "
                f"name={patient.get('name', '')!r}")
        print(line)
        log.write(line + "\n")
        log.flush()
        return True

    if control_id == "204":
        # Periodic numeric parameters such as HR, RR and SpO2.
        results = parsed["results"]
        if not results:
            return False
        print(f"{prefix} VITALS")
        log.write(f"{prefix} VITALS\n")
        for result in results:
            label = result["name"] or result["code"]
            suffix = f" {result['unit']}" if result["unit"] else ""
            line = f"  {label} [{result['code']}] = {result['value']}{suffix}"
            print(line)
            log.write(line + "\n")
        log.flush()
        return True

    if control_id == "503":
        # NIBP is aperiodic: this message is emitted after a cuff measurement.
        results = [r for r in parsed["results"] if r["code"] in {"170", "171", "172", "173"}]
        if not results:
            return False
        values = {r["code"]: r["value"].strip() for r in results}
        # This uMEC12 emits -100 for Sys/Dia/Mean when no completed cuff
        # reading exists. Keep that state visible, but never present it as a
        # clinical blood-pressure value.
        pressure_values = [values.get(code) for code in ("170", "171", "172")]
        if pressure_values and all(value == "-100" for value in pressure_values if value is not None):
            line = f"{prefix} NIBP: no completed measurement"
            print(line)
            log.write(line + "\n")
            log.flush()
            return True
        print(f"{prefix} NIBP")
        log.write(f"{prefix} NIBP\n")
        for result in results:
            label = result["name"] or result["code"]
            suffix = f" {result['unit']}" if result["unit"] else ""
            line = f"  {label} [{result['code']}] = {result['value']}{suffix}"
            print(line)
            log.write(line + "\n")
        log.flush()
        return True

    if control_id in {"54", "56"}:
        # Alarm messages encode the useful alarm code/text in OBX-5. Other
        # OBXs in the same message are flags/configuration, so omit them here.
        alarms = []
        for result in parsed["results"]:
            if result["code"] not in {"2", "3", "4"} or "^" not in result["value"]:
                continue
            alarm_code, alarm_text = result["value"].split("^", 1)
            if alarm_text.strip():
                alarms.append((alarm_code, alarm_text.strip()))
        if not alarms:
            return False
        alarm_kind = "PHYSIOLOGICAL ALARM" if control_id == "54" else "TECHNICAL ALARM"
        print(f"{prefix} {alarm_kind}")
        log.write(f"{prefix} {alarm_kind}\n")
        for alarm_code, alarm_text in alarms:
            line = f"  {alarm_text} (code {alarm_code})"
            print(line)
            log.write(line + "\n")
        log.flush()
        return True

    return False


def _udp_listener(port: int, stop_event: threading.Event, discovered: dict,
                  discovered_event: threading.Event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        print(f"[udp:{port}] could not bind: {exc}")
        return
    sock.settimeout(1.0)
    print(f"[udp:{port}] listening for uMEC broadcasts")

    os.makedirs(CAPTURES_DIR, exist_ok=True)
    path = os.path.join(CAPTURES_DIR, f"umec12_udp_{port}_{datetime.now():%Y%m%d_%H%M%S}.log")
    last_data = None
    with open(path, "a", encoding="utf-8") as log:
        while not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            # Both observed channels repeat an identical announcement every
            # second. Print/log the first copy and any later change; suppress
            # exact duplicates so realtime TCP observations remain readable.
            if data == last_data:
                continue
            last_data = data

            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            if b"MSH|" in data:
                _print_message(f"[{stamp}] UDP {addr[0]}:{addr[1]} -> {port}", data, log)
                if port == 4600:
                    ip, tcp_port = _broadcast_tcp_endpoint(data, addr[0])
                    discovered["ip"] = ip
                    discovered["port"] = tcp_port
                    discovered_event.set()
            else:
                # Port 3501 is seen on this unit but is not specified as the
                # realtime-results channel in the PDS guide. Preserve it raw
                # without inventing a decoder for an undocumented payload.
                line = (f"[{stamp}] UDP {addr[0]}:{addr[1]} -> {port}: "
                        f"{len(data)} bytes hex={data.hex()}")
                print(line)
                log.write(line + "\n")
                log.flush()
    sock.close()


def _tcp_capture(monitor_ip: str, monitor_port: int, stop_event: threading.Event,
                 verbose: bool = False, parameter_ids=DEFAULT_PARAMETER_IDS):
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(CAPTURES_DIR, f"umec12_tcp_{ts}_{monitor_ip}.log")
    raw_path = os.path.join(CAPTURES_DIR, f"umec12_tcp_{ts}_{monitor_ip}.raw")

    print(f"[tcp] connecting to uMEC12 at {monitor_ip}:{monitor_port} ...")
    conn = socket.create_connection((monitor_ip, monitor_port), timeout=5)
    conn.settimeout(0.25)
    print(f"[tcp] connected to {monitor_ip}:{monitor_port}")
    print(f"[tcp] readable log -> {log_path}")
    print(f"[tcp] raw capture  -> {raw_path}")

    buffer = b""
    next_echo = time.monotonic()
    last_alarm_messages = {}
    query_resent_after_ready = False
    with open(log_path, "a", encoding="utf-8") as log, open(raw_path, "ab") as raw:
        query = _query_message(parameter_ids)
        conn.sendall(query)
        log.write(f"SENT QRY (initial) {query!r}\n")
        log.flush()
        requested = "all" if parameter_ids is None else ",".join(parameter_ids)
        print(f"[tcp] sent initial QRY for parameters {requested} and all alarms")

        try:
            while not stop_event.is_set():
                now = time.monotonic()
                if now >= next_echo:
                    conn.sendall(_echo_message())
                    next_echo = now + 1.0

                try:
                    data = conn.recv(65535)
                except socket.timeout:
                    continue
                if not data:
                    print("[tcp] monitor closed the connection")
                    break

                raw.write(data)
                raw.flush()
                buffer += data
                messages = list(hl7_mllp.iter_messages(buffer))
                if messages:
                    for message, remainder in messages:
                        buffer = remainder
                        prefix = f"[{datetime.now():%H:%M:%S.%f}] TCP"
                        if verbose:
                            _print_message(prefix, message, log)
                        parsed = _message_summary(message)
                        control_id = parsed["control_id"]
                        # Older uMEC firmware can ignore a QRY that arrives
                        # while it is still streaming the connection's initial
                        # patient/module configuration. Its first control=106
                        # echo marks the end of that initialization burst.
                        # Re-send the complete query once at that boundary;
                        # Mindray specifies that a new QRY replaces the old one,
                        # so this is safe even when the initial QRY was accepted.
                        if control_id == "106" and not query_resent_after_ready:
                            conn.sendall(query)
                            query_resent_after_ready = True
                            log.write(f"SENT QRY (after monitor ready) {query!r}\n")
                            log.flush()
                            print(f"[tcp] monitor ready; re-sent QRY for parameters {requested}")
                        if verbose:
                            continue
                        if control_id in {"54", "56"}:
                            # Alarm state is repeated every second. Display it
                            # once, then again only when the state changes.
                            if last_alarm_messages.get(control_id) == message:
                                continue
                            last_alarm_messages[control_id] = message
                        _print_clinical_message(prefix, message, log)
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-ip", default=DEFAULT_MONITOR_IP,
                        help=f"uMEC12 address (default: {DEFAULT_MONITOR_IP})")
    parser.add_argument("--monitor-port", type=int, default=DEFAULT_MONITOR_PORT,
                        help=f"Mindray PDS TCP port (default: {DEFAULT_MONITOR_PORT})")
    parser.add_argument("--discover-only", action="store_true",
                        help="decode UDP announcements without opening the PDS TCP connection")
    parser.add_argument("--verbose", action="store_true",
                        help="print every PDS configuration and heartbeat message")
    parser.add_argument("--parameters", default=",".join(DEFAULT_PARAMETER_IDS),
                        help="comma-separated Mindray parameter IDs to request explicitly")
    parser.add_argument("--all-parameters", action="store_true",
                        help="use Mindray's send-all query instead of explicit parameter IDs")
    args = parser.parse_args()

    stop_event = threading.Event()
    discovered_event = threading.Event()
    discovered = {}
    threads = []
    for udp_port in UDP_PORTS:
        thread = threading.Thread(
            target=_udp_listener,
            args=(udp_port, stop_event, discovered, discovered_event),
            name=f"umec-udp-{udp_port}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    try:
        if args.discover_only:
            print("[umec12] discovery-only mode; press Ctrl+C to stop")
            while True:
                time.sleep(1)
        else:
            # A fixed IP is known for this unit, so connect immediately. UDP
            # decoding remains active alongside TCP and records the advertised
            # endpoint for comparison.
            parameter_ids = None if args.all_parameters else [
                value.strip() for value in args.parameters.split(",") if value.strip()
            ]
            _tcp_capture(args.monitor_ip, args.monitor_port, stop_event,
                         verbose=args.verbose, parameter_ids=parameter_ids)
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError) as exc:
        print(f"[tcp] connection failed: {exc}")
        print("[tcp] The uMEC12 must expose Mindray PDS Realtime Results on TCP 4601.")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
