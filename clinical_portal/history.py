"""Periodic SQLite persistence of the rolling in-memory store.

``ClinicalStore`` (store.py) is intentionally memory-only and resets on
restart. This module ticks every ``interval`` seconds, reads each machine's
already-decoded, already-validated 10s window straight from the store (same
numbers the live UI shows - it never re-parses raw device bytes itself), and
appends one row per parameter that has data: the latest value plus that
window's mean/min/max/count. This is what the head doctor's "keep the 10s
values and the stats" request means in practice.

Each row also records ``machine_id`` - the physical device's own identifier,
independent of ``chamber_id``/``source`` (the slot it happened to be plugged
into at capture time). ``chamber_id``/``source`` describe *where* a reading
came from; ``machine_id`` describes *which device*. If a machine is ever
physically moved to a different block, old rows keep the correct machine_id
even though the block changes, and ``recent_by_machine_id`` can follow one
physical device across that move.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from clinical_portal.configuration import public_config
from clinical_portal.store import CHAMBERS, store as default_store


DB_PATH = os.path.join(os.path.dirname(__file__), "data", "history.db")
SNAPSHOT_WINDOW_SECONDS = 10


def _connect(db_path=DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS readings_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            chamber_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            machine_id TEXT NOT NULL DEFAULT '',
            code TEXT NOT NULL,
            label TEXT NOT NULL,
            unit TEXT NOT NULL,
            window_seconds INTEGER NOT NULL,
            latest_value REAL,
            latest_raw TEXT,
            valid INTEGER NOT NULL,
            mean REAL,
            min_value REAL,
            max_value REAL,
            count INTEGER NOT NULL
        )
        """
    )
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(readings_history)")}
    if "machine_id" not in existing_columns:
        connection.execute("ALTER TABLE readings_history ADD COLUMN machine_id TEXT NOT NULL DEFAULT ''")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_readings_history_lookup "
        "ON readings_history (chamber_id, source, code, captured_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_readings_history_machine "
        "ON readings_history (machine_id, code, captured_at)"
    )
    connection.commit()
    return connection


def _machine_ids_by_block_source():
    """{(chamber_id, source): machine_id} from the current configuration."""
    lookup = {}
    for block in public_config()["blocks"]:
        for source, machine in block["machines"].items():
            lookup[(block["id"], source)] = machine["machine_id"]
    return lookup


class HistoryRecorder:
    def __init__(self, store=default_store, db_path=DB_PATH):
        self.store = store
        self.db_path = db_path
        self._lock = threading.Lock()

    def snapshot_once(self):
        """Persist one row per parameter with data this window. Returns row count."""
        captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        machine_ids = _machine_ids_by_block_source()
        rows = []
        for chamber_id in CHAMBERS:
            try:
                snapshot = self.store.chamber(chamber_id, SNAPSHOT_WINDOW_SECONDS)
            except KeyError:
                continue
            for device in snapshot["devices"]:
                source = device["source"]
                machine_id = machine_ids.get((chamber_id, source), "")
                for parameter in device["parameters"]:
                    if parameter["count"] == 0 and parameter["latest"] is None:
                        continue
                    rows.append((
                        captured_at, chamber_id, source, machine_id, parameter["code"],
                        parameter["label"], parameter["unit"], SNAPSHOT_WINDOW_SECONDS,
                        parameter["latest"], parameter["latest_raw"], int(parameter["valid"]),
                        parameter["mean"], parameter["min"], parameter["max"], parameter["count"],
                    ))
        if not rows:
            return 0
        with self._lock:
            connection = _connect(self.db_path)
            try:
                connection.executemany(
                    """
                    INSERT INTO readings_history (
                        captured_at, chamber_id, source, machine_id, code, label, unit, window_seconds,
                        latest_value, latest_raw, valid, mean, min_value, max_value, count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                connection.commit()
            finally:
                connection.close()
        return len(rows)

    def recent(self, chamber_id: int, source: str, code: str | None = None, limit: int = 100):
        with self._lock:
            connection = _connect(self.db_path)
            try:
                connection.row_factory = sqlite3.Row
                if code:
                    cursor = connection.execute(
                        """
                        SELECT * FROM readings_history
                        WHERE chamber_id=? AND source=? AND code=?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (chamber_id, source, code, limit),
                    )
                else:
                    cursor = connection.execute(
                        """
                        SELECT * FROM readings_history
                        WHERE chamber_id=? AND source=?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (chamber_id, source, limit),
                    )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                connection.close()

    def recent_by_machine_id(self, machine_id: str, code: str | None = None, limit: int = 100):
        """History for one physical device, following it across any block moves."""
        with self._lock:
            connection = _connect(self.db_path)
            try:
                connection.row_factory = sqlite3.Row
                if code:
                    cursor = connection.execute(
                        """
                        SELECT * FROM readings_history
                        WHERE machine_id=? AND code=?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (machine_id, code, limit),
                    )
                else:
                    cursor = connection.execute(
                        """
                        SELECT * FROM readings_history
                        WHERE machine_id=?
                        ORDER BY id DESC LIMIT ?
                        """,
                        (machine_id, limit),
                    )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                connection.close()

    def run_loop(self, stop_event, interval=SNAPSHOT_WINDOW_SECONDS):
        while not stop_event.is_set():
            try:
                self.snapshot_once()
            except sqlite3.Error as exc:
                print(f"[clinical-history] snapshot failed: {exc}")
            stop_event.wait(interval)


recorder = HistoryRecorder()
