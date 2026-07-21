"""
Selectra chemistry analyzer - LIS2-A decoder (carried from elitech_daemon.py).

The analyzer is a Selectra; ELITech is the name of the software/protocol
stack it runs, not the machine itself - "selectra" is the correct name to
use anywhere this needs to be identified (machine field sent to the clinic
API, printed logs, DB rows), per the user's correction on 2026-07-16.
"""

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
        sample_id = (fields[2] if len(fields) > 2 else "").strip()
        carrier = (fields[3] if len(fields) > 3 else "").strip()
        # The Selectra runs its own QC/calibration cycle automatically -
        # confirmed via real capture (2026-07-21): pure QC/blank/calibrator
        # runs (ELITROL I, BLANK, AU CAL, CREAT CAL, ELICAL 2) have NO
        # sample_id at all, only a named carrier in this field instead; a
        # second pattern (STD AU, STD CAL, STD CREAT) DOES put something in
        # the sample_id slot, but it's a standard-curve label, not a real
        # sample, always prefixed "STD ". Neither is a real patient - flag
        # both so server.py skips the whole run (no sample/result rows),
        # same treatment as the I-Smart's calibration cycle.
        if not sample_id or sample_id.upper().startswith("STD "):
            return {"kind": "calibration", "raw": line}
        return {"kind": "order", "sample_id": sample_id,
                "specimen_type": fields[14] if len(fields) > 14 else "", "raw": line}

    if rtype == "R":
        parts = (fields[2] if len(fields) > 2 else "").split("^")
        test_name = parts[-1] if parts else ""
        return {"kind": "result",
                "test_code": test_name, "test_name": test_name,
                "value": fields[3] if len(fields) > 3 else "",
                "unit": fields[4] if len(fields) > 4 else "",
                "ref_range": "",
                "flag": fields[6] if len(fields) > 6 else "",
                "status": fields[8] if len(fields) > 8 else "", "raw": line}

    return {"kind": "unknown", "raw": line}
