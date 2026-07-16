"""
Sysmex XN-330 hematology analyzer - ASTM E1394 decoder.

Carries over the test-code knowledge from the original xn330_daemon.py:
 - measured CBC codes live in TEST_LABELS
 - SCAT_/DIST_ codes are references to scattergram/histogram image files, not
   values (the machine only sends the filename over ASTM, not the image)
 - codes ending in "?" are Sysmex suspect-flag confidence scores (0-100)
"""

import re

# Measured numeric CBC codes -> (friendly name, unit).
TEST_LABELS = {
    "WBC": ("White Blood Cell count", "10^3/uL"),
    "RBC": ("Red Blood Cell count", "10^6/uL"),
    "HGB": ("Hemoglobin", "g/dL"),
    "HCT": ("Hematocrit", "%"),
    "MCV": ("Mean Corpuscular Volume", "fL"),
    "MCH": ("Mean Corpuscular Hemoglobin", "pg"),
    "MCHC": ("Mean Corpuscular Hemoglobin Concentration", "g/dL"),
    "PLT": ("Platelet count", "10^3/uL"),
    "RDW-SD": ("Red cell Distribution Width (SD)", "fL"),
    "RDW-CV": ("Red cell Distribution Width (CV)", "%"),
    "PDW": ("Platelet Distribution Width", "fL"),
    "MPV": ("Mean Platelet Volume", "fL"),
    "P-LCR": ("Platelet Large Cell Ratio", "%"),
    "PCT": ("Plateletcrit", "%"),
    "NEUT#": ("Neutrophils (absolute)", "10^3/uL"),
    "LYMPH#": ("Lymphocytes (absolute)", "10^3/uL"),
    "MONO#": ("Monocytes (absolute)", "10^3/uL"),
    "EO#": ("Eosinophils (absolute)", "10^3/uL"),
    "BASO#": ("Basophils (absolute)", "10^3/uL"),
    "NEUT%": ("Neutrophils (%)", "%"),
    "LYMPH%": ("Lymphocytes (%)", "%"),
    "MONO%": ("Monocytes (%)", "%"),
    "EO%": ("Eosinophils (%)", "%"),
    "BASO%": ("Basophils (%)", "%"),
    "IG#": ("Immature Granulocytes (absolute)", "10^3/uL"),
    "IG%": ("Immature Granulocytes (%)", "%"),
    "MICROR": ("Microcytic RBC ratio", "%"),
    "MACROR": ("Macrocytic RBC ratio", "%"),
}

GRAPHIC_PREFIXES = ("SCAT_", "DIST_")


def extract_code(test_field: str) -> str:
    """Pull the test code out of ASTM's nested ^^^^CODE^rep field."""
    parts = test_field.split("^")
    return parts[4] if len(parts) > 4 else test_field


SPECIMEN_ID_RE = re.compile(r"^(\d{2})(\d{2})(\d{4})(\d{2})$")


def parse_specimen_id(sample_id: str) -> dict:
    """
    This lab's specimen IDs (e.g. "2607044407") are formatted as:
        YY MM SSSS PP
        26 07 0444 07  -> year 2026, month 07, sequence #444 this month,
                          paillasse (bench) 07
    Returns {} if sample_id doesn't match this 10-digit pattern (e.g. IDs
    from other sources/formats) rather than guessing.
    """
    m = SPECIMEN_ID_RE.match((sample_id or "").strip())
    if not m:
        return {}
    year, month, sequence, paillasse = m.groups()
    return {
        "year": 2000 + int(year),
        "month": int(month),
        "sequence": int(sequence),
        "paillasse": paillasse,
    }


def decode_record(line: str) -> dict:
    """Decode one XN-330 ASTM record line into a normalized event dict."""
    line = re.sub(r"^\d(?=[A-Z]\|)", "", line)  # strip leading frame digit
    if not line or "|" not in line:
        return {"kind": "unknown", "raw": line}

    rtype = line[0]
    fields = line.split("|")

    if rtype == "H":
        machine_id = fields[4].strip() if len(fields) > 4 else ""
        return {"kind": "header", "analyzer_model": machine_id,
                "timestamp": fields[-1] if fields else "", "raw": line}

    if rtype == "P":
        return {"kind": "patient",
                "patient_id": fields[2] if len(fields) > 2 else "",
                "patient_name": fields[5] if len(fields) > 5 else "",
                "raw": line}

    if rtype == "O":
        # sample id: plain field 2, else nested in instrument field 3
        sample_id = fields[2].strip() if len(fields) > 2 and fields[2].strip() else ""
        if not sample_id and len(fields) > 3:
            parts = [p for p in fields[3].split("^") if p]
            sample_id = parts[0] if parts else ""
        ev = {"kind": "order", "sample_id": sample_id, "raw": line}
        ev.update(parse_specimen_id(sample_id))  # adds year/month/sequence/paillasse if it matches
        return ev

    if rtype == "R":
        if len(fields) < 4:
            return {"kind": "unknown", "raw": line}
        code = extract_code(fields[2])
        value = fields[3]
        flag = fields[6] if len(fields) > 6 else ""

        # graphic reference (filename only, no image over ASTM)
        if code.startswith(GRAPHIC_PREFIXES):
            return {"kind": "result", "test_code": code, "test_name": code,
                    "value": value, "unit": "", "ref_range": "", "flag": flag,
                    "status": "graphic", "raw": line}

        # suspect-flag confidence score, not a measurement
        if code.endswith("?"):
            name = code[:-1].replace("_", " ")
            return {"kind": "result", "test_code": code, "test_name": name,
                    "value": value, "unit": "", "ref_range": "", "flag": flag,
                    "status": "suspect_score", "raw": line}

        name, unit = TEST_LABELS.get(code, (code, fields[4] if len(fields) > 4 else ""))
        return {"kind": "result", "test_code": code, "test_name": name,
                "value": value, "unit": unit,
                "ref_range": fields[5] if len(fields) > 5 else "",
                "flag": flag, "status": "measured", "raw": line}

    return {"kind": "unknown", "raw": line}
