"""
Write path into the clinic Postgres DB - TEMPORARY staging table only.

This module writes ONLY to labo_bridge.labo_bridge_results, a table created
specifically for this project (not labo.labo_result - the coworker asked us
not to touch that table; their app will get a proper API later to reconcile
these with real appointments/exams).

Only results the matcher confidently mapped to a labo_param.id (curated
matches) are ever written here. Pending/unmatched results never reach
Postgres - they stay in the local labo_bridge.db SQLite staging queue only.

Connection uses the same local Postgres server/credentials already set up
for this machine (pgpass.conf). If Postgres is unreachable, writes are
skipped with a printed warning rather than crashing the listener - capturing
results from the analyzer must never depend on Postgres being up.
"""

import psycopg2

PG_DSN = "host=192.168.137.1 port=5432 dbname=clinic user=postgres"

_conn = None
_warned = False


def _get_conn():
    global _conn, _warned
    if _conn is not None:
        try:
            with _conn.cursor() as cur:
                cur.execute("SELECT 1")
            return _conn
        except Exception:
            _conn = None  # stale connection, reconnect below

    try:
        _conn = psycopg2.connect(PG_DSN)
        _conn.autocommit = True
        _warned = False
        return _conn
    except Exception as e:
        if not _warned:
            print(f"[pg] WARNING: could not connect to clinic Postgres DB ({e}). "
                  f"Matched results will stay in local SQLite only until PG is reachable.")
            _warned = True
        return None


_paillasse_names = None  # cache: {id: name}, loaded once from labo.labo_paillase


def _lookup_paillasse_name(paillasse_code):
    """
    Resolve a 2-digit paillasse code (e.g. "07") to its name in
    labo.labo_paillase (e.g. "AUTO IMMUNITE"). This is a live lookup, not
    hardcoded - labo_paillase is a small, stable reference table (15 rows,
    department names), unlike the ambiguous labo_param table. Returns None
    if unresolvable (code doesn't match any id, or PG unreachable) - never
    guesses.
    """
    global _paillasse_names
    if not paillasse_code:
        return None
    conn = _get_conn()
    if conn is None:
        return None

    if _paillasse_names is None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM labo.labo_paillase")
                _paillasse_names = {row[0]: row[1] for row in cur.fetchall()}
        except Exception as e:
            print(f"[pg] WARNING: could not load labo_paillase reference table: {e}")
            return None

    try:
        return _paillasse_names.get(int(paillasse_code))
    except ValueError:
        return None


def write_matched_result(machine, sample_id, specimen, test_code, match, rec):
    """
    Insert one confidently-matched result into labo_bridge.labo_bridge_results.
    `specimen` is the dict from xn330.parse_specimen_id() (year/month/sequence),
    or {} if not applicable/unparseable. `match` is matcher.match()'s return
    value - caller must only call this when match['param_id'] is not None.
    Returns True on success, False if the write was skipped (PG unreachable).
    """
    conn = _get_conn()
    if conn is None:
        return False

    paillasse_code = specimen.get("paillasse")
    paillasse_name = _lookup_paillasse_name(paillasse_code)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO labo_bridge.labo_bridge_results
                    (machine, sample_id, specimen_year, specimen_month,
                     specimen_sequence, paillasse, paillasse_name, test_code,
                     param_id, param_abbrev, param_name, result_value, unit,
                     flag, service_tarification_id, service_tarification_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (machine, sample_id.strip(), specimen.get("year"),
                 specimen.get("month"), specimen.get("sequence"),
                 paillasse_code, paillasse_name, test_code, match["param_id"],
                 match["abbrev"], match["name"], rec.get("value", ""),
                 rec.get("unit", ""), rec.get("flag", ""),
                 match.get("service_tarification_id"),
                 match.get("service_tarification_name")),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to write matched result "
              f"{machine}/{sample_id}/{test_code}: {e}")
        return False
