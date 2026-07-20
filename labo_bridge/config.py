"""
Small runtime switches for labo_bridge. Kept in one place so behavior can be
changed without hunting through server.py/pg.py/api_client.py.
"""

# Once the coworker's clinic API (see API_LABO_MACHINE_RESULT.md) is
# confirmed live, flip this to True. server.py then queues matched results
# onto each session's api_batch and sends them via api_client.send_batch()
# (one combined JSON array per batch/message) instead of writing them one row
# at a time to pg.write_matched_result() (our temporary
# labo_bridge.labo_bridge_results staging table). Both code paths exist and
# are ready - this is the only line that needs to change.
#
# Currently False: we work locally against Postgres only (labo_bridge schema),
# not the coworker's API yet. Matched results go to pg.write_matched_result(),
# unmatched go to pg.write_pending_result() - Postgres is the only store
# either way (no SQLite fallback). When flipped True, matched results are
# sent to the clinic API instead of the staging table; if the API call fails
# they are NOT persisted anywhere (by design - the API is the source of truth
# once live, not a downstream push from a local cache).
USE_MACHINE_RESULT_API = True

API_TIMEOUT_SECONDS = 5
