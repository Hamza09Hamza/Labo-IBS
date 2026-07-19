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
    fields None). service_tarification_* comes from mappings.SERVICE_TARIFICATION
    when that machine's whole map is known to belong to one exam; otherwise None.
    """
    code = (test_code or "").strip()
    machine_map = mappings.MAPS.get(machine, {})
    st_id, st_name = mappings.SERVICE_TARIFICATION.get(machine, (None, None))

    if code in machine_map:
        param_id, abbrev, name = machine_map[code]
        return {"param_id": param_id, "abbrev": abbrev, "name": name,
                "method": "curated",
                "service_tarification_id": st_id,
                "service_tarification_name": st_name}

    return {"param_id": None, "abbrev": None, "name": None, "method": "none",
            "service_tarification_id": None, "service_tarification_name": None}
