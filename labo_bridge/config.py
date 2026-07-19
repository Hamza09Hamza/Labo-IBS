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
# Set True on 2026-07-19 for pre-production validation: the API isn't live
# yet, so every POST will fail (connection refused), but api_client now logs
# the exact JSON payload it builds before attempting the send - this lets us
# visually check the request shape is correct ahead of the coworker's API
# going live. Matched results will NOT land in the pg staging table while
# this is True (they still stay captured in the local SQLite db either way -
# only the "downstream push" step is affected). Flip back to False to resume
# staging-table writes.
USE_MACHINE_RESULT_API = True

API_TIMEOUT_SECONDS = 5
