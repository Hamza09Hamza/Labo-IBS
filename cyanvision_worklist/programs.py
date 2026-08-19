"""CYANVision outbound worklist program identifiers.

These identifiers are deliberately separate from ``labo_bridge.mappings``.
The latter maps codes observed in result OBX records to clinic parameters;
CYANVision's worklist DSP.8 value is an analyzer-side program selection.

The CY014 manual demonstrates the eighth DSP data line with ``GLUC`` but does
not name its identifier namespace.  The numeric values below are therefore a
controlled field trial based on ProgramID values observed in real CYANVision
NTE.8 result metadata, not values asserted by the manual itself.
"""

from __future__ import annotations


WORKLIST_PROGRAM_IDS = {
    # result code -> ProgramID observed in NTE.8
    "ALP": "3",
    "CRE": "11",
    "LIPASE": "23",
}


# Temporary field trial (2026-08-18): CRE's numeric ProgramID (11, above)
# never completes the DSR/ACK handshake on the real unit, and the analyzer
# keeps preselecting ALP regardless of what DSP.8 carries. CY014's own
# worklist example puts a literal mnemonic in DSP.8 ("GLUC" - 4 letters, for
# Glucose) rather than a translated numeric ID, so each entry below is one
# candidate DSP.8 payload for a Creatinine push. They show up as ordinary
# selectable entries in the CYANVision console's test dropdown - stage one,
# arm it, watch the analyzer's screen for whether it preselects Creatinine
# instead of ALP, then move to the next. Delete this block (and its entries
# below) once the working format is confirmed and CRE is updated to match.
CRE_DSP8_TRIALS = {
    "CY014-TRIAL-GLUC": "GLUC",           # exact selector from the manufacturer's example
    "CRE-TRIAL-CREA": "CREA",              # 4-letter mnemonic, matches GLUC's pattern
    "CRE-TRIAL-TXT": "CRE",                # 3-letter mnemonic, matches the LIS result code
    "CRE-TRIAL-MIXED": "Crea",             # mixed case, in case the table is case-sensitive
    "CRE-TRIAL-FULLNAME": "CREATININE",    # full analyte name, spelled out
    "CRE-TRIAL-NUM": "11",                 # original numeric guess (known to fail - control)
    "CRE-TRIAL-NUM0": "011",               # zero-padded numeric
    "CRE-TRIAL-BLANK": "",                 # DSP.8 left empty - does ALP still show?
}
WORKLIST_PROGRAM_IDS.update(CRE_DSP8_TRIALS)

CRE_DSP8_TRIAL_SEQUENCE = (
    {
        "sample_id": "JD123", "test_code": "CY014-TRIAL-GLUC",
        "label": "CY014 GLUC", "dsp8": "GLUC", "sequence": 0,
        "given_name": "Johnathana", "family_name": "Does",
        "birth_date": "1955-06-04", "sex": "F",
    },
    {"sample_id": "CV-01-CREA", "test_code": "CRE-TRIAL-CREA", "label": "01 CREA", "dsp8": "CREA", "sequence": 1},
    {"sample_id": "CV-02-CRE", "test_code": "CRE-TRIAL-TXT", "label": "02 CRE", "dsp8": "CRE", "sequence": 2},
    {"sample_id": "CV-03-CASE", "test_code": "CRE-TRIAL-MIXED", "label": "03 Crea", "dsp8": "Crea", "sequence": 3},
    {"sample_id": "CV-04-FULL", "test_code": "CRE-TRIAL-FULLNAME", "label": "04 CREATININE", "dsp8": "CREATININE", "sequence": 4},
    {"sample_id": "CV-05-NUM11", "test_code": "CRE-TRIAL-NUM", "label": "05 NUM11", "dsp8": "11", "sequence": 5},
    {"sample_id": "CV-06-NUM011", "test_code": "CRE-TRIAL-NUM0", "label": "06 NUM011", "dsp8": "011", "sequence": 6},
    {"sample_id": "CV-07-BLANK", "test_code": "CRE-TRIAL-BLANK", "label": "07 BLANK", "dsp8": "", "sequence": 7},
)


def program_id_for(test_code: str) -> str:
    """Return the field-observed worklist ProgramID for a result code."""
    code = str(test_code or "").strip().upper()
    try:
        return WORKLIST_PROGRAM_IDS[code]
    except KeyError as exc:
        raise ValueError(
            f"no CYANVision outbound Program ID is known for test code {code or '(empty)'}"
        ) from exc
