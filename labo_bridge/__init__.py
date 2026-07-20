"""
labo_bridge - shared library for capturing lab analyzer results over Ethernet
and matching them against the clinic DB's labo_param dictionary.

Each analyzer speaks a different protocol (ASTM E1394, LIS2-A, HL7/MLLP), but
they all funnel through the same pipeline:

    raw bytes -> protocol framing -> per-machine decoder -> normalized result
              -> matcher (curated map) -> Postgres (labo_bridge schema)

Postgres is the ONLY persistence layer (see pg.py) - confidently-matched
results go to labo_bridge.labo_bridge_results, and any test code with no
curated mapping yet goes to labo_bridge.pending_params: the mapping BACKLOG
(one row per unmapped code, not per result) surfaced for human review via
the admin UI. The bridge never writes to labo.labo_result directly - only to
its own labo_bridge schema tables, created specifically for this project.
"""

__all__ = ["protocols", "decoders", "pg", "matcher", "mappings", "server"]
