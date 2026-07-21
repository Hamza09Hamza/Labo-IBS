"""
Write path into the clinic Postgres DB - the ONLY persistence layer for this
project (local SQLite was retired; see git history for the earlier
labo_bridge.db-based design if needed).

Four tables in the `labo_bridge` schema (all created specifically for this
project - never labo.labo_result, per the coworker's request; their app
gets a proper API later to reconcile with real appointments/exams):
  labo_bridge.samples          - one row per sample/order captured
  labo_bridge.labo_bridge_results - confidently-matched results (curated map hit)
  labo_bridge.pending_params    - the mapping BACKLOG: one row per (machine,
                                   test_code) the analyzer has sent that has no
                                   curated mapping yet. This is NOT a result
                                   log - a code that's been seen 50 times still
                                   has exactly one row here (seen_count=50,
                                   example_value updated to the latest). Once a
                                   human maps the code in mappings.py, it's
                                   gone from here for good - every future
                                   result for that code goes straight to
                                   labo_bridge_results instead.
  labo_bridge.mappings          - a READ-ONLY MIRROR of mappings.py's MAPS
                                   dict, kept in sync by mappings_editor.py
                                   every time a mapping is added/edited/
                                   deleted. mappings.py is still the actual
                                   source of truth matcher.py reads from -
                                   this table exists purely so the curated
                                   mappings are visible/queryable in pgAdmin
                                   like everything else. Never write here
                                   directly; always go through
                                   mappings_editor.py so both stay in sync.
  labo_bridge.machine_config    - per-machine display/API settings (label,
                                   kind, protocol, port, color, photo,
                                   machine_id). This IS the source of truth
                                   (unlike mappings, nothing else defines
                                   these values) - it replaced editing
                                   app.py's MACHINE_META dict directly,
                                   because rewriting a live Python source
                                   file on every settings change was fragile
                                   (a save could hang for a long time
                                   waiting on the OS/antivirus to release
                                   the file handle, and a botched edit could
                                   leave the file with invalid syntax and
                                   crash the whole admin server on next
                                   reload - both actually happened). A
                                   plain UPDATE never has either problem.

Connection uses the same local Postgres server/credentials already set up
for this machine (pgpass.conf). If Postgres is unreachable, writes are
skipped with a printed warning rather than crashing the listener - but since
Postgres is now the only store, results genuinely aren't captured anywhere
while it's down (this is a deliberate tradeoff, not an oversight - keep
Postgres up when analyzers are actively sending results).
"""

import threading
import time

import psycopg2

PG_DSN = "host=localhost port=5432 dbname=clinic user=postgres"
CONNECT_TIMEOUT_SECONDS = 2

# After a failed connection attempt, don't retry for this long - callers like
# the admin UI's search-as-you-type hit _get_conn() on every keystroke, and
# without this a single unreachable-DB keystroke would try (and time out) a
# fresh TCP connect on every subsequent keystroke too, making the whole UI
# feel like it's hanging/buggy rather than just "DB is down".
RETRY_COOLDOWN_SECONDS = 15

# ONE psycopg2 connection object was previously shared across every thread
# (5 machine listener threads + the Flask admin thread). psycopg2 connections
# are NOT safe for concurrent use from multiple threads at once - if a
# listener thread was mid-query (writing a result from an actively-streaming
# analyzer) at the same moment the admin UI ran a query on that same
# connection, they collided and one side could block indefinitely. This is
# exactly why machine_id saves would hang only while a real machine was
# connected and sending data, and work fine in isolated testing. Fix: give
# each thread its own connection via threading.local() - every thread gets
# its own socket to Postgres, no shared mutable connection state, no
# blocking on another thread's in-flight query.
_local = threading.local()
_warned = False
_last_failure_at = 0.0


def _get_conn():
    global _warned, _last_failure_at
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception:
            _local.conn = None  # stale connection, reconnect below

    if time.monotonic() - _last_failure_at < RETRY_COOLDOWN_SECONDS:
        return None  # still in cooldown, don't hammer an unreachable host

    try:
        conn = psycopg2.connect(PG_DSN, connect_timeout=CONNECT_TIMEOUT_SECONDS)
        conn.autocommit = True
        _local.conn = conn
        _warned = False
        return conn
    except Exception as e:
        _last_failure_at = time.monotonic()
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


