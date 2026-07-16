"""ELITech Chemistry Analyzer - LIS2-A decoder (carried from elitech_daemon.py)."""

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
        return {"kind": "order",
                "sample_id": (fields[2] if len(fields) > 2 else "").strip(),
                "specimen_type": fields[3] if len(fields) > 3 else "", "raw": line}

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
