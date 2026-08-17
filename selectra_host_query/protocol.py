"""Small, self-contained ASTM/LIS2-A codec for the Selectra test bench.

This module intentionally does not import the production Labo Bridge.  The
test bench must remain usable without its database, result writer, or runtime
listeners.  Order-record field placement must still be validated against the
exact Selectra host-interface document before clinical use.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


# Real test-name -> abbreviated-code mapping decoded from this Selectra's own
# M (installed-method list) records.  Host-to-analyser O-5 requires these
# case-sensitive abbreviations (maximum four characters), not display names.
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
    # Alternate labels already exposed by the staging page.
    "Phosphatase Alc": "PAL",
    "Calcium": "CAL",
    "CALCUIM": "CA2+",
    "CRP IP V3": "CRP3",
    "CK-NAC": "CPK",
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
    """Build one ASTM frame containing one complete LIS2-A message."""
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


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def test_abbreviation(value: str) -> str:
    """Return this analyser's installed, case-sensitive test abbreviation."""
    clean = _clean(value)
    if clean in KNOWN_SHORT_CODES:
        return KNOWN_SHORT_CODES[clean]
    if clean in set(KNOWN_SHORT_CODES.values()) and len(clean) <= 4:
        return clean
    raise ValueError(f"unknown Selectra order test {clean!r}; use an installed method")


def build_order_records(order: dict, api_outbound_fields=None) -> list[str]:
    """Build one protocol-aligned host response to a Selectra Q record.

    Direction matters: fields observed in analyser result uploads are not
    copied blindly into a host order.  H-5 identifies the configured host
    (WINLAB), H-10 identifies this device (PROM), O-5 contains installed test
    abbreviations, O-26 marks a query response, and L terminates the message.
    The caller must place all returned records in ONE ASTM frame.
    """
    sample_id = _clean(order["sample_id"])
    if not sample_id:
        raise ValueError("Selectra sample ID is required")
    minimal_api_order = order.get("source") == "api"
    enabled_api_fields = set(api_outbound_fields or ())
    preserve_demographics = bool(order.get("preserve_analyser_demographics"))
    family_name = _clean(order.get("family_name"))
    given_name = _clean(order.get("given_name"))
    patient_name = " ".join(part for part in (family_name, given_name) if part)[:20]
    birth_date = _clean(order.get("birth_date")).replace("-", "")
    sex = _clean(order.get("sex") or "M").upper()
    if sex not in {"M", "F", "U"}:
        sex = "M"
    tests = [test_abbreviation(code) for code in order.get("tests") or []]
    if not tests:
        raise ValueError("at least one installed Selectra test is required")
    universal_tests = "\\".join(f"^^^{code}" for code in dict.fromkeys(tests))

    order_fields = [""] * 26
    order_fields[0] = "O"
    order_fields[1] = "1"
    order_fields[2] = sample_id
    order_fields[4] = universal_tests
    order_fields[5] = "R"
    action_code = _clean(order.get("action_code") or "N").upper()
    if action_code not in {"N", "A", "C"}:
        raise ValueError("Selectra action code must be N, A, or C")
    order_fields[11] = action_code
    specimen_type = _clean(order.get("outbound_specimen_type"))
    if specimen_type and (not minimal_api_order or "specimen_type" in enabled_api_fields):
        order_fields[15] = specimen_type
    ordering_physician = _clean(order.get("ordering_physician"))[:20]
    if ordering_physician and (not minimal_api_order or "ordering_physician" in enabled_api_fields):
        order_fields[16] = ordering_physician
    order_fields[25] = "Q"

    # The analyser rejects an order when demographics conflict with a request
    # already present under the same sample ID. A wildcard response cannot
    # know those demographics, so it must send the required minimal P record
    # rather than inventing a patient name, birth date, or sex.
    if preserve_demographics:
        patient_record = "P|1"
    elif not minimal_api_order:
        patient_record = f"P|1||||{patient_name}||{birth_date}|{sex}"
    elif "sex" in enabled_api_fields:
        patient_record = (
            f"P|1||||{patient_name}||"
            f"{birth_date if 'birth_date' in enabled_api_fields else ''}|{sex}"
        )
    elif "birth_date" in enabled_api_fields:
        patient_record = f"P|1||||{patient_name}||{birth_date}"
    else:
        patient_record = f"P|1||||{patient_name}"

    records = [
        f"H|\\^&|||WINLAB|||||PROM||P|LIS2-A|{_stamp()}",
        patient_record,
        "|".join(order_fields),
    ]
    comment = (
        _clean(order.get("comment"))[:100]
        if not minimal_api_order or "comment" in enabled_api_fields else ""
    )
    if comment:
        # A C record immediately following O is copied into the Selectra's
        # sample Comment field. The instrument stores at most 100 characters.
        records.append(f"C|1||{comment}")
    records.append("L|1|F")
    return records


def application_rejections(records: list[str]) -> list[dict[str, str]]:
    """Extract Selectra O records that reject a host order (O-26 = X)."""
    rejected = []
    patient_name = ""
    for record in records:
        fields = record.split("|")
        record_type = fields[0].lstrip("01234567") if fields else ""
        if record_type == "P":
            patient_name = fields[5].strip() if len(fields) > 5 else ""
        elif record_type == "O" and len(fields) > 25 and fields[25].strip() == "X":
            rejected.append({
                "sample_id": fields[2].strip() if len(fields) > 2 else "",
                "patient_name": patient_name,
                "record": record,
            })
    return rejected


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
