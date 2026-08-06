#!/usr/bin/env python3
"""
Standalone raw-capture listener - NOT wired into the labo_bridge pipeline.

Use this to find out exactly what a new machine actually sends BEFORE
writing a real decoder for it - the same first step already used for
xs500i and the Mini VIDAS (capture real bytes, then build the decoder from
that evidence - see labo_bridge/decoders/minividas.py's docstring). There is
no ASTM handshake, no matcher, no Postgres, no clinic-API call anywhere in
this file - it only binds a port, logs every byte it receives (raw hex +
readable text, flushed to disk immediately, not buffered until the
connection closes), and additionally auto-decodes anything that turns out
to be HL7/MLLP framed.

Why MLLP gets special treatment: the Mindray WATO EX-35's Ethernet HL7 mode
uses plain MLLP (<VT> message <FS> <CR>) per its manual, identical to
CyanVision's - labo_bridge/protocols/hl7_mllp.py already parses that
correctly, so it's reused here read-only (no other part of the package is
imported - this script does not touch matcher.py/pg.py/api_client.py at
all). When a complete MLLP message is found, this also sends back a generic
HL7 ACK, because HL7 senders that don't get one typically stall/resend
instead of continuing - without this, a real capture session could look
like "it only sent one message" when actually it sent one and then waited
forever for an ACK that never came.

The WATO's SERIAL mode (RS-232, via a serial-to-Ethernet adapter) inserts a
4-character CRC between the message and the closing <FS> - this parser does
NOT strip that, so in serial captures the last segment will show a few
extra trailing characters. Harmless for discovery purposes (the point here
is "see the real bytes"); a real decoder would need to strip it once
confirmed.

Karl Storz (or anything else that isn't MLLP-framed at all) is still
captured byte-for-byte - it just never enters the MLLP branch, and nothing
is ever sent back to it unsolicited (safest default for a device whose
protocol isn't documented).

Usage:
    python capture_listener.py                        # wato_ex35 on 6010
    python capture_listener.py wato_ex35:6010
    python capture_listener.py wato_ex35:6010 karlstorz:6011

Ctrl+C stops every listener. Every connection's bytes land in
captures/<name>_<timestamp>_<peer-ip>.log as they arrive, so nothing is
lost even if the connection never closes cleanly.
"""

import os
import socket
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labo_bridge.protocols import hl7_mllp  # noqa: E402 - pure framing helper, no side effects
from labo_bridge.decoders import wato_ex35  # noqa: E402 - only used for the wato_ex35 target below

HOST = "0.0.0.0"
CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
IDLE_TIMEOUT_SECONDS = 90  # how long a silent-but-still-open connection waits before we say so

DEFAULT_TARGETS = {"wato_ex35": 6010}


def _readable(data: bytes) -> str:
    """Same convention as server.py's _write_session_file: printable ASCII
    as itself, everything else (control bytes, non-UTF8) as \\xNN - nothing
    is ever hidden from the dump."""
    return "".join(chr(b) if 32 <= b < 127 or b in (9, 10, 13) else f"\\x{b:02x}" for b in data)


def _parse_targets(argv):
    if not argv:
        return dict(DEFAULT_TARGETS)
    targets = {}
    for arg in argv:
        if ":" not in arg:
            raise SystemExit(f"bad target {arg!r}, expected name:port (e.g. wato_ex35:6010)")
        name, port_s = arg.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            raise SystemExit(f"bad port in {arg!r}: {port_s!r} is not a number")
        targets[name] = port
    return targets


