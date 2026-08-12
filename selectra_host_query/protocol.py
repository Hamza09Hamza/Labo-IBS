"""Small, self-contained ASTM/LIS2-A codec for the Selectra test bench.

This module intentionally does not import the production Labo Bridge.  The
test bench must remain usable without its database, result writer, or runtime
listeners.  Order-record field placement must still be validated against the
exact Selectra host-interface document before clinical use.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


# Real test-name -> short-code mapping, decoded from an actual M (method
# master list) record this Selectra sent on its own during a live capture
# (2026-08-12), e.g. "26^SGOT^SGOT^1^^^19006" -> short code "SGOT" for full
# name "SGOT", "28^ALP^Phosphatase ALP^1^^^78" -> short code "ALP" for full
# name "Phosphatase ALP". Four real host-query order attempts (samples
# 2003-2006) using ONLY the full name in the O record's universal-test-ID
# field were all ACKed at the ASTM frame level but produced zero visible
# effect on the Selectra's screen. Generic LIS2-A/Vitalab documentation
# (unverified, low-medium confidence - see selectra_host_query/README.md)
# suggests this field's expected shape is "^^^<short_code>^<full_name>",
# not the full name alone - this map supplies the short-code half from
# real evidence, not a guess. Only entries actually observed on THIS
# instrument's own M record are here; any other test code falls back to
# sending the full name alone (old behavior) since no confirmed short
# code exists for it yet.
KNOWN_SHORT_CODES = {
    "Glucose pap sl": "Gly",
    "Uree uv sl": "Uree",
    "Creatinine": "Crea",
    "Acide Urique": "AU",
    "Cholesterol": "Chol",
    "Triglycerides": "Trig",
    "Cholesterol HDL": "HDL",
    "Albumine": "Alb",
    "CK NAK": "CPK",
    "CRP IP v3": "CRP3",
    "CAL elitech": "cal",
    "Phosphore": "Phos",
    "BILI TOTAL BIO": "BILT",
    "BILI DIRECT BIO": "BILD",
    "LDH-L SL": "LDHL",
    "Proteine totale": "Prot",
    "SGOT": "SGOT",
    "SGPT": "SGPT",
    "Phosphatase ALP": "ALP",
    "GGT": "G GT",
    "Proteines U": "PrtU",
}

ENQ = 0x05
ACK = 0x06
NAK = 0x15
STX = 0x02
ETX = 0x03
ETB = 0x17
EOT = 0x04
CR = 0x0D
LF = 0x0A

B_ENQ = bytes([ENQ])
B_ACK = bytes([ACK])
B_NAK = bytes([NAK])
B_EOT = bytes([EOT])

CONTROL_NAMES = {ENQ: "ENQ", ACK: "ACK", NAK: "NAK", STX: "STX", ETX: "ETX", ETB: "ETB", EOT: "EOT"}
_QUERY_SPLIT = re.compile(r"[\^&\\]")


def checksum(payload_through_terminator: bytes) -> bytes:
    return f"{sum(payload_through_terminator) & 0xFF:02X}".encode("ascii")


def build_frame(frame_number: int, text: str, final: bool = True) -> bytes:
    """Build one ASTM frame with a single LIS2-A record."""
    terminator = ETX if final else ETB
    body = f"{frame_number % 8}{text}\r".encode("ascii", errors="strict")
    checked = body + bytes([terminator])
    return bytes([STX]) + checked + checksum(checked) + bytes([CR, LF])


def decode_frame(frame: bytes) -> str:
    if len(frame) < 8 or frame[0] != STX or frame[-2:] != bytes([CR, LF]):
        raise ValueError("incomplete ASTM frame")
    terminator_index = max(frame.rfind(bytes([ETX])), frame.rfind(bytes([ETB])))
    if terminator_index < 0:
        raise ValueError("ASTM frame has no ETX/ETB terminator")
    expected = frame[terminator_index + 1:terminator_index + 3].upper()
    actual = checksum(frame[1:terminator_index + 1])
    if expected != actual:
        raise ValueError(f"ASTM checksum mismatch: expected {expected!r}, calculated {actual!r}")
    return frame[2:terminator_index].decode("ascii", errors="replace").rstrip("\r")


def split_records(payload: str) -> list[str]:
    return [record for record in payload.split("\r") if record.strip()]


def query_candidates(record: str) -> list[str]:
    """Extract strict specimen-ID candidates from a LIS2-A Q record.

    Q-2 commonly contains a component expression such as ``^SAMPLE123^``.
    The store performs an exact match against these candidates; the bench
    never uses a substring or fuzzy patient match to select an order.
    """
    fields = record.split("|")
    if not fields or fields[0].lstrip("01234567") != "Q":
        return []
    query_field = fields[2].strip() if len(fields) > 2 else ""
    values = [part.strip() for part in _QUERY_SPLIT.split(query_field) if part.strip()]
    if query_field and query_field not in values:
        values.append(query_field)
    return list(dict.fromkeys(values))


def _clean(value: str) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _universal_test_id(full_name: str) -> str:
    """Build one O-record universal-test-ID component for a requested test.

    Uses this Selectra's own confirmed short code when known (see
    KNOWN_SHORT_CODES); falls back to the full name alone for anything not
    yet observed on this instrument's own M record, rather than guessing a
    short code that was never confirmed.
    """
    short_code = KNOWN_SHORT_CODES.get(full_name)
    if short_code:
        return f"^^^{short_code}^{full_name}"
    return f"^^^{full_name}"


def build_order_records(order: dict) -> list[str]:
    """Build a conservative LIS2-A H/P/O/L order-download transaction."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    sample_id = _clean(order["sample_id"])
    patient_id = _clean(order.get("patient_id") or sample_id)
    family_name = _clean(order.get("family_name"))
    given_name = _clean(order.get("given_name"))
    birth_date = _clean(order.get("birth_date")).replace("-", "")
    sex = _clean(order.get("sex") or "U").upper()
    specimen_type = _clean(order.get("specimen_type") or "SERUM")
    tests = [_clean(code) for code in order.get("tests") or [] if _clean(code)]
    universal_tests = "\\".join(_universal_test_id(code) for code in tests)
    # O record field [5] (priority) is populated "R" (Routine) in every real
    # O record this Selectra has been observed to send in its own R/result
    # captures (e.g. "O|1|339|||R||||||||||Normal||||...") - confirmed via
    # real capture, 2026-08-12. Leaving it blank (as before) is a real
    # mismatch from what the machine's own usage of this record shape looks
    # like, unlike patient_id in the P record below, which is correctly
    # empty-by-default and only non-empty here because of what was typed
    # into the web form's Patient ID field during testing.
    #
    # H record field [9] (receiving application) confirmed via real capture
    # (2026-08-12) to be "WINLAB" in every H record this Selectra sends on
    # its own uploads - e.g. "H|\^&|||PROM^4.3.13||||1.5|WINLAB||P|LIS2-A|...".
    # Two real orders (samples 2003, 2004) were ACKed frame-by-frame at the
    # ASTM level but produced zero visible reaction on the Selectra's own
    # screen when this field said "SELECTRA" instead - consistent with the
    # analyzer validating the receiving-application name and silently
    # discarding anything not addressed to its own configured host name
    # before the content ever reaches the worklist.
    return [
        f"H|\\^&|||LABO-BRIDGE-HQ|||||WINLAB||P|LIS2-A|{stamp}",
        f"P|1||{patient_id}||{family_name}^{given_name}||{birth_date}|{sex}",
        f"O|1|{sample_id}||{universal_tests}|R||||||N||||{specimen_type}||||||||||O",
        "L|1|N",
    ]


def visible_bytes(data: bytes) -> str:
    """Render transport controls without leaking unreadable terminal bytes."""
    pieces = []
    for byte in data:
        if byte in CONTROL_NAMES:
            pieces.append(f"<{CONTROL_NAMES[byte]}>")
        elif byte == CR:
            pieces.append("<CR>")
        elif byte == LF:
            pieces.append("<LF>")
        elif 32 <= byte < 127:
            pieces.append(chr(byte))
        else:
            pieces.append(f"<0x{byte:02X}>")
    return "".join(pieces)
