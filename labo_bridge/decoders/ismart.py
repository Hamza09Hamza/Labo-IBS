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
        return {"kind": "order", "sample_id": sample_id,
                "instrument_specimen_id": instrument_specimen_id,
                "specimen_type": fields[14] if len(fields) > 14 else "", "raw": line}

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