def write_sample(machine, sample_id, analyzer_model, patient_name, patient_id,
                 source_ip, paillasse=None):
    """
    Upsert sample-level metadata into labo_bridge.samples. Called once per
    ASTM O record / HL7 OBR segment - keyed on (machine, sample_id).
    Returns True on success, False if the write was skipped (PG unreachable).
    """
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO labo_bridge.samples
                    (machine, sample_id, analyzer_model, patient_name,
                     patient_id, source_ip, paillasse)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (machine, sample_id) DO UPDATE SET
                    analyzer_model = EXCLUDED.analyzer_model,
                    patient_name = EXCLUDED.patient_name,
                    patient_id = EXCLUDED.patient_id,
                    source_ip = EXCLUDED.source_ip,
                    paillasse = EXCLUDED.paillasse,
                    received_at = now()
                """,
                (machine, sample_id.strip(), analyzer_model, patient_name,
                 patient_id, source_ip, paillasse),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to write sample {machine}/{sample_id}: {e}")
        return False


def write_pending_param(machine, rec):
    """
    Upsert one unmapped test code into labo_bridge.pending_params - the
    mapping backlog (see pg.py's module docstring). Keyed on (machine,
    test_code): the first time a code is seen this creates the row: every
    later sighting of the SAME code just bumps seen_count and refreshes the
    example value/last_seen_at, it does NOT add a new row - sample identity
    is irrelevant here, this tracks unmapped CODES, not results.
    Returns True on success, False if the write was skipped (PG unreachable).
    """
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO labo_bridge.pending_params
                    (machine, test_code, test_name, example_value, example_unit, example_raw)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (machine, test_code) DO UPDATE SET
                    test_name = EXCLUDED.test_name,
                    example_value = EXCLUDED.example_value,
                    example_unit = EXCLUDED.example_unit,
                    example_raw = EXCLUDED.example_raw,
                    seen_count = labo_bridge.pending_params.seen_count + 1,
                    last_seen_at = now()
                """,
                (machine, rec.get("test_code", ""), rec.get("test_name", ""),
                 rec.get("value", ""), rec.get("unit", ""), rec.get("raw", "")),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to write pending param "
              f"{machine}/{rec.get('test_code','')}: {e}")
        return False


def write_matched_result(machine, sample_id, specimen, test_code, match, rec,
                         api_sent=False, api_result_id=None):
    """
    Insert one confidently-matched result into labo_bridge.labo_bridge_results.
    `specimen` is the dict from xn330.parse_specimen_id() (year/month/sequence),
    or {} if not applicable/unparseable. `match` is matcher.match()'s return
    value - caller must only call this when match['param_id'] is not None.

    Always called regardless of USE_MACHINE_RESULT_API - this table is the
    ONLY place the admin UI (sample detail, mapped-table "last value") can
    see matched results, so a result sent live to the clinic API still needs
    a local record or it's invisible everywhere in the UI. `api_sent`/
    `api_result_id` record whether/where it also went to the clinic, purely
    as history - they don't change matching logic.

    Replaces (not appends to) any existing row for this exact
    (machine, sample_id, test_code) - confirmed necessary via real Selectra
    captures (2026-07-21): short numeric sample IDs get physically reused
    for entirely separate, unrelated runs on different days, which used to
    pile up as multiple contradictory rows for the "same" sample (e.g. 4
    different Creatinine values for sample "589"). Keeping only the latest
    matches what a user checking a sample actually expects to see - a
    single current result per test, not an unlabeled mix of old and new.
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
                "DELETE FROM labo_bridge.labo_bridge_results "
                "WHERE machine = %s AND sample_id = %s AND test_code = %s",
                (machine, sample_id.strip(), test_code),
            )
            cur.execute(
                """
                INSERT INTO labo_bridge.labo_bridge_results
                    (machine, sample_id, specimen_year, specimen_month,
                     specimen_sequence, paillasse, paillasse_name, test_code,
                     param_id, param_abbrev, param_name, result_value, unit,
                     flag, service_tarification_id, service_tarification_name,
                     api_sent, api_result_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (machine, sample_id.strip(), specimen.get("year"),
                 specimen.get("month"), specimen.get("sequence"),
                 paillasse_code, paillasse_name, test_code, match["param_id"],
                 match["abbrev"], match["name"], rec.get("value", ""),
                 rec.get("unit", ""), rec.get("flag", ""),
                 match.get("service_tarification_id"),
                 match.get("service_tarification_name"),
                 api_sent, api_result_id),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to write matched result "
              f"{machine}/{sample_id}/{test_code}: {e}")
        return False


def mark_api_sent(machine, sample_id, test_code, api_result_id):
    """
    Update the local labo_bridge_results row already written for this result
    with the outcome of sending it to the clinic API (see server.py's
    _flush_api_batch, called right after api_client.send_batch returns).
    Only marks the MOST RECENT matching row (ORDER BY id DESC LIMIT 1) -
    the same code can legitimately appear more than once for a sample across
    retransmissions, and only the row from THIS send should be marked.
    """
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE labo_bridge.labo_bridge_results
                SET api_sent = TRUE, api_result_id = %s
                WHERE id = (
                    SELECT id FROM labo_bridge.labo_bridge_results
                    WHERE machine = %s AND sample_id = %s AND test_code = %s
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (api_result_id, machine, sample_id.strip(), test_code),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to mark api_sent for "
              f"{machine}/{sample_id}/{test_code}: {e}")
        return False


