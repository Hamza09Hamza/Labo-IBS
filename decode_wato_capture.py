#!/usr/bin/env python3
"""
Read a capture_listener.py capture file for the WATO EX-35 and print every
HL7 message it contains as human-readable results (see
labo_bridge/decoders/wato_ex35.py).

This is a standalone reporting tool, same spirit as capture_listener.py
itself - it does NOT touch Postgres, matcher.py, or the clinic API. Local
captures in, readable text out, meant to be shared with a coworker to show
what the machine actually sends.

Usage:
    python decode_wato_capture.py captures/wato_ex35_20260806_192458_192.168.23.250.log
"""

import re
import sys

from labo_bridge.decoders import wato_ex35
from labo_bridge.protocols import hl7_mllp

# Matches one decoded MLLP message block as capture_listener.py writes it:
# "---- MLLP message #N decoded (M segments) ----" followed by indented
# "    SEGMENT|..." lines, up to "---- end message ----".
MESSAGE_BLOCK_RE = re.compile(
    r"---- MLLP message #\d+ decoded.*?----\n(.*?)\n\s*---- end message ----",
    re.DOTALL,
)


def extract_messages(capture_text: str) -> list:
    """Return a list of segment-lists, one per MLLP message found in the
    capture file's already-decoded blocks (capture_listener.py writes these
    itself at capture time - reusing them here instead of re-parsing hex,
    since they're already known-correct)."""
    messages = []
    for block in MESSAGE_BLOCK_RE.findall(capture_text):
        segments = [line.strip() for line in block.splitlines() if line.strip()]
        if segments:
            messages.append(segments)
    return messages


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <capture-file.log>")
        raise SystemExit(1)

    with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
        capture_text = f.read()

    messages = extract_messages(capture_text)
    if not messages:
        print("No decoded MLLP messages found in this capture file.")
        return

    print(f"{len(messages)} message(s) found in {sys.argv[1]}\n")
    for segments in messages:
        decoded = wato_ex35.decode_message(segments)
        print(wato_ex35.readable_summary(decoded))
        print()


if __name__ == "__main__":
    main()
