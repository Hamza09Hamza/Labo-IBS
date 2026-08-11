"""Private SQLite staging store for the Selectra Host Query bench."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class BenchStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    sample_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    family_name TEXT NOT NULL,
                    given_name TEXT NOT NULL,
                    birth_date TEXT NOT NULL DEFAULT '',
                    sex TEXT NOT NULL DEFAULT 'U',
                    specimen_type TEXT NOT NULL DEFAULT 'SERUM',
                    tests_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'staged',
                    query_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_query_at TEXT,
                    last_delivery_at TEXT,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sample_id TEXT,
                    message TEXT NOT NULL,
                    raw_text TEXT NOT NULL DEFAULT ''
                );
                """
            )

    @staticmethod
    def _order(row):
        if row is None:
            return None
        value = dict(row)
        value["tests"] = json.loads(value.pop("tests_json"))
        return value

    def upsert_order(self, order: dict) -> dict:
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO orders
                    (sample_id, patient_id, family_name, given_name, birth_date,
                     sex, specimen_type, tests_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?)
                ON CONFLICT(sample_id) DO UPDATE SET
                    patient_id=excluded.patient_id,
                    family_name=excluded.family_name,
                    given_name=excluded.given_name,
                    birth_date=excluded.birth_date,
                    sex=excluded.sex,
                    specimen_type=excluded.specimen_type,
                    tests_json=excluded.tests_json,
                    status='staged',
                    updated_at=excluded.updated_at,
                    last_error=NULL
                """,
                (
                    order["sample_id"], order["patient_id"], order["family_name"],
                    order["given_name"], order.get("birth_date", ""), order.get("sex", "U"),
                    order.get("specimen_type", "SERUM"), json.dumps(order["tests"], ensure_ascii=True),
                    now, now,
                ),
            )
        self.add_event("local", "order_staged", order["sample_id"],
                       f"Staged {len(order['tests'])} test(s) for exact sample ID {order['sample_id']}")
        return self.get_order(order["sample_id"])

    def get_order(self, sample_id: str):
        with self._session() as connection:
            row = connection.execute("SELECT * FROM orders WHERE sample_id=?", (sample_id,)).fetchone()
        return self._order(row)

    def resolve_candidates(self, candidates: list[str]):
        matches = []
        for candidate in candidates:
            order = self.get_order(candidate)
            if order:
                matches.append(order)
        unique = {order["sample_id"]: order for order in matches}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def list_orders(self, limit: int = 100):
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM orders ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._order(row) for row in rows]

    def mark_query(self, sample_id: str):
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                "UPDATE orders SET status='queried', query_count=query_count+1, last_query_at=?, updated_at=? WHERE sample_id=?",
                (now, now, sample_id),
            )

    def mark_delivered(self, sample_id: str, simulated: bool = False):
        now = utc_now()
        status = "simulated" if simulated else "delivered"
        with self._session() as connection:
            connection.execute(
                "UPDATE orders SET status=?, last_delivery_at=?, updated_at=?, last_error=NULL WHERE sample_id=?",
                (status, now, now, sample_id),
            )

    def mark_error(self, sample_id: str, message: str):
        with self._session() as connection:
            connection.execute(
                "UPDATE orders SET status='error', last_error=?, updated_at=? WHERE sample_id=?",
                (message, utc_now(), sample_id),
            )

    def add_event(self, direction: str, kind: str, sample_id: str | None, message: str, raw_text: str = ""):
        with self._session() as connection:
            cursor = connection.execute(
                "INSERT INTO events (created_at, direction, kind, sample_id, message, raw_text) VALUES (?, ?, ?, ?, ?, ?)",
                (utc_now(), direction, kind, sample_id, message, raw_text),
            )
            return cursor.lastrowid

    def list_events(self, after: int = 0, limit: int = 200):
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id DESC LIMIT ?", (after, limit)
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