def clear_pending_param(machine, code):
    """
    Remove one (machine, code) row from labo_bridge.pending_params - called
    right after a mapping is added for that code (see mappings_editor.
    upsert_entry), since it's no longer "waiting to be mapped" once it has
    been. Without this, a mapped code would keep showing up in the Pending
    tab as if it still needed attention, even though the matcher now
    resolves it fine.
    """
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM labo_bridge.pending_params WHERE machine = %s AND test_code = %s",
                (machine, code),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to clear pending param {machine}/{code}: {e}")
        return False


def sync_mapping(machine, code, param_id, service_tarification_id,
                  service_tarification_name, abbrev, name):
    """
    Mirror one mappings.py entry into labo_bridge.mappings - called by
    mappings_editor.py right after it writes the same entry into the source
    file, so the two never drift apart. mappings.py stays authoritative
    (matcher.py reads it directly); this table exists only so mappings are
    visible/queryable in pgAdmin. Silently no-ops if PG is unreachable - the
    file write already succeeded, and this is a convenience mirror, not the
    source of truth, so it shouldn't block or fail the actual edit.
    """
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO labo_bridge.mappings
                    (machine, test_code, param_id, service_tarification_id,
                     service_tarification_name, abbrev, name)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (machine, test_code) DO UPDATE SET
                    param_id = EXCLUDED.param_id,
                    service_tarification_id = EXCLUDED.service_tarification_id,
                    service_tarification_name = EXCLUDED.service_tarification_name,
                    abbrev = EXCLUDED.abbrev,
                    name = EXCLUDED.name,
                    updated_at = now()
                """,
                (machine, code, param_id, service_tarification_id,
                 service_tarification_name, abbrev, name),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to sync mapping {machine}/{code} to labo_bridge.mappings: {e}")
        return False


def delete_mapping_sync(machine, code):
    """Remove one entry from labo_bridge.mappings - mirrors mappings_editor.delete_entry()."""
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM labo_bridge.mappings WHERE machine = %s AND test_code = %s",
                (machine, code),
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to delete mapping {machine}/{code} from labo_bridge.mappings: {e}")
        return False


_MACHINE_CONFIG_COLUMNS = ("machine", "label", "kind", "protocol", "port",
                          "color", "photo", "photo_bg", "machine_id")


def get_all_machine_configs():
    """
    Return {machine: {label, kind, protocol, port, color, photo, photo_bg,
    machine_id}} for every configured machine, or {} if PG is unreachable.
    The admin UI's /api/machines reads this instead of a hardcoded dict.
    """
    conn = _get_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(_MACHINE_CONFIG_COLUMNS)} FROM labo_bridge.machine_config")
            rows = cur.fetchall()
        return {row[0]: dict(zip(_MACHINE_CONFIG_COLUMNS[1:], row[1:])) for row in rows}
    except Exception as e:
        print(f"[pg] WARNING: failed to read labo_bridge.machine_config: {e}")
        return {}


def get_machine_config(machine):
    """Return one machine's config dict, or None if not found/PG unreachable."""
    return get_all_machine_configs().get(machine)


def upsert_machine_config(machine, label=None, kind=None, protocol=None, port=None,
                          color=None, photo=None, photo_bg=None, machine_id="__unset__"):
    """
    Insert or partially update one machine's row in labo_bridge.machine_config.
    Any field left at its default (None, or the machine_id sentinel) keeps
    its current DB value rather than being overwritten - callers only pass
    the fields they actually want to change (see api_put_machine_config /
    add_machine in admin/app.py). Returns True on success, False if the
    write was skipped (PG unreachable) or the machine doesn't exist yet for
    a partial update.
    """
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM labo_bridge.machine_config WHERE machine = %s", (machine,))
            exists = cur.fetchone() is not None

            if not exists:
                cur.execute(
                    """
                    INSERT INTO labo_bridge.machine_config
                        (machine, label, kind, protocol, port, color, photo, photo_bg, machine_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (machine, label or machine, kind or "", protocol or "", port or 0,
                     color or "#0C8599", photo, photo_bg or "transparent",
                     None if machine_id == "__unset__" else machine_id),
                )
                return True

            sets, params = [], []
            for col, val in (("label", label), ("kind", kind), ("protocol", protocol),
                             ("port", port), ("color", color), ("photo", photo),
                             ("photo_bg", photo_bg)):
                if val is not None:
                    sets.append(f"{col} = %s")
                    params.append(val)
            if machine_id != "__unset__":
                sets.append("machine_id = %s")
                params.append(machine_id)
            if not sets:
                return True
            sets.append("updated_at = now()")
            params.append(machine)
            cur.execute(
                f"UPDATE labo_bridge.machine_config SET {', '.join(sets)} WHERE machine = %s",
                params,
            )
        return True
    except Exception as e:
        print(f"[pg] WARNING: failed to write machine_config for {machine}: {e}")
        return False
