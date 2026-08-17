"""Sysmex XN-L ASTM Host Query records and E1381 transport framing.

The XN-330 belongs to the XN-L interface family.  In ASTM mode it sends an
H/Q/L inquiry and expects an H/P/O/L response.  The O-3 specimen selector is
echoed exactly from Q-3; the human sample ID used for staging is the third
component of that selector.
"""

from __future__ import annotations

from datetime import datetime, timezone

from labo_bridge.protocols import astm


CONTROL_NAMES = {
    astm.ENQ: "ENQ", astm.ACK: "ACK", astm.NAK: "NAK",
    astm.STX: "STX", astm.ETX: "ETX", astm.ETB: "ETB", astm.EOT: "EOT",
}

# Parameters documented as the XN-L CBC and six-part differential profiles.
CBC_TESTS = (
    "WBC", "RBC", "HGB", "HCT", "MCV", "MCH", "MCHC", "PLT",
    "RDW-SD", "RDW-CV", "MPV",
)
DIFF_TESTS = (
    "NEUT#", "LYMPH#", "MONO#", "EO#", "BASO#",
    "NEUT%", "LYMPH%", "MONO%", "EO%", "BASO%", "IG#", "IG%",
)
ORDERABLE_TESTS = CBC_TESTS + DIFF_TESTS
TEST_SET = frozenset(ORDERABLE_TESTS)


def _clean(value: object) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def query_details(record: str) -> dict[str, str] | None:
    """Return the exact Q-3 selector and its staged sample-ID component."""
    fields = record.split("|")
    if not fields or fields[0].lstrip("01234567") != "Q":
        return None
    selector = fields[2] if len(fields) > 2 else ""
    components = selector.split("^")
    # XN-L ASTM Q-3 is ``rack^tube^sample ID^mode``.  Manual queries still
    # use the same composite, with blank rack/tube components.
    sample_id = components[2].strip() if len(components) >= 3 else selector.strip()
    if not sample_id:
        return None
    return {"selector": selector, "sample_id": sample_id}


def validate_tests(values) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("XN-330 tests must be a non-empty JSON array")
    tests = []
    for value in values:
        code = _clean(value).upper()
        if code not in TEST_SET:
            raise ValueError(f"unknown XN-330 order test {code!r}")
        if code not in tests:
            tests.append(code)
    return tests


def build_order_records(order: dict, query_selector: str | None = None) -> list[str]:
    sample_id = _clean(order.get("sample_id"))
    if not sample_id:
        raise ValueError("XN-330 sample ID is required")
    tests = validate_tests(order.get("tests"))
    selector = query_selector if query_selector is not None else f"^^{sample_id}^M"

    patient_fields = [""] * 27
    patient_fields[0] = "P"
    patient_fields[1] = "1"
    patient_fields[4] = _clean(order.get("patient_id"))[:16]
    # XN ASTM P-6: ^First name^Last name, maximum 20 bytes each.
    patient_fields[5] = (
        f"^{_clean(order.get('given_name'))[:20]}^{_clean(order.get('family_name'))[:20]}"
    )
    patient_fields[7] = _clean(order.get("birth_date")).replace("-", "")
    sex = _clean(order.get("sex") or "U").upper()
    patient_fields[8] = sex if sex in {"M", "F", "U"} else "U"

    order_fields = [""] * 26
    order_fields[0] = "O"
    order_fields[1] = "1"
    order_fields[2] = selector
    order_fields[4] = "\\".join(f"^^^^{code}" for code in tests)
    order_fields[6] = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    order_fields[11] = "N"  # normal sample analysis
    order_fields[25] = "Q"  # matching order exists for this inquiry

    return [
        "H|\\^&|||||||||||E1394-97",
        "|".join(patient_fields).rstrip("|"),
        "|".join(order_fields),
        "L|1|N",
    ]


def _checksum(payload: bytes) -> bytes:
    return f"{sum(payload) & 0xFF:02X}".encode("ascii")


def build_frame(frame_number: int, text: str, final: bool) -> bytes:
    terminator = astm.ETX if final else astm.ETB
    body = f"{frame_number % 8}{text}".encode("ascii", errors="strict")
    checked = body + bytes([terminator])
    return bytes([astm.STX]) + checked + _checksum(checked) + bytes([astm.CR, astm.LF])


def build_message_frames(records: list[str], max_text_bytes: int = 220) -> list[bytes]:
    """Split one ASTM message across E1381 frames without exceeding 240 bytes."""
    message = "\r".join(records) + "\r"
    encoded = message.encode("ascii", errors="strict")
    chunks = [encoded[index:index + max_text_bytes] for index in range(0, len(encoded), max_text_bytes)]
    return [
        build_frame(index + 1, chunk.decode("ascii"), index == len(chunks) - 1)
        for index, chunk in enumerate(chunks)
    ]


def visible_bytes(data: bytes) -> str:
    parts = []
    for byte in data:
        if byte in CONTROL_NAMES:
            parts.append(f"<{CONTROL_NAMES[byte]}>")
        elif byte == astm.CR:
            parts.append("<CR>")
        elif byte == astm.LF:
            parts.append("<LF>")
        elif 32 <= byte < 127:
            parts.append(chr(byte))
        else:
            parts.append(f"<0x{byte:02X}>")
    return "".join(parts)

