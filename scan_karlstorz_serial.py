#!/usr/bin/env python3
"""
Serial brute-force sweep for Karl Storz's DATA/SERIAL RJ-45 ports (or any
other RS-232 device with no known baud rate) - the "listen everywhere" tool
for when there's no documented protocol to point a normal listener at.

WHY THIS EXISTS, AND WHAT IT CANNOT FIX
========================================
This tool solves exactly ONE unknown: which BAUD RATE the device is talking
at, once the physical wiring is already correct. It sweeps a list of common
baud rates on every detected serial port and saves everything it hears.

It does NOT and CANNOT solve a SECOND, separate unknown: which physical pins
on Karl Storz's RJ-45 DATA/SERIAL jacks carry TX/RX/GND. There is no public
Karl Storz pinout (confirmed - see project memory; this is also where the
earlier "Port 28" / "DB9 cable" claims were debunked against the real
hardware photos). If a USB-to-RS232 adapter is wired to the wrong pins,
sweeping every baud rate that exists will still hear nothing, because
nothing is actually connected in a way that carries signal. If EVERY baud on
EVERY port comes back completely silent, that points at the wiring, not the
baud rate - go solve the pinout problem before running this again.

WHAT IT DOES
============
1. Auto-detects every serial port currently connected to this machine (or
   accepts explicit device paths as arguments).
2. Sweeps BAUD_RATES (below) at 8N1 on each port - one thread per port, so
   multiple adapters (e.g. one wired to DATA, one to SERIAL) get swept at
   the same time instead of doubling the total wait.
3. For EVERY (port, baud) combination, whatever came in during the window is
   saved unconditionally to captures/karlstorz_<port>_<baud>_<ts>.raw -
   including a genuinely empty file if nothing arrived (confirmed silence at
   that baud is still a result worth keeping, same principle as
   capture_listener.py/capture_wato.py's raw-first guarantee).
4. Flags combinations that look like real data (high printable-ASCII ratio)
   as a POSSIBLE HIT worth checking by hand - a heuristic, not proof: a
   binary protocol could be real data with a low printable ratio, and random
   line noise could occasionally look printable by chance.

HOW TO USE IT
==============
Wire a USB-to-RS232 adapter to Karl Storz's DATA or SERIAL port (needs the
correct RJ-45 pins for TX/RX/GND - not yet confirmed, see above), plug it
into this Mac, then:

    python scan_karlstorz_serial.py

One full sweep (8 baud rates x --window seconds each) takes a few minutes.
GO PRESS WHATEVER BUTTON YOU'RE TESTING REPEATEDLY throughout the whole
sweep, not just once - there's no way to know in advance which baud window
will be active at the moment you press it.

    python scan_karlstorz_serial.py /dev/cu.usbserial-XXXX   # target one port
    python scan_karlstorz_serial.py --window 20              # longer per-baud window
    python scan_karlstorz_serial.py --loop                   # repeat until Ctrl+C
"""

import argparse
import os
import sys
import threading
import time
from datetime import datetime

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    print("pyserial is required: pip install pyserial  (or: pip install -r requirements.txt)")
    sys.exit(1)

CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")

# Common RS-232 rates across medical/industrial serial gear, low to high -
# includes 115200 since that's the CONFIRMED default on the Mindray WATO's
# own serial HL7 config (different manufacturer, but a real medical-device
# data point, not a random guess). 8N1 (8 data bits, no parity, 1 stop bit)
# is the overwhelmingly common default and is what this sweep uses; if a
# full 8N1 sweep finds nothing on a port that IS correctly wired, parity/
# stop-bit variations would be the next thing to try (not swept here, to
# keep this first pass small enough to actually sit through once).
BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

DEFAULT_WINDOW_SECONDS = 12
PRINTABLE_HIT_THRESHOLD = 0.70  # fraction of bytes that must be printable ASCII to flag as a hit


def _readable(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 or b in (9, 10, 13) else f"\\x{b:02x}" for b in data)


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(data)


def _safe_port_tag(port: str) -> str:
    return port.replace("/", "_").replace("\\", "_").replace(":", "_")


