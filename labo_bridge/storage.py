"""
Unified local SQLite storage for all analyzers (replaces the per-machine
*.db files). Three tables:

  samples        - one row per sample/order captured
  results        - one row per measured result
  result_matches - the STAGING QUEUE: each result paired with the labo_param.id
                   the matcher believes it corresponds to, awaiting human review.
                   Nothing here is ever pushed to Postgres automatically.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                       "labo_bridge.db")
DB_PATH = os.path.abspath(DB_PATH)


def connect(db_path: str = None) -> sqlite3.Connection:
    # Read the module global at call time so tests can override DB_PATH.
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    _init(conn)
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            machine        TEXT,
            sample_id      TEXT,
            analyzer_model TEXT,
            patient_name   TEXT,
            patient_id     TEXT,
            source_ip      TEXT,
            received_at    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            machine      TEXT,
            sample_id    TEXT,
            test_code    TEXT,
            test_name    TEXT,
            value        TEXT,
            unit         TEXT,
            ref_range    TEXT,
            flag         TEXT,
            status       TEXT,
            received_at  TEXT,
            raw          TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS result_matches (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            machine          TEXT,
            sample_id        TEXT,
            test_code        TEXT,
            value            TEXT,
            unit             TEXT,
            matched_param_id INTEGER,
            matched_abbrev   TEXT,
            matched_name     TEXT,
            match_method     TEXT,      -- 'curated' | 'none'
            status           TEXT,      -- 'matched' | 'pending' | 'approved' | 'rejected'
            created_at       TEXT
        )
    """)
    conn.commit()


def store_sample(conn, machine, sample_id, analyzer_model, patient_name,
                 patient_id, source_ip):
    """Upsert a sample; clears any prior results/matches for the same key."""
    conn.execute("DELETE FROM results WHERE machine=? AND sample_id=?",
                 (machine, sample_id))
    conn.execute("DELETE FROM result_matches WHERE machine=? AND sample_id=?",
                 (machine, sample_id))
    conn.execute("DELETE FROM samples WHERE machine=? AND sample_id=?",
                 (machine, sample_id))
    conn.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                 (machine, sample_id, analyzer_model, patient_name, patient_id,
                  source_ip, datetime.now().isoformat()))
    conn.commit()


def store_result(conn, machine, sample_id, rec):
    conn.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (machine, sample_id, rec.get("test_code", ""),
                  rec.get("test_name", ""), rec.get("value", ""),
                  rec.get("unit", ""), rec.get("ref_range", ""),
                  rec.get("flag", ""), rec.get("status", ""),
                  datetime.now().isoformat(), rec.get("raw", "")))
    conn.commit()


def store_match(conn, machine, sample_id, rec, match):
    """`match` is the dict returned by matcher.match()."""
    conn.execute(
        "INSERT INTO result_matches "
        "(machine, sample_id, test_code, value, unit, matched_param_id, "
        " matched_abbrev, matched_name, match_method, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (machine, sample_id, rec.get("test_code", ""), rec.get("value", ""),
         rec.get("unit", ""), match.get("param_id"), match.get("abbrev"),
         match.get("name"), match.get("method"),
         "matched" if match.get("param_id") else "pending",
         datetime.now().isoformat()))
    conn.commit()
