"""
Match an incoming machine test code to a clinic labo_param.

Deliberately conservative: ONLY the curated per-machine maps in mappings.py
count as a match. Anything else returns method='none' and is staged as
'pending' for a human. No fuzzy matching, no guessing on clinical data.
"""

from . import mappings


def match(machine: str, test_code: str) -> dict:
    """
    Return {'param_id', 'abbrev', 'name', 'method',
            'service_tarification_id', 'service_tarification_name'}.
    method is 'curated' when found in the verified map, else 'none' (all
    fields None). Every code in mappings.MAPS carries its own
    service_tarification_id/name - a machine is NOT assumed to belong to a
    single exam (Selectra alone spans ~20 different exams; only XN-330's CBC
    panel happens to share one, FNS).
    """
    code = (test_code or "").strip()
    machine_map = mappings.MAPS.get(machine, {})

    if code in machine_map:
        param_id, st_id, st_name, abbrev, name = machine_map[code]
        return {"param_id": param_id, "abbrev": abbrev, "name": name,
                "method": "curated",
                "service_tarification_id": st_id,
                "service_tarification_name": st_name}

    return {"param_id": None, "abbrev": None, "name": None, "method": "none",
            "service_tarification_id": None, "service_tarification_name": None}