def _sweep_one_port(port: str, window_seconds: float, results: list, results_lock: threading.Lock):
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    port_tag = _safe_port_tag(port)
    print(f"[{port}] starting sweep: {len(BAUD_RATES)} baud rates x {window_seconds}s each "
          f"(~{len(BAUD_RATES) * window_seconds:.0f}s total)")

    for baud in BAUD_RATES:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_path = os.path.join(CAPTURES_DIR, f"karlstorz_{port_tag}_{baud}_{ts}.raw")
        print(f"[{port}] listening at {baud} baud 8N1 for {window_seconds}s - "
              f"PRESS THE BUTTON NOW if you haven't already this round...")

        collected = b""
        error = None
        try:
            with serial.Serial(port, baudrate=baud, bytesize=serial.EIGHTBITS,
                                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                timeout=0.5) as ser:
                deadline = time.monotonic() + window_seconds
                while time.monotonic() < deadline:
                    chunk = ser.read(4096)
                    if chunk:
                        collected += chunk
        except serial.SerialException as e:
            error = str(e)
            print(f"[{port}] error at {baud} baud: {e}")

        # Always save whatever was collected, even on a mid-sweep error or
        # zero bytes - confirmed silence, or a partial capture before a
        # disconnect, are both results worth keeping.
        with open(raw_path, "wb") as f:
            f.write(collected)

        ratio = _printable_ratio(collected)
        is_hit = len(collected) > 0 and ratio >= PRINTABLE_HIT_THRESHOLD
        with results_lock:
            results.append({"port": port, "baud": baud, "bytes": len(collected),
                            "ratio": ratio, "error": error, "raw_path": raw_path,
                            "hit": is_hit})

        if error and not collected:
            continue

        if collected:
            tag = ("*** POSSIBLE HIT ***" if is_hit else
                   "(received, but low printable ratio - noise, wrong baud, or a binary protocol)")
            preview = collected[:200]
            print(f"[{port}] {baud} baud: {len(collected)} bytes {tag}\n"
                  f"[{port}]   txt: {_readable(preview)}{'...' if len(collected) > 200 else ''}\n"
                  f"[{port}]   saved -> {raw_path}")
        else:
            print(f"[{port}] {baud} baud: 0 bytes (silence) -> {raw_path}")

    print(f"[{port}] sweep of this port complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ports", nargs="*", help="explicit serial device path(s); "
                        "auto-detected if omitted")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_SECONDS,
                        help=f"seconds to listen per baud rate (default {DEFAULT_WINDOW_SECONDS})")
    parser.add_argument("--loop", action="store_true",
                        help="repeat the full sweep until Ctrl+C instead of running once")
    args = parser.parse_args()

    ports = args.ports or [p.device for p in list_ports.comports()]
    if not ports:
        print("No serial ports detected.\n\n"
              "This means either no USB-to-RS232 adapter is plugged into this Mac, or "
              "the OS hasn't enumerated it as a serial device. Plug in an adapter wired "
              "to Karl Storz's DATA or SERIAL port and re-run this script - if it still "
              "finds nothing, check `ls /dev/cu.*` to confirm the adapter itself is "
              "recognized before suspecting this script.")
        sys.exit(1)

    print(f"Detected {len(ports)} serial port(s): {', '.join(ports)}")
    print(f"Sweeping {len(BAUD_RATES)} baud rates per port: {BAUD_RATES}")
    print(f"Captures saved under {CAPTURES_DIR}/\n")
    print("REMINDER: this only helps if the physical wiring/pinout is already correct.")
    print("If every baud on every port comes back silent, that's a wiring problem, not")
    print("a baud-rate problem - see this file's docstring.\n")

    round_num = 0
    try:
        while True:
            round_num += 1
            if args.loop:
                print(f"=== sweep round {round_num} ===\n")
            results = []
            results_lock = threading.Lock()
            threads = [threading.Thread(target=_sweep_one_port,
                                        args=(port, args.window, results, results_lock),
                                        name=f"sweep-{port}")
                      for port in ports]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            hits = [r for r in results if r.get("hit")]
            print("\n=== sweep summary ===")
            for r in sorted(results, key=lambda r: (r["port"], r["baud"])):
                if r.get("error") and not r["bytes"]:
                    print(f"  {r['port']} @ {r['baud']}: ERROR - {r['error']}")
                else:
                    flag = " <-- POSSIBLE HIT" if r.get("hit") else ""
                    print(f"  {r['port']} @ {r['baud']}: {r['bytes']} bytes, "
                          f"{r['ratio']*100:.0f}% printable{flag}")
            if hits:
                print(f"\n{len(hits)} possible hit(s) - go inspect those .raw files first.")
            else:
                print("\nNo likely hits this round. If this was total silence across every "
                      "baud and every port, suspect the wiring/pinout, not the baud rate.")

            if not args.loop:
                break
            print("\nStarting next round (Ctrl+C to stop)...\n")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
