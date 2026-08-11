"""
Mindray WATO EX-35 anesthesia workstation - HL7 v2.6 IHE PCD-01 decoder.

Confirmed via real capture (2026-08-06, capture_listener.py, MLLP-framed over
the machine's [System] > [Network] > HL7 Ethernet output, port 6010):
this is device/ventilator TELEMETRY (pressures, tidal volume, resp rate,
O2 concentration, ventilator mode, device status), NOT a lab test result -
there is no sample_id tied to a clinic appointment/tube and no clinic
labo_param exists for any of these readings. This module is intentionally
standalone: it does NOT feed matcher.py, mappings.py, or the clinic API
send path (see api_client.py) - none of that applies here. Local-only
decoding for now.

One HL7 message = one MSH + PID + PV1 + OBR header, followed by one OBX
segment per reading. PID/PV1 are consistently empty in every real capture
seen so far (no patient tied to this feed) - carried through anyway in
case that ever changes on a real case.

OBX field shape (confirmed against real captures, 0-indexed after split('|')):
    [1]  seq            - 1, 2, 3... within this message
    [2]  value type      - "NM" (numeric), "CWE" (coded), "SN" (structured
                            numeric, e.g. a ratio like "1:2"), or "" (no
                            reading this cycle - paired with status "X")
    [3]  code            - "<MDC code>^<MDC label>^<coding system>",
                            e.g. "151957^MDC_VENT_PRESS_MAX^MDC"
    [4]  observation id   - a dotted numeric id, not used here
    [5]  value            - shape depends on [2]:
                               NM:  plain number, e.g. "15"
                               CWE: "<code>^<label>^<system>", the label is
                                    the human-readable reading, e.g.
                                    "50005^MNDRY_VENT_MODE_VCV^99MNDRY"
                               SN:  a compound expression, e.g. "^1^:^2"
                                    (ratio "1:2") - caret-joined parts
                            empty when this cycle has no reading (status "X")
    [6]  unit             - "<unit code>^<unit label>^<unit system>",
                            e.g. "266048^MDC_DIM_CM_H2O^MDC" -> "cmH2O"
    [11] status flag       - "F" (final/settled), "R" (refresh/live-updating),
                            "X" (invalid/no reading this cycle, paired with
                            value type "" and value "")
    [14] timestamp         - YYYYMMDDHHMMSS-ZZZZ, the machine's own clock
"""

import re

# Human-readable labels for every MDC_*/MNDRY_* code actually seen in real
# captures (2026-08-06) - built from evidence, not the full IEEE 11073 MDC
# nomenclature spec. A code not in here just falls back to showing the raw
# MDC_* name (see friendly_label()) rather than guessing at a translation.
FRIENDLY_LABELS = {
    # Device/mode status
    "MDC_EVT_STAT_DEV": "Device Status",
    "MDC_EVT_STAT_RUNNING": "Running",
    "MDC_EVT_STAT_STANDBY": "Standby",
    "MNDRY_EVT_STAT_MODE_DEV": "Device Mode",
    "MNDRY_EVT_STAT_MODE_NORMAL": "Normal",
    "MNDRY_EVT_PATIENT_TYPE": "Patient Type",
    "MDC_EVT_STAT_DEV_MODE_ADULT": "Adult",
    "MNDRY_EVT_STAT_WARMER_ON_BOOL": "Warmer On",
    "MDC_TRUE": "Yes",

    # Ventilator mode
    "MDC_VENT_MODE": "Ventilator Mode",
    "MNDRY_VENT_MODE_VCV": "Volume Control (VCV)",
    "MNDRY_VENT_MODE_MANUAL": "Manual",
    "MNDRY_VENT_MODE_ACGO": "ACGO (Bag/Manual Circuit)",

    # Live measurements
    "MDC_VENT_PRESS_MAX": "Peak Airway Pressure",
    "MDC_PRESS_AWAY_INSP_MEAN": "Mean Inspiratory Pressure",
    "MDC_PRESS_RESP_PLAT": "Plateau Pressure",
    "MDC_VENT_PRESS_AWAY_END_EXP_POS": "PEEP (measured)",
    "MDC_VOL_MINUTE_AWAY": "Minute Volume",
    "MDC_VOL_AWAY_TIDAL_EXP": "Tidal Volume (Expired)",
    "MDC_VOL_AWAY_TIDAL_INSP": "Tidal Volume (Inspired)",
    "MDC_RATIO_IE": "I:E Ratio",
    "MDC_RES_AWAY": "Airway Resistance",
    "MDC_COMPL_LUNG": "Lung Compliance",
    "MDC_CONC_AWAY_O2_INSP": "Inspired O2 Concentration",
    "MDC_VENT_RESP_RATE": "Respiratory Rate",

    # Ventilator settings (operator-configured targets, not live measurements)
    "MDC_VOL_AWAY_TIDAL_SETTING": "Tidal Volume (Set)",
    "MDC_VENT_RESP_RATE_SETTING": "Respiratory Rate (Set)",
    "MDC_RATIO_IE_SETTING": "I:E Ratio (Set)",
    "MNDRY_VENT_PAUSE_TIME_PERCENT_SETTING": "Inspiratory Pause (Set)",
    "MDC_PRESS_AWAY_END_EXP_POS_SETTING": "PEEP (Set)",
    "MDC_VENT_PRESS_AWAY_END_EXP_POS_SETTING": "PEEP (Set)",
    "MNDRY_VENT_PRESS_LIMIT_SETTING": "Pressure Limit (Set)",

    # Units
    "MDC_DIM_CM_H2O": "cmH2O",
    "MDC_DIM_DIMLESS": "",  # dimensionless - no unit to show
    "MDC_DIM_L_PER_MIN": "L/min",
    "MDC_DIM_MILLI_L": "mL",
    "MDC_DIM_CM_H2O_PER_L_PER_SEC": "cmH2O/L/s",
    "MDC_DIM_MILLI_L_PER_CM_H2O": "mL/cmH2O",
    "MDC_DIM_PERCENT": "%",
    "MDC_DIM_RESP_PER_MIN": "breaths/min",

    # Device/system type (OBR field 5, not an OBX reading, but shares the
    # same code^label^system shape)
    "MDC_DEV_SYS_ANESTH": "Anesthesia System",
}


