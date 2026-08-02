"""
Match an incoming machine test code to a clinic labo_param.

Deliberately conservative: ONLY the curated per-machine maps in mappings.py
count as a match. Anything else returns method='none' and is staged as
'pending' for a human. No fuzzy matching, no guessing on clinical data.

Each mappings.py entry is a LIST of (param_id, service_tarification_id,
service_tarification_name, abbrev, name) tuples - usually just one, but a
single machine code can genuinely correspond to more than one clinic
param/exam (e.g. a result the clinic files under two different historical
exam entries for what's physically the same measurement). The machine only
ever sends one raw result; match_all() surfaces every candidate target so
that decision can be made explicitly (by a human, or by clinic-side logic)
rather than the bridge silently picking one.
"""

from . import mappings


def match_all(machine: str, test_code: str) -> list:
    """
    Return a list of candidate matches, one dict per curated target:
    {'param_id', 'abbrev', 'name', 'method',
     'service_tarification_id', 'service_tarification_name'}.
    Empty list when the code isn't in the curated map at all (method='none'
    territory - caller should treat an empty list as unmatched/pending).
    """
    code = (test_code or "").strip()
    machine_map = mappings.MAPS.get(machine, {})
    targets = machine_map.get(code)
    if not targets:
        return []

    return [
        {"param_id": param_id, "abbrev": abbrev, "name": name,
         "method": "curated",
         "service_tarification_id": st_id,
         "service_tarification_name": st_name}
        for (param_id, st_id, st_name, abbrev, name) in targets
    ]


def match(machine: str, test_code: str) -> dict:
    """
    Single-match convenience wrapper over match_all(): returns the FIRST
    candidate target, or the unmatched/'none' shape if there isn't one.
    Kept for every existing caller that only ever expected one match per
    result (server.py's write/send path) - none of them are multi-match
    aware yet, so this preserves today's behavior unchanged. New code that
    needs to see every candidate should call match_all() directly.
    """
    candidates = match_all(machine, test_code)
    if candidates:
        return candidates[0]

    return {"param_id": None, "abbrev": None, "name": None, "method": "none",
            "service_tarification_id": None, "service_tarification_name": None}