def _handle_connection(conn, addr, name):
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(CAPTURES_DIR, f"{name}_{ts}_{addr[0]}.log")
    print(f"[{name}] connected by {addr[0]}:{addr[1]} -> logging to {log_path}")

    log = open(log_path, "a", encoding="utf-8")

    def _log(text):
        log.write(text)
        log.flush()

    # Live-decoded readable output, alongside the raw capture - only for
    # wato_ex35 (the one target with a real decoder, see
    # labo_bridge/decoders/wato_ex35.py); other targets (e.g. karlstorz)
    # have no decoder yet, so this file simply isn't created for them.
    decoded_path = None
    decoded_file = None
    if name == "wato_ex35":
        decoded_path = os.path.join(CAPTURES_DIR, f"{name}_{ts}_{addr[0]}_decoded.txt")
        decoded_file = open(decoded_path, "a", encoding="utf-8")
        print(f"[{name}] readable decoded results -> {decoded_path}")

    def _log_decoded(text):
        if decoded_file:
            decoded_file.write(text)
            decoded_file.flush()

    _log(f"=== capture started {datetime.now().isoformat()} from {addr[0]}:{addr[1]} ===\n")

    buffer = b""
    total_bytes = 0
    mllp_count = 0
    conn.settimeout(IDLE_TIMEOUT_SECONDS)
    try:
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                print(f"[{name}] no data for {IDLE_TIMEOUT_SECONDS}s, connection still open, waiting...")
                continue
            except ConnectionResetError:
                break
            if not data:
                break

            total_bytes += len(data)
            buffer += data
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            chunk_block = (f"[{stamp}] RECV {len(data)} bytes\n"
                            f"  hex: {data.hex()}\n"
                            f"  txt: {_readable(data)}\n")
            print(f"[{name}] {chunk_block}", end="")
            _log(chunk_block)

            # Peel off any complete HL7/MLLP messages now sitting in the
            # buffer (VT...FS CR framing - see module docstring). A single
            # recv() can contain more than one queued message, so this
            # mirrors server.py's _handle_hl7 loop exactly: no early break,
            # keep draining until iter_messages has nothing left to yield.
            for message, remainder in hl7_mllp.iter_messages(buffer):
                buffer = remainder
                mllp_count += 1
                segments = hl7_mllp.split_segments(message)
                block_lines = [f"  ---- MLLP message #{mllp_count} decoded "
                               f"({len(segments)} segments) ----"]
                block_lines += [f"    {seg}" for seg in segments]
                block_lines.append("  ---- end message ----")
                block = "\n".join(block_lines) + "\n"
                print(f"[{name}]\n{block}", end="")
                _log(block)

                if name == "wato_ex35":
                    decoded_message = wato_ex35.decode_message(segments)
                    _log_decoded(wato_ex35.readable_summary(decoded_message) + "\n\n")

                control_id = "0"
                for seg in segments:
                    fields = seg.split("|")
                    if fields and fields[0] == "MSH" and len(fields) > 9:
                        control_id = fields[9]
                        break
                try:
                    conn.sendall(hl7_mllp.build_ack(control_id))
                    print(f"[{name}] << sent HL7 ACK (control_id={control_id!r})")
                    _log(f"  << sent HL7 ACK (control_id={control_id!r})\n")
                except OSError as e:
                    print(f"[{name}] failed to send ACK: {e}")
    finally:
        _log(f"=== capture ended {datetime.now().isoformat()} "
             f"({total_bytes} bytes total, {mllp_count} MLLP message(s) decoded) ===\n")
        log.close()
        if decoded_file:
            decoded_file.close()
            print(f"[{name}] readable decoded results saved to {decoded_path}")
        conn.close()
        print(f"[{name}] disconnected {addr[0]}:{addr[1]} - {total_bytes} bytes, "
              f"{mllp_count} MLLP message(s). Full capture saved to {log_path}")


def _serve(name, port, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
    except OSError as e:
        print(f"[{name}] could not bind {HOST}:{port}: {e}")
        return
    sock.listen(5)
    sock.settimeout(1.0)  # so Ctrl+C (stop_event) is noticed promptly, not blocked in accept()
    print(f"[{name}] listening on {HOST}:{port} (raw capture, MLLP auto-decode if applicable)")

    try:
        while not stop_event.is_set():
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            try:
                _handle_connection(conn, addr, name)
            except Exception as e:
                print(f"[{name}] error handling connection from {addr}: {e}")
            print(f"[{name}] ready for next connection.")
    finally:
        sock.close()


def main():
    targets = _parse_targets(sys.argv[1:])
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    print(f"Raw capture listener - captures saved under {CAPTURES_DIR}/")
    print(f"Targets: {', '.join(f'{n}={p}' for n, p in targets.items())}")
    print("This does NOT touch Postgres, the clinic API, or any matcher/mapping "
          "logic - it only captures and (for HL7/MLLP) decodes for viewing.\n")

    stop_event = threading.Event()
    threads = []
    for name, port in targets.items():
        t = threading.Thread(target=_serve, args=(name, port, stop_event),
                             name=f"capture-{name}", daemon=True)
        t.start()
        threads.append(t)

    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            for t in threads:
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
        stop_event.set()
        for t in threads:
            t.join(timeout=2)
        print("Stopped.")


if __name__ == "__main__":
    main()
