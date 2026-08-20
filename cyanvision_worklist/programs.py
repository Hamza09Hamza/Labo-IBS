"""CYANVision outbound worklist program identifiers.

These identifiers are deliberately separate from ``labo_bridge.mappings``.
The latter maps codes observed in result OBX records to clinic parameters;
CYANVision's worklist DSP.8 value is an analyzer-side program selection.

Confirmed 2026-08-20 by a controlled field trial (see CRE_DSP8_TRIALS below)
plus direct observation of the analyzer's own "Selection du programme"
screen: DSP.8 selects the program correctly when it is the *exact literal
program name shown on that screen* (e.g. "CRE"), not a numeric ID and not a
guessed abbreviation ("CREA" was rejected - the connection dropped without
ACK). The values below were read directly off that screen. A numeric guess
had been used here previously (ALP=3, CRE=11, LIPASE=23, sourced from
unrelated NTE.8 result metadata) and never actually worked.
"""

from __future__ import annotations


WORKLIST_PROGRAM_IDS = {
    # result code -> literal program name, exactly as shown on the
    # analyzer's own "Selection du programme" screen
    "ALP": "ALP",
    "CRE": "CRE",
    "GPT": "GPT",
    "GLUC": "GLUC",
    "GGT": "GGT",
    "LIPASE": "LIPASE",
    "IRON": "IRON",
}


# Confirms the hypothesis above and stays in place as reusable tooling for
# validating any new code before it goes live (e.g. whatever this analyzer
# calls the clinic's UA test - not yet seen on the visible part of the
# program list). CRE-TRIAL-TXT="CRE" is the one that was confirmed working:
# the analyzer's own worklist screen showed CV-02-CRE's program change from
# the default ALP to CRE after this exact push.
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
    """Return the confirmed DSP.8 program name for a result code."""
    code = str(test_code or "").strip().upper()
    try:
        return WORKLIST_PROGRAM_IDS[code]
    except KeyError as exc:
        raise ValueError(
            f"no CYANVision outbound Program ID is known for test code {code or '(empty)'}"
        ) from exc
