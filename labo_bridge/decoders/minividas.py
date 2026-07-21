"""
bioMerieux Mini VIDAS - decoder for its own tag/value framing.

Confirmed by real capture (2026-07-21, capture_minividas.py) at 4800 baud,
no parity, 1 stop bit, 8 data bits - NOT the ASTM `|`-delimited-fields-on-
CR-terminated-lines layout the other four machines use. A Mini VIDAS frame
looks like this (control bytes shown as \\xNN):

    \\x02\\x1emtrsl|\\x1epi|\\x1epn|\\x1esi|\\x1eci653|\\x1ertHCV|
    \\x1ernAnti-HCV|\\x1ett16:21|\\x1etd07/21/25|\\x1eqlNegatif|
    \\x1eqn0.15|\\x1eqd1|\\x1d70\\x03

  \\x02 STX (start of frame)         \\x1d<seq> + \\x03  (end, GS + 2-char
  \\x1e RS separates each field        sequence tag, then ETX)
  each field is "<2-letter tag><value>|" - tag and value joined directly,
  with a trailing '|' terminating the field (not a delimiter between tag
  and value - the tag is always the first 2 letters after \\x1e).

Known tags (from the real capture; more may exist and will show up in
results/minividas_*.txt raw dumps the first time this machine sends them -
add them here rather than guessing):
    mt  message type ("rsl" = result)
    pi  patient ID
    pn  patient name
    si  sample ID
    ci  cup/carrier ID (this run's physical cup number, NOT a patient key)
    rt  test/reagent type code (e.g. "HCV")
    rn  reagent/test full name (e.g. "Anti-HCV")
    tt  test time (HH:MM)
    td  test date (MM/DD/YY)
    ql  qualitative result (e.g. "Negatif", "Positif")
    qn  quantitative/numeric result
    qd  dilution factor flag
"""

import re

TAG_RE = re.compile(r"^([a-z]{2})(.*)$")


def parse_fields(text: str) -> dict:
    """Split a frame's payload (already stripped of STX/leading RS) into
    {tag: value} by 2-letter tag prefix. Each field ends with a trailing '|'
    (stripped here, not a tag/value separator). Unrecognized/malformed
    segments are dropped rather than guessed at."""
    out = {}
    for seg in text.split("\x1e"):
        seg = seg.strip("\r\n").rstrip("|")
        if not seg:
            continue
        m = TAG_RE.match(seg)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def decode_frame(payload: str) -> list:
    """
    Decode one Mini VIDAS STX...ETX frame's inner text (RS-separated tag/value
    fields, trailing \\x1d<seq> already stripped by the caller) into a LIST of
    normalized events - unlike the ASTM/HL7 machines, one Mini VIDAS frame
    carries patient + sample + result all together rather than as separate
    H/P/O/R records, so this emits the same "patient" -> "order" -> "result"
    event sequence _Session.handle_event() already expects from the others.
    """
    fields = parse_fields(payload)
    if fields.get("mt") != "rsl":
        return [{"kind": "unknown", "raw": payload}]

    patient_id = fields.get("pi", "").strip()
    patient_name = fields.get("pn", "").strip()
    sample_id = fields.get("si", "").strip()
    cup_id = fields.get("ci", "").strip()
    test_code = fields.get("rt", "").strip()
    test_name = fields.get("rn", "").strip() or test_code
    # Prefer the quantitative value; fall back to the qualitative reading
    # (e.g. "Negatif"/"Positif") when a test has no numeric result at all.
    value = fields.get("qn", "").strip() or fields.get("ql", "").strip()

    return [
        {"kind": "patient", "patient_id": patient_id, "patient_name": patient_name,
         "raw": payload},
        # sample_id falls back to the cup ID so a run with no patient ID yet
        # (e.g. before the "enter patient ID" step becomes routine) still
        # gets a stable per-run identity instead of an empty string.
        {"kind": "order", "sample_id": sample_id or cup_id, "raw": payload},
        {"kind": "result", "test_code": test_code, "test_name": test_name,
         "value": value, "unit": "", "ref_range": "",
         "flag": fields.get("ql", "").strip(),
         "status": "measured" if fields.get("qn") else "qualitative",
         "raw": payload},
    ]
