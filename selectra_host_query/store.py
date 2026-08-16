"""Durable SQLite order staging and protocol-event audit store."""

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
                    last_error TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    ready INTEGER NOT NULL DEFAULT 0,
                    external_order_id TEXT,
                    outbound_specimen_type TEXT NOT NULL DEFAULT '',
                    ordering_physician TEXT NOT NULL DEFAULT '',
                    comment TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS cyanvision_orders (
                    sample_id TEXT PRIMARY KEY,
                    given_name TEXT NOT NULL,
                    family_name TEXT NOT NULL,
                    birth_date TEXT NOT NULL,
                    sex TEXT NOT NULL,
                    test_code TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'staged',
                    source TEXT NOT NULL DEFAULT 'manual',
                    ready INTEGER NOT NULL DEFAULT 1,
                    external_order_id TEXT,
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
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Existing installations predate API-fed orders. SQLite's
            # CREATE TABLE IF NOT EXISTS does not add new columns, so apply
            # the small migration in place without deleting staged history.
            order_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            for column, definition in (
                ("source", "TEXT NOT NULL DEFAULT 'manual'"),
                ("ready", "INTEGER NOT NULL DEFAULT 0"),
                ("external_order_id", "TEXT"),
                ("outbound_specimen_type", "TEXT NOT NULL DEFAULT ''"),
                ("ordering_physician", "TEXT NOT NULL DEFAULT ''"),
                ("comment", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in order_columns:
                    connection.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

            # API orders created before per-order manual arming was introduced
            # were persisted as ready immediately. Disarm them once during the
            # upgrade so no legacy order can bypass the new operator approval.
            migration = "selectra_api_manual_arming_v1"
            already_applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE name=?", (migration,),
            ).fetchone()
            if not already_applied:
                connection.execute(
                    """
                    UPDATE orders
                    SET ready=0, status='staged', updated_at=?
                    WHERE source='api' AND ready=1
                      AND status IN ('staged', 'queried', 'error')
                    """,
                    (utc_now(),),
                )
                connection.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (migration, utc_now()),
                )

    @staticmethod
    def _order(row):
        if row is None:
            return None
        value = dict(row)
        value["tests"] = json.loads(value.pop("tests_json"))
        value["ready"] = bool(value.get("ready"))
        return value

    def upsert_order(self, order: dict, source="manual", ready=False) -> dict:
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO orders
                    (sample_id, patient_id, family_name, given_name, birth_date,
                     sex, specimen_type, tests_json, status, source, ready,
                     external_order_id, outbound_specimen_type,
                     ordering_physician, comment, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sample_id) DO UPDATE SET
                    patient_id=excluded.patient_id,
                    family_name=excluded.family_name,
                    given_name=excluded.given_name,
                    birth_date=excluded.birth_date,
                    sex=excluded.sex,
                    specimen_type=excluded.specimen_type,
                    tests_json=excluded.tests_json,
                    status='staged',
                    source=excluded.source,
                    ready=excluded.ready,
                    external_order_id=excluded.external_order_id,
                    outbound_specimen_type=excluded.outbound_specimen_type,
                    ordering_physician=excluded.ordering_physician,
                    comment=excluded.comment,
                    updated_at=excluded.updated_at,
                    last_error=NULL
                """,
                (
                    order["sample_id"], order["patient_id"], order["family_name"],
                    order["given_name"], order.get("birth_date", ""), order.get("sex", "U"),
                    order.get("specimen_type", "SERUM"), json.dumps(order["tests"], ensure_ascii=True),
                    source, int(bool(ready)), order.get("external_order_id"),
                    order.get("outbound_specimen_type", ""),
                    order.get("ordering_physician", ""), order.get("comment", ""),
                    now, now,
                ),
            )
        direction = "api" if source == "api" else "local"
        self.add_event(direction, "order_staged", order["sample_id"],
                       f"Staged {len(order['tests'])} test(s) for exact sample ID {order['sample_id']}"
                       + ("; API order is ready for Selectra" if ready else ""))
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

    def mark_transport_acknowledged(self, sample_id: str):
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                "UPDATE orders SET status=?, ready=0, last_delivery_at=?, updated_at=?, last_error=NULL WHERE sample_id=?",
                ("transport_acknowledged", now, now, sample_id),
            )

    def mark_rejected(self, sample_id: str, message: str):
        with self._session() as connection:
            connection.execute(
                "UPDATE orders SET status='rejected', last_error=?, updated_at=? WHERE sample_id=?",
                (message, utc_now(), sample_id),
            )

    def mark_error(self, sample_id: str, message: str):
        with self._session() as connection:
            connection.execute(
                "UPDATE orders SET status='error', last_error=?, updated_at=? WHERE sample_id=?",
                (message, utc_now(), sample_id),
            )

    def cancel_order(self, sample_id: str) -> bool:
        with self._session() as connection:
            cursor = connection.execute(
                "UPDATE orders SET status='cancelled', ready=0, updated_at=? WHERE sample_id=?",
                (utc_now(), sample_id),
            )
        return cursor.rowcount > 0

    def set_order_ready(self, sample_id: str, ready: bool):
        """Arm or disarm one API order without changing its clinical data."""
        with self._session() as connection:
            row = connection.execute(
                "SELECT status, source FROM orders WHERE sample_id=?", (sample_id,),
            ).fetchone()
            if row is None:
                return None
            if row["source"] != "api":
                raise ValueError("only orders received through the API use per-order arming")
            if ready and row["status"] not in {"staged", "queried", "error"}:
                raise ValueError(f"an order in state {row['status']} cannot be armed")
            connection.execute(
                """
                UPDATE orders
                SET ready=?, status=?, updated_at=?, last_error=NULL
                WHERE sample_id=?
                """,
                (int(bool(ready)), "staged" if ready or row["status"] == "queried" else row["status"],
                 utc_now(), sample_id),
            )
        return self.get_order(sample_id)

    def selectra_auto_arm_enabled(self) -> bool:
        with self._session() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE name='selectra_api_auto_arm'"
            ).fetchone()
        return bool(row and row["value"] == "1")

    def set_selectra_auto_arm(self, enabled: bool) -> int:
        """Persist API auto-arm and update all still-actionable API orders."""
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO settings (name, value, updated_at)
                VALUES ('selectra_api_auto_arm', ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at
                """,
                ("1" if enabled else "0", now),
            )
            if enabled:
                cursor = connection.execute(
                    """
                    UPDATE orders
                    SET ready=1, status='staged', updated_at=?, last_error=NULL
                    WHERE source='api' AND status IN ('staged', 'queried', 'error')
                    """,
                    (now,),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE orders
                    SET ready=0, updated_at=?
                    WHERE source='api' AND ready=1
                      AND status IN ('staged', 'queried', 'error')
                    """,
                    (now,),
                )
        return cursor.rowcount

    @staticmethod
    def _cyanvision_order(row):
        if row is None:
            return None
        value = dict(row)
        value["ready"] = bool(value["ready"])
        return value

    def upsert_cyanvision_order(self, order: dict, source="manual", ready=True) -> dict:
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO cyanvision_orders
                    (sample_id, given_name, family_name, birth_date, sex,
                     test_code, status, source, ready, external_order_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'staged', ?, ?, ?, ?, ?)
                ON CONFLICT(sample_id) DO UPDATE SET
                    given_name=excluded.given_name,
                    family_name=excluded.family_name,
                    birth_date=excluded.birth_date,
                    sex=excluded.sex,
                    test_code=excluded.test_code,
                    status='staged',
                    source=excluded.source,
                    ready=excluded.ready,
                    external_order_id=excluded.external_order_id,
                    updated_at=excluded.updated_at,
                    last_error=NULL
                """,
                (
                    order["sample_id"], order["given_name"], order["family_name"],
                    order["birth_date"], order["sex"], order["test_code"],
                    source, int(bool(ready)), order.get("external_order_id"), now, now,
                ),
            )
        direction = "api" if source == "api" else "local"
        self.add_event(
            direction, "cyanvision_order_staged", order["sample_id"],
            f"Staged CYANVision test {order['test_code']} for sample {order['sample_id']}"
            + ("; ready for Load from LIS" if ready else ""),
        )
        return self.get_cyanvision_order(order["sample_id"])

    def get_cyanvision_order(self, sample_id: str):
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM cyanvision_orders WHERE sample_id=?", (sample_id,),
            ).fetchone()
        return self._cyanvision_order(row)

    def list_ready_cyanvision_orders(self, limit=100):
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM cyanvision_orders
                WHERE ready=1
                ORDER BY created_at, sample_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._cyanvision_order(row) for row in rows]

    def mark_cyanvision_query(self, sample_id: str):
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                UPDATE cyanvision_orders
                SET status='queried', query_count=query_count+1,
                    last_query_at=?, updated_at=?
                WHERE sample_id=?
                """,
                (now, now, sample_id),
            )

    def mark_cyanvision_delivered(self, sample_id: str):
        now = utc_now()
        with self._session() as connection:
            connection.execute(
                """
                UPDATE cyanvision_orders
                SET status='acknowledged', ready=0, last_delivery_at=?,
                    updated_at=?, last_error=NULL
                WHERE sample_id=?
                """,
                (now, now, sample_id),
            )

    def mark_cyanvision_rejected(self, sample_id: str, message: str):
        with self._session() as connection:
            connection.execute(
                """
                UPDATE cyanvision_orders
                SET status='rejected', ready=0, last_error=?, updated_at=?
                WHERE sample_id=?
                """,
                (message, utc_now(), sample_id),
            )

    def cancel_cyanvision_order(self, sample_id: str) -> bool:
        with self._session() as connection:
            cursor = connection.execute(
                """
                UPDATE cyanvision_orders
                SET status='cancelled', ready=0, updated_at=?
                WHERE sample_id=?
                """,
                (utc_now(), sample_id),
            )
        return cursor.rowcount > 0

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
