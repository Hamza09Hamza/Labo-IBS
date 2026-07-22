"""I-Smart 30 PRO - ASTM E1394-97 decoder (carried from ismart_daemon.py)."""

import re


def decode_record(line: str) -> dict:
    line = re.sub(r"^\d(?=[A-Z]\|)", "", line)
    if not line or "|" not in line:
        return {"kind": "unknown", "raw": line}

    rtype = line[0]
    fields = line.split("|")

    if rtype == "H":
        parts = (fields[4] if len(fields) > 4 else "").split("^")
        return {"kind": "header",
                "analyzer_model": parts[0].strip() if parts else "",
                "timestamp": fields[-1] if fields else "", "raw": line}

    if rtype == "P":
        return {"kind": "patient",
                "patient_id": fields[2] if len(fields) > 2 else "",
                "patient_name": fields[5] if len(fields) > 5 else "",
                "raw": line}

    if rtype == "O":
        # O-2 is the real Specimen ID (the scanned/entered barcode - the same
        # kind of ID XN-330/Selectra send). O-3 "Instrument Specimen ID" is a
        # secondary ID the analyzer generates for its OWN internal tracking -
        # it looks nothing like the clinic's real sample ID format and should
        # only be a fallback, not the preferred source.
        specimen_id = fields[2] if len(fields) > 2 else ""
        instrument_specimen_id = fields[3] if len(fields) > 3 else ""
        sample_id = specimen_id or instrument_specimen_id
        # Specimen type's field position isn't stable across sessions on this
        # machine - a "2PCal" capture (2026-07-21) had it at index 14, a
        # "1PCal" capture (2026-07-22) had it at index 15. Rather than trust
        # one fixed index, scan all fields for anything ending in "cal".
        specimen_type = next((f for f in fields if f.strip().lower().endswith("cal")), "")
        # The machine runs its own electrode calibration/QC cycle automatically
        # (before/after real patient tests) - confirmed via real captures:
        # these carry a specimen_type like "2PCal"/"1PCal" (vs "Blood" for a
        # real patient specimen) and their result codes are diagnostic
        # (Slope/Drift1/Drift2/Measured1/Measured2), never real clinical
        # values - kind="calibration" tells server.py to skip the whole
        # session (no sample row, no pending/result rows) instead of
        # cluttering Recent Samples and the mapping backlog with machine
        # housekeeping data.
        if specimen_type:
            return {"kind": "calibration", "raw": line}
        return {"kind": "order", "sample_id": sample_id,
                "instrument_specimen_id": instrument_specimen_id,
                "specimen_type": specimen_type, "raw": line}

    if rtype == "R":
        parts = (fields[2] if len(fields) > 2 else "").split("^")
        test_name = parts[-2] if len(parts) > 1 else (fields[2] if len(fields) > 2 else "")
        return {"kind": "result",
                "test_code": test_name, "test_name": test_name,
                "value": fields[3] if len(fields) > 3 else "",
                "unit": fields[4] if len(fields) > 4 else "",
                "ref_range": fields[5] if len(fields) > 5 else "",
                "flag": (fields[6] if len(fields) > 6 else "").strip("^"),
                "status": fields[8] if len(fields) > 8 else "", "raw": line}

    return {"kind": "unknown", "raw": line}
