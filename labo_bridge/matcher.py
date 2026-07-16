"""
Match an incoming machine test code to a clinic labo_param.

Deliberately conservative: ONLY the curated per-machine maps in mappings.py
count as a match. Anything else returns method='none' and is staged as
'pending' for a human. No fuzzy matching, no guessing on clinical data.
"""

from . import mappings


def match(machine: str, test_code: str) -> dict:
    """
    Return {'param_id', 'abbrev', 'name', 'method'}.
    method is 'curated' when found in the verified map, else 'none'
    (param_id/abbrev/name are None).
    """
    code = (test_code or "").strip()
    machine_map = mappings.MAPS.get(machine, {})

    if code in machine_map:
        param_id, abbrev, name = machine_map[code]
        return {"param_id": param_id, "abbrev": abbrev, "name": name,
                "method": "curated"}

    return {"param_id": None, "abbrev": None, "name": None, "method": "none"}