def friendly_label(mdc_name: str) -> str:
    """Human-readable label for an MDC_*/MNDRY_* code name - falls back to
    the raw code name itself if it's not in FRIENDLY_LABELS (a future
    capture may show a code never seen before; better to show its real
    name than hide or guess at it)."""
    return FRIENDLY_LABELS.get(mdc_name, mdc_name)


MDC_LABEL_RE = re.compile(r"^\d+\^([A-Za-z0-9_]+)\^")


def _mdc_label(field: str) -> str:
    """Pull the human-readable MDC_* / MNDRY_* label out of a
    "<code>^<LABEL>^<system>" field - falls back to the raw field if it
    doesn't match that shape (never seen in real captures, but the machine's
    own wire format isn't something to trust blindly)."""
    m = MDC_LABEL_RE.match(field or "")
    return m.group(1) if m else (field or "")


def _decode_value(value_type: str, raw_value: str):
    """Return (display_value, raw_value) for one OBX's value field,
    interpreted per its value type. display_value is always a string -
    this is telemetry for a human to read, not something matched/typed
    downstream the way lab results are."""
    if not raw_value:
        return "", raw_value
    if value_type == "CWE":
        return _mdc_label(raw_value), raw_value
    if value_type == "SN":
        # "^1^:^2" -> "1:2" (caret-joined parts, empty parts dropped)
        parts = [p for p in raw_value.split("^") if p]
        return "".join(parts), raw_value
    # NM, or any other/unrecognized type - use as-is (already a plain number
    # for every NM case seen in real captures)
    return raw_value, raw_value


def decode_obx(fields: list) -> dict:
    """Decode one OBX segment (already split on '|') into a normalized
    reading dict. Returns None for a segment that isn't OBX at all."""
    if not fields or fields[0] != "OBX":
        return None

    value_type = fields[2] if len(fields) > 2 else ""
    code_field = fields[3] if len(fields) > 3 else ""
    raw_value = fields[5] if len(fields) > 5 else ""
    unit_field = fields[6] if len(fields) > 6 else ""
    status = fields[11] if len(fields) > 11 else ""
    timestamp = fields[14] if len(fields) > 14 else ""

    display_value, _ = _decode_value(value_type, raw_value)
    mdc_code = _mdc_label(code_field)
    mdc_unit = _mdc_label(unit_field) if unit_field else ""
    # A CWE value IS itself an MDC_*/MNDRY_* code (e.g. "MNDRY_VENT_MODE_VCV")
    # - friendly-label it the same way as the reading's own code/unit, not
    # just shown as the raw MDC name.
    display_value = friendly_label(display_value) if value_type == "CWE" else display_value

    return {
        "code": mdc_code,
        "label": friendly_label(mdc_code),
        "raw_code": code_field,
        "value_type": value_type,
        "value": display_value,
        "raw_value": raw_value,
        "unit": mdc_unit,
        "unit_label": friendly_label(mdc_unit) if mdc_unit else "",
        "status": status,
        # "X" means the machine explicitly marked this cycle's reading
        # invalid/unavailable (e.g. no breath cycle completed yet after a
        # mode switch) - not the same as a missing/malformed segment.
        "invalid": status == "X",
        "timestamp": timestamp,
    }


def decode_message(segments: list) -> dict:
    """
    Decode one full HL7 message's segments (already split via
    hl7_mllp.split_segments) into {header fields..., "readings": [...]}.
    Every OBX segment becomes one entry in "readings"; MSH/OBR give the
    message's own control id and timestamp for context.
    """
    header = {"control_id": "", "message_timestamp": "", "analyzer_model": ""}
    readings = []

    for seg in segments:
        fields = seg.split("|")
        seg_type = fields[0] if fields else ""

        if seg_type == "MSH":
            header["analyzer_model"] = fields[2].split("^")[0] if len(fields) > 2 else ""
            header["message_timestamp"] = fields[6] if len(fields) > 6 else ""
            header["control_id"] = fields[9] if len(fields) > 9 else ""
        elif seg_type == "OBX":
            reading = decode_obx(fields)
            if reading:
                readings.append(reading)

    header["readings"] = readings
    return header


def readable_summary(decoded_message: dict) -> str:
    """
    Render one decoded message as plain text, one reading per line - meant
    to be handed to someone non-technical (e.g. "here's what the machine
    actually sends"), not for programmatic use. Invalid/no-reading entries
    are shown with a clear marker rather than silently skipped, since "the
    machine has no reading yet" is itself useful information (e.g. right
    after a ventilator mode switch).
    """
    lines = [
        f"Message #{decoded_message['control_id']}  "
        f"({decoded_message['message_timestamp']}, {decoded_message['analyzer_model']})"
    ]
    for r in decoded_message["readings"]:
        if r["invalid"]:
            lines.append(f"  {r['label']}: (no reading)")
            continue
        unit_suffix = f" {r['unit_label']}" if r["unit_label"] else ""
        lines.append(f"  {r['label']}: {r['value']}{unit_suffix}")
    return "\n".join(lines)
