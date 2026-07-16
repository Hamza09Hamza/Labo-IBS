"""
Per-machine decoders. Each decoder turns raw analyzer records into a stream of
normalized events with this contract:

  Header  -> {"kind": "header",  "analyzer_model": str, "timestamp": str}
  Patient -> {"kind": "patient", "patient_id": str, "patient_name": str}
  Order   -> {"kind": "order",   "sample_id": str, ...extra machine fields}
  Result  -> {"kind": "result",  "test_code": str, "test_name": str,
              "value": str, "unit": str, "ref_range": str,
              "flag": str, "status": str, "raw": str}

`test_code` is the stable code the matcher keys on (e.g. "WBC"); `test_name`
is the human label. Storage + matcher + query code are protocol-agnostic and
only ever see these dicts.
"""

# machine name -> module attribute, filled in by get_decoder()
_REGISTRY = {}


def register(name):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def get_decoder(machine):
    """Return the decode_record(line) or decode_segment(fields) callable."""
    if machine not in _REGISTRY:
        raise KeyError(f"no decoder registered for machine {machine!r} "
                       f"(have: {sorted(_REGISTRY)})")
    return _REGISTRY[machine]
