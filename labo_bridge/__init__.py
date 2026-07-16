"""
labo_bridge - shared library for capturing lab analyzer results over Ethernet
and staging them against the clinic DB's labo_param dictionary.

Each analyzer speaks a different protocol (ASTM E1394, LIS2-A, HL7/MLLP), but
they all funnel through the same pipeline:

    raw bytes -> protocol framing -> per-machine decoder -> normalized result
              -> storage (SQLite) -> matcher (curated map) -> staging queue

The bridge NEVER writes to the Postgres clinic DB. It only matches incoming
machine codes to existing labo_param.id values (baked into mappings.py) and
stages the pairing locally for a human to review/approve later.
"""

__all__ = ["protocols", "decoders", "storage", "matcher", "mappings", "server"]
