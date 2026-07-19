"""
Small runtime switches for labo_bridge. Kept in one place so behavior can be
changed without hunting through server.py/pg.py/api_client.py.
"""

# Once the coworker's clinic API (see API_LABO_MACHINE_RESULT.md) is
# confirmed live, flip this to True. server.py then sends matched results
# to api_client.write_matched_result() instead of pg.write_matched_result()
# (our temporary labo_bridge.labo_bridge_results staging table). Both code
# paths exist and are ready - this is the only line that needs to change.
USE_MACHINE_RESULT_API = False

API_TIMEOUT_SECONDS = 5
