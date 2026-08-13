import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cyanvision_worklist import protocol as cyan_protocol
from cyanvision_worklist.service import CyanVisionWorklistService
from labo_bridge.protocols import hl7_mllp
from selectra_host_query import protocol as selectra_protocol
from selectra_host_query.app import create_app
from selectra_host_query.order_api_auth import load_or_create_order_api_token
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore


TOKEN = "integration-test-token"
HEADERS = {"X-API-TOKEN": TOKEN}

SELECTRA_ORDER = {
    "external_order_id": "EXT-SEL-001",
    "sample_id": "SEL-API-001",
    "patient_id": "PAT-001",
    "family_name": "BENCH",
    "given_name": "PATIENT",
    "birth_date": "1980-06-15",
    "sex": "F",
    "specimen_type": "SERUM",
    "tests": ["Creatinine", "SGPT"],
}

CYAN_ORDER = {
    "external_order_id": "EXT-CYAN-001",
    "sample_id": "CYAN-API-001",
    "given_name": "BENCH",
    "family_name": "PATIENT",
    "birth_date": "1980-06-15",
    "sex": "F",
    "test_code": "ALP",
}

QUERY = [
    "MSH|^~\\&|CYPRESS|CYANVISION|||||QRY^Q02|QUERY-API|P|2.3.1",
    "QRD|20070723170000|R|D|1|||RD||OTH|||T|",
    "QRF|CyanVision|20070723000000|20070723170000|||RCT|COR|ALL||",
]


class FakeConnection:
    def __init__(self, replies=b""):
        self.replies = bytearray(replies)
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if not self.replies:
            return b""
        data = bytes(self.replies[:size])
        del self.replies[:size]
        return data


def unframe(payload):
    messages = list(hl7_mllp.iter_messages(payload))
    assert len(messages) == 1
    return hl7_mllp.split_segments(messages[0][0])


class OrderApiCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "orders.db")
        self.store = BenchStore(self.db_path)
        self.selectra = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        self.cyan = CyanVisionWorklistService(self.store, port=6004)
        self.client = create_app(
            self.store, self.selectra, self.cyan, order_api_token=TOKEN,
        ).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_local_order_api_token_is_generated_once_and_reused(self):
        token_path = os.path.join(self.temp.name, "runtime", "order_api_token.txt")
        first = load_or_create_order_api_token(token_path)
        second = load_or_create_order_api_token(token_path)

        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(second, first)
        with open(token_path, encoding="ascii") as token_file:
            self.assertEqual(token_file.read().strip(), first)

    def test_order_api_requires_a_separate_token(self):
        missing = self.client.post("/api/v1/orders/selectra", json=SELECTRA_ORDER)
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.post(
            "/api/v1/orders/selectra", json=SELECTRA_ORDER,
            headers={"X-API-TOKEN": "wrong"},
        )
        self.assertEqual(wrong.status_code, 401)
        disabled = create_app(
            self.store, self.selectra, self.cyan, order_api_token="",
        ).test_client().post(
            "/api/v1/orders/selectra", json=SELECTRA_ORDER, headers=HEADERS,
        )
        self.assertEqual(disabled.status_code, 503)

    def test_existing_selectra_database_is_migrated_without_losing_orders(self):
        legacy_path = os.path.join(self.temp.name, "legacy.db")
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE orders (
                sample_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL,
                family_name TEXT NOT NULL, given_name TEXT NOT NULL,
                birth_date TEXT NOT NULL DEFAULT '', sex TEXT NOT NULL DEFAULT 'U',
                specimen_type TEXT NOT NULL DEFAULT 'SERUM', tests_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'staged', query_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_query_at TEXT, last_delivery_at TEXT, last_error TEXT
            );
            INSERT INTO orders VALUES (
                'LEGACY-001','P1','OLD','PATIENT','1980-01-01','F','SERUM',
                '["Creatinine"]','staged',0,'2026-01-01','2026-01-01',NULL,NULL,NULL
            );
            """
        )
        connection.commit()
        connection.close()
        migrated = BenchStore(legacy_path).get_order("LEGACY-001")
        self.assertEqual(migrated["source"], "manual")
        self.assertFalse(migrated["ready"])
        self.assertEqual(migrated["tests"], ["Creatinine"])

    def test_selectra_api_order_requires_per_order_manual_arming(self):
        staged = self.client.post(
            "/api/v1/orders/selectra", json=SELECTRA_ORDER, headers=HEADERS,
        )
        self.assertEqual(staged.status_code, 201)
        self.assertEqual(staged.get_json(), {
            "ok": True,
            "analyzer": "selectra",
            "sample_id": "SEL-API-001",
            "state": "staged",
        })
        stored = self.store.get_order("SEL-API-001")
        self.assertFalse(stored["ready"])
        self.assertEqual(stored["source"], "api")

        restarted_store = BenchStore(self.db_path)
        restarted_service = SelectraHostQueryServer(
            restarted_store, armed=False, embedded=True,
        )
        blocked = FakeConnection(selectra_protocol.B_ACK * 2)
        restarted_service.handle_records(
            blocked, ["Q|1|^SEL-API-001||ALL||||||||0"],
        )
        self.assertEqual(blocked.sent, [])

        armed = self.client.post(
            "/api/orders/SEL-API-001/arm",
            json={"confirmation": "ARM SELECTRA ORDER"},
        )
        self.assertEqual(armed.status_code, 200)
        self.assertTrue(armed.get_json()["armed"])

        connection = FakeConnection(selectra_protocol.B_ACK * 2)
        restarted_service.handle_records(
            connection, ["Q|1|^SEL-API-001||ALL||||||||0"],
        )
        self.assertEqual(connection.sent[0], selectra_protocol.B_ENQ)
        self.assertEqual(connection.sent[-1], selectra_protocol.B_EOT)
        delivered = restarted_store.get_order("SEL-API-001")
        self.assertFalse(delivered["ready"])
        self.assertEqual(delivered["status"], "transport_acknowledged")

        # Even if the manual bench is globally armed later, a consumed API
        # order must never be sent a second time.
        restarted_service.set_armed(True)
        duplicate = FakeConnection(selectra_protocol.B_ACK * 2)
        restarted_service.handle_records(
            duplicate, ["Q|1|^SEL-API-001||ALL||||||||0"],
        )
        self.assertEqual(duplicate.sent, [])

    def test_selectra_api_resolves_clinic_ids_to_machine_codes(self):
        order = {
            **SELECTRA_ORDER,
            "sample_id": "SEL-ID-001",
            "tests": [
                {"service_tarification_id": 392},
                {"param_id": 99953, "service_tarification_id": 528},
                {"service_tarification_id": 481},
            ],
        }
        response = self.client.post(
            "/api/v1/orders/selectra", json=order, headers=HEADERS,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload, {
            "ok": True,
            "analyzer": "selectra",
            "sample_id": "SEL-ID-001",
            "state": "staged",
        })
        stored = self.store.get_order("SEL-ID-001")
        self.assertEqual(stored["tests"], [
            "Creatinine", "SGPT", "Phosphatase ALP",
        ])
        self.assertIn(
            "^^^Crea\\^^^SGPT\\^^^ALP",
            selectra_protocol.build_order_records(stored)[2],
        )

    def test_selectra_api_rejects_ambiguous_or_unknown_clinic_ids(self):
        ambiguous = self.client.post(
            "/api/v1/orders/selectra",
            json={**SELECTRA_ORDER, "tests": [{"service_tarification_id": 528}]},
            headers=HEADERS,
        )
        self.assertEqual(ambiguous.status_code, 400)
        self.assertIn("ambiguous", ambiguous.get_json()["error"])

        unknown = self.client.post(
            "/api/v1/orders/selectra",
            json={**SELECTRA_ORDER, "tests": [{"param_id": 123456789}]},
            headers=HEADERS,
        )
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("no curated", unknown.get_json()["error"])

    @patch("selectra_host_query.app.pg.list_observed_test_codes", return_value=[])
    def test_cyanvision_api_queue_uses_documented_dsr_ack_continuation(self, _observed):
        first = self.client.post(
            "/api/v1/orders/cyanvision", json=CYAN_ORDER, headers=HEADERS,
        )
        second_order = {
            **CYAN_ORDER,
            "external_order_id": "EXT-CYAN-002",
            "sample_id": "CYAN-API-002",
            "test_code": "CRE",
        }
        second = self.client.post(
            "/api/v1/orders/cyanvision", json=second_order, headers=HEADERS,
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

        restarted_store = BenchStore(self.db_path)
        restarted_service = CyanVisionWorklistService(restarted_store, port=6004)
        connection = FakeConnection()
        restarted_service.handle_message(connection, QUERY)
        first_dsr = unframe(connection.sent[0])
        self.assertIn("DSP|1||CYAN-API-001|||", first_dsr)
        self.assertEqual(first_dsr[-1], "DSC|CYAN-API-002|")

        first_id = cyan_protocol.control_id(first_dsr)
        restarted_service.handle_message(connection, [
            "MSH|^~\\&|CYPRESS|CYANVISION|||||ACK^Q03|ACK-1|P|2.3.1",
            f"MSA|AA|{first_id}|Message accepted|||0|",
        ])
        second_dsr = unframe(connection.sent[1])
        self.assertIn("DSP|1||CYAN-API-002|||", second_dsr)
        self.assertEqual(second_dsr[-1], "DSC||")

        second_id = cyan_protocol.control_id(second_dsr)
        restarted_service.handle_message(connection, [
            "MSH|^~\\&|CYPRESS|CYANVISION|||||ACK^Q03|ACK-2|P|2.3.1",
            f"MSA|AA|{second_id}|Message accepted|||0|",
        ])
        self.assertFalse(restarted_store.get_cyanvision_order("CYAN-API-001")["ready"])
        self.assertFalse(restarted_store.get_cyanvision_order("CYAN-API-002")["ready"])

    @patch("selectra_host_query.app.pg.list_observed_test_codes", return_value=[])
    def test_cyanvision_api_resolves_clinic_ids_to_program_code(self, _observed):
        response = self.client.post(
            "/api/v1/orders/cyanvision",
            json={
                key: value for key, value in CYAN_ORDER.items()
                if key != "test_code"
            } | {"sample_id": "CYAN-ID-001", "test": {"service_tarification_id": 481}},
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload, {
            "ok": True,
            "analyzer": "cyanvision",
            "sample_id": "CYAN-ID-001",
            "state": "ready",
        })
        self.assertEqual(
            self.store.get_cyanvision_order("CYAN-ID-001")["test_code"], "ALP",
        )

    @patch("selectra_host_query.app.pg.list_observed_test_codes", return_value=[])
    def test_api_status_and_cancellation(self, _observed):
        self.client.post("/api/v1/orders/selectra", json=SELECTRA_ORDER, headers=HEADERS)
        selected = self.client.get(
            "/api/v1/orders/selectra/SEL-API-001", headers=HEADERS,
        )
        self.assertEqual(selected.status_code, 200)
        cancelled = self.client.delete(
            "/api/v1/orders/selectra/SEL-API-001", headers=HEADERS,
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(self.store.get_order("SEL-API-001")["ready"])

        self.client.post("/api/v1/orders/cyanvision", json=CYAN_ORDER, headers=HEADERS)
        cyan_cancelled = self.client.delete(
            "/api/v1/orders/cyanvision/CYAN-API-001", headers=HEADERS,
        )
        self.assertEqual(cyan_cancelled.status_code, 200)
        self.assertFalse(self.store.get_cyanvision_order("CYAN-API-001")["ready"])

    def test_console_can_disarm_and_remove_a_staged_api_order(self):
        self.client.post(
            "/api/v1/orders/selectra", json=SELECTRA_ORDER, headers=HEADERS,
        )
        missing_confirmation = self.client.post("/api/orders/SEL-API-001/arm", json={})
        self.assertEqual(missing_confirmation.status_code, 400)

        self.client.post(
            "/api/orders/SEL-API-001/arm",
            json={"confirmation": "ARM SELECTRA ORDER"},
        )
        disarmed = self.client.delete("/api/orders/SEL-API-001/arm")
        self.assertEqual(disarmed.status_code, 200)
        self.assertFalse(self.store.get_order("SEL-API-001")["ready"])

        removed = self.client.delete("/api/orders/SEL-API-001")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.store.get_order("SEL-API-001")["status"], "cancelled")
        visible = self.client.get("/api/orders").get_json()["orders"]
        self.assertNotIn("SEL-API-001", [order["sample_id"] for order in visible])


if __name__ == "__main__":
    unittest.main()
