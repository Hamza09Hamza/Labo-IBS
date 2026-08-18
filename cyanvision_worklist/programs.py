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


def program_id_for(test_code: str) -> str:
    """Return the field-observed worklist ProgramID for a result code."""
    code = str(test_code or "").strip().upper()
    try:
        return WORKLIST_PROGRAM_IDS[code]
    except KeyError as exc:
        raise ValueError(
            f"no CYANVision outbound Program ID is known for test code {code or '(empty)'}"
        ) from exc
