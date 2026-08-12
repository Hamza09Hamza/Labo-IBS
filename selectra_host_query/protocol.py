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
# name "Phosphatase ALP". Kept as reference data only: the O record's
# universal-test-ID field turned out to be unused by this Selectra (see the
# field [4] evidence in build_order_records below), so this map is not
# currently consumed anywhere. Left here in case a future format change
# needs the short-code vocabulary again.
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


# Evidence log, most recent first. Every variant below is a genuine attempt,
# not a guess pulled from generic LIS2-A docs (none exist for this model) -
# each is either lifted directly from a real captured Selectra-originated
# record, or is the one remaining unexplored slot in that same record shape.
#   - 2026-08-12, samples 2003/2004: H record field [9]="SELECTRA" -> ACKed,
#     no worklist effect. Real captured H records always say "WINLAB" there;
#     switching to "WINLAB" is now baseline in every variant below.
#   - 2026-08-12, samples 2007/2008: O record field [4] populated with
#     "^^^SGPT^SGPT" / "^^^Calcium" -> ACKed, no worklist effect. Real
#     captured O records always leave field [4] empty; blank field [4] is
#     also now baseline below.
#   - 2026-08-12, sample 2009: field [4] blank (as above) -> still ACKed,
#     still no worklist effect. Confirms the blocker is not field [4] alone.
#   - Real O record for reference: "O|1|339|||R||||||||||Normal||||||||||F"
#     (result-upload direction; field [15]="Normal" not a specimen type,
#     trailing field [25]="F" or "I", never "O").
#   - Real H record for reference: "H|\^&|||PROM^4.3.13||||1.5|WINLAB||P|LIS2-A|<ts>"
#     (field [8]="1.5", ours leaves it blank).
# Since single-field changes have not worked, build_order_variants() below
# generates several distinct whole-record shapes to try in sequence against
# real hardware, rather than one more isolated guess.
def build_order_records(order: dict) -> list[str]:
    """Build the baseline LIS2-A H/P/O/L order-download transaction (variant 0)."""
    return build_order_variants(order)[0][1]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def build_order_variants(order: dict) -> list[tuple[str, list[str]]]:
    """Build several distinct H/P/O/L record shapes to try against real hardware.

    Returns a list of ``(label, records)`` pairs. Each variant changes more
    than one field at once on purpose - single-field changes (WINLAB, R
    priority, field [4] content) have already been tried individually against
    real hardware without result, so this tries whole alternate shapes instead
    of the next isolated tweak.
    """
    sample_id = _clean(order["sample_id"])
    patient_id = _clean(order.get("patient_id") or sample_id)
    family_name = _clean(order.get("family_name"))
    given_name = _clean(order.get("given_name"))
    birth_date = _clean(order.get("birth_date")).replace("-", "")
    sex = _clean(order.get("sex") or "U").upper()
    specimen_type = _clean(order.get("specimen_type") or "SERUM")
    tests = [_clean(code) for code in order.get("tests") or [] if _clean(code)]
    test_id = tests[0] if tests else ""
    stamp = _stamp()

    h_baseline = f"H|\\^&|||LABO-BRIDGE-HQ|||||WINLAB||P|LIS2-A|{stamp}"
    h_version = f"H|\\^&|||LABO-BRIDGE-HQ|||||1.5|WINLAB||P|LIS2-A|{stamp}"
    p_record = f"P|1||{patient_id}||{family_name}^{given_name}||{birth_date}|{sex}"
    l_record = "L|1|N"

    variants: list[tuple[str, list[str]]] = []

    # 0: current baseline - field [4] blank, trailing status "O".
    variants.append((
        "baseline: blank test-id field, trailing O",
        [h_baseline, p_record, f"O|1|{sample_id}|||R||||||N||||{specimen_type}||||||||||O", l_record],
    ))

    # 1: trailing status "F" (Final) instead of "O" - matches every real
    # captured O record's own trailing field, never tested in this position.
    variants.append((
        "trailing status F instead of O",
        [h_baseline, p_record, f"O|1|{sample_id}|||R||||||N||||{specimen_type}||||||||||F", l_record],
    ))

    # 2: trailing status blank entirely.
    variants.append((
        "trailing status blank",
        [h_baseline, p_record, f"O|1|{sample_id}|||R||||||N||||{specimen_type}|||||||||||", l_record],
    ))

    # 3: H record field [8]="1.5" (matches real captured H records exactly,
    # including the version field we've left blank so far).
    variants.append((
        "H field [8]=1.5 (full real H-record match)",
        [h_version, p_record, f"O|1|{sample_id}|||R||||||N||||{specimen_type}||||||||||F", l_record],
    ))

    # 4: bare-minimum O record - only sample ID and priority, everything else
    # blank (mirrors how sparse real O records actually are: mostly empty
    # fields with only [2] and [5] populated).
    variants.append((
        "minimal O record (sample id + priority only)",
        [h_baseline, p_record, f"O|1|{sample_id}||||R", l_record],
    ))

    # 5: test name back in field [4], plain (no short code, no ^^^ prefix) -
    # untested exact shape, in case the leading ^^^ component markers (not
    # the presence of a test name) were the actual problem.
    variants.append((
        "plain test name in field [4], no ^^^ prefix",
        [h_baseline, p_record,
         f"O|1|{sample_id}||{test_id}|R||||||N||||{specimen_type}||||||||||F", l_record],
    ))

    # 6: no P record at all - some ASTM hosts send H/O/L only for a query
    # response and treat P as upload-direction only; untested here.
    variants.append((
        "no P record (H/O/L only)",
        [h_baseline, f"O|1|{sample_id}|||R||||||N||||{specimen_type}||||||||||F", l_record],
    ))

    # 7: patient ID left blank in P record (real captured P records from this
    # Selectra always leave patient ID blank - see "P|1||||BOUCHEROUR|||M" -
    # our staged patient_id may itself be a rejected/unexpected value).
    variants.append((
        "P record with blank patient id (matches real captures)",
        [h_baseline, f"P|1||||{family_name}^{given_name}||{birth_date}|{sex}",
         f"O|1|{sample_id}|||R||||||N||||{specimen_type}||||||||||F", l_record],
    ))

    return variants


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
