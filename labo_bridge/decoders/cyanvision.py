"""
CyanVision - HL7 v2.3.1 decoder (carried from cyanvision_daemon.py).

Unlike the ASTM decoders (one record line at a time), HL7 works on whole
segments. handle_message in server.py feeds each segment's split fields here.
"""


def decode_segment(fields: list) -> dict:
    """Decode one HL7 segment (already split on '|') into a normalized event."""
    seg_type = fields[0] if fields else ""

    if seg_type == "MSH":
        return {"kind": "header",
                "message_type": fields[8] if len(fields) > 8 else "",
                "control_id": fields[9] if len(fields) > 9 else "",
                "analyzer_model": fields[2] if len(fields) > 2 else "", "raw": "|".join(fields)}

    if seg_type == "PID":
        patient_id = fields[2] if len(fields) > 2 else ""
        # The CyanVision runs its own QC/drift check automatically - confirmed
        # via real capture (2026-07-22): these carry patient_id "Drift"
        # (vs a real numeric sample ID like "569" for an actual patient run).
        # Same treatment as the I-Smart's calibration cycle and the
        # Selectra's QC/standard runs - flag it so server.py skips the whole
        # message (no sample/result written) instead of cluttering Recent
        # Samples and the mapping backlog with machine housekeeping data.
        if patient_id.strip().lower() == "drift":
            return {"kind": "calibration", "raw": "|".join(fields)}
        return {"kind": "patient",
                "patient_id": patient_id,
                "patient_name": fields[5] if len(fields) > 5 else "",
                "raw": "|".join(fields)}

    if seg_type == "OBR":
        return {"kind": "order",
                "sample_id": fields[2] if len(fields) > 2 else "",
                "raw": "|".join(fields)}

    if seg_type == "OBX":
        test = fields[4] if len(fields) > 4 else (fields[3] if len(fields) > 3 else "")
        # Second calibration-code family found (2026-07-23): D-LIN578, a
        # linearity/wavelength check (578nm), same shape as D-DRIFT (mAbs
        # unit, sample_id "4" not a real specimen ID, timestamp over a year
        # stale) but with a DIFFERENT patient_id than "Drift" - so the PID-
        # based filter above wouldn't have caught this one. No currently
        # mapped code on any machine starts with "D-" (checked before
        # adding this), and the manufacturer's own naming for these
        # internal checks all share that prefix, so filtering on it here
        # is safe rather than waiting for a second confirmed capture.
        if test.strip().upper().startswith("D-"):
            return {"kind": "calibration", "raw": "|".join(fields)}
        return {"kind": "result",
                "test_code": test, "test_name": test,
                "value": fields[5] if len(fields) > 5 else "",
                "unit": fields[6] if len(fields) > 6 else "",
                "ref_range": fields[7] if len(fields) > 7 else "",
                "flag": (fields[8] if len(fields) > 8 else "").strip(),
                "status": "", "raw": "|".join(fields)}

    return {"kind": "unknown", "raw": "|".join(fields)}
