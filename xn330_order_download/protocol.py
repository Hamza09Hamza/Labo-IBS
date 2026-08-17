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

    # O-2 (Specimen ID) vs O-3 (Instrument Specimen ID):
    #   1. Selector in O-3 only, O-2 blank (matching a real captured
    #      RESULT-UPLOAD session) ACKed cleanly but produced no visible
    #      order and no error message.
    #   2. Adding the STRIPPED plain sample_id to O-2 (2026-08-17, sample
    #      2608217116) still produced the exact same real error: "N echant.
    #      de l'ordi hote different de la demande. N echant. de la demande
    #      sera utilise" (Sample No. from host computer differs from the
    #      request). The analyzer names a sample-number mismatch even
    #      though O-2 held the identical digits it queried with.
    # The one difference between what we sent and "the request" itself:
    # the analyzer's own Q-3 selector is fixed-width, space-padded (e.g.
    # "^^            2608217116^M", not "^^2608217116^M") - we were
    # comparing/sending the STRIPPED sample_id, not the padded component
    # exactly as the analyzer itself sent it. Using the unstripped
    # sample-ID component of the query selector for O-2 - not the cleaned
    # order["sample_id"] - so O-2 is a byte-for-byte match of what the
    # analyzer itself calls "the request", per its own error text.
    query_sample_component = sample_id
    if query_selector is not None:
        selector_parts = query_selector.split("^")
        if len(selector_parts) >= 3:
            query_sample_component = selector_parts[2]
    order_fields = [""] * 26
    order_fields[0] = "O"
    order_fields[1] = "1"
    order_fields[2] = query_sample_component
    order_fields[3] = selector
    order_fields[4] = "\\".join(f"^^^^{code}" for code in tests)
    order_fields[6] = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    order_fields[11] = "N"  # normal sample analysis
    order_fields[25] = "Q"  # matching order exists for this inquiry

    # H-4 (Sender Name or ID): every one of 9 real captured XN-330 sessions
    # (results/xn330_*.txt) shows this analyzer always populates its own H-4
    # with its identity ("    XN-330^00-29^18762^^^^CX851950") when it is the
    # sender - never blank. We are the sender in the order-download
    # direction, and left H-4 entirely blank. The O-3 fix above did not make
    # tests appear on a real armed order (sample 2608217107, 2026-08-17)
    # despite a clean ASTM ACK, consistent with the same class of problem
    # Selectra had: a blank/unrecognized sender identity being silently
    # discarded at the application level rather than causing a transport
    # error. Not a guess at the exact required string - LABO-BRIDGE
    # identifies this host descriptively, matching the structural pattern
    # (H-4 populated, not blank) rather than inventing a value formatted
    # like the analyzer's own instrument ID.
    return [
        "H|\\^&|||LABO-BRIDGE||||||||E1394-97",
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
    """Frame each ASTM record independently, one record per frame.

    A real captured XN-330 session (2026-07-22,
    results/xn330_20260722_145258_695713.txt - 140+ records, H/P/C/O/R/L)
    never uses ETB (0x17) anywhere; every single one of its 294 frames is
    terminated with ETX (0x03), one complete record per frame - confirmed
    by counting terminator bytes across the whole raw capture, not
    inferred. Our previous implementation ignored record boundaries
    entirely and packed multiple different records into one frame, split
    purely by byte count with ETB continuation between chunks - a real,
    confirmed mismatch from how this instrument's own messages are shaped,
    found after O-3/H-4 field fixes and connection-lifecycle changes alone
    did not make a real armed order appear on the analyzer's screen despite
    a clean ASTM ACK (2026-08-17). A record that itself exceeds
    max_text_bytes is still split across multiple frames (necessary for the
    long O record listing every requested test), using ETB between its own
    pieces and ETX only on that record's last piece, since no real capture
    shows a single record this XN-330 sends exceeding the byte cap to
    confirm behavior there either way.
    """
    frames = []
    frame_number = 1
    for record in records:
        encoded = (record + "\r").encode("ascii", errors="strict")
        pieces = [encoded[index:index + max_text_bytes] for index in range(0, len(encoded), max_text_bytes)] or [b""]
        for piece_index, piece in enumerate(pieces):
            is_last_piece = piece_index == len(pieces) - 1
            frames.append(build_frame(frame_number, piece.decode("ascii"), is_last_piece))
            frame_number += 1
    return frames


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

