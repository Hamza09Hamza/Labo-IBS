import os
import tempfile
import unittest

from labo_bridge.protocols import astm
from selectra_host_query.app import create_app
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore
from xn330_order_download import protocol
from xn330_order_download.service import XN330OrderDownloadService


ORDER = {
    "sample_id": "XN-DEMO-001",
    "patient_id": "PAT-001",
    "given_name": "MANEL",
    "family_name": "PATIENT",
    "birth_date": "1980-06-15",
    "sex": "F",
    "tests": list(protocol.ORDERABLE_TESTS),
}


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


class XN330OrderDownloadCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BenchStore(os.path.join(self.temp.name, "orders.db"))
        self.service = XN330OrderDownloadService(self.store)
        self.store.upsert_xn330_order(ORDER)

    def tearDown(self):
        self.temp.cleanup()

    def test_query_extracts_sample_and_preserves_selector(self):
        query = "Q|1|2^2^ XN-DEMO-001^B||||20260816120000||||||N"
        details = protocol.query_details(query)
        self.assertEqual(details["sample_id"], "XN-DEMO-001")
        self.assertEqual(details["selector"], "2^2^ XN-DEMO-001^B")

    def test_response_uses_xn_patient_name_tests_and_report_type(self):
        selector = "2^2^ XN-DEMO-001^B"
        records = protocol.build_order_records(ORDER, selector)
        self.assertEqual([record[0] for record in records], ["H", "P", "O", "L"])
        patient = records[1].split("|")
        self.assertEqual(patient[4], "PAT-001")
        self.assertEqual(patient[5], "^MANEL^PATIENT")
        self.assertEqual(patient[7], "19800615")
        self.assertEqual(patient[8], "F")
        order = records[2].split("|")
        # O-2 (Specimen ID) stays blank; O-3 (Instrument Specimen ID) carries
        # the full selector - matches this analyzer's own real O records
        # (see protocol.build_order_records for the captured evidence).
        self.assertEqual(order[2], "")
        self.assertEqual(order[3], selector)
        self.assertIn("^^^^WBC", order[4])
        self.assertIn("^^^^NEUT%", order[4])
        self.assertEqual(order[11], "N")
        self.assertEqual(order[25], "Q")
        self.assertEqual(records[-1], "L|1|N")

    def test_large_order_is_split_into_bounded_astm_frames(self):
        frames = protocol.build_message_frames(protocol.build_order_records(ORDER))
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(len(frame) < 240 for frame in frames))
        self.assertIn(bytes([astm.ETB]), frames[0])
        self.assertIn(bytes([astm.ETX]), frames[-1])

    def test_unarmed_match_is_logged_but_sends_nothing(self):
        connection = FakeConnection()
        self.service.handle_records(
            connection,
            ["H|\\^&|||XN-330", "Q|1|^^XN-DEMO-001^M", "L|1|N"],
        )
        self.assertEqual(connection.sent, [])
        self.assertEqual(self.store.get_xn330_order("XN-DEMO-001")["status"], "queried")
        self.assertTrue(any(
            event["kind"] == "xn330_response_blocked" for event in self.store.list_events()
        ))

    def test_armed_exact_match_sends_once_then_disarms(self):
        self.store.set_xn330_order_ready("XN-DEMO-001", True)
        frame_count = len(protocol.build_message_frames(protocol.build_order_records(ORDER)))
        connection = FakeConnection(astm.B_ACK * (1 + frame_count))
        self.service.handle_records(
            connection,
            ["H|\\^&|||XN-330", "Q|1|2^2^XN-DEMO-001^B", "L|1|N"],
        )
        self.assertEqual(connection.sent[0], astm.B_ENQ)
        self.assertEqual(connection.sent[-1], astm.B_EOT)
        saved = self.store.get_xn330_order("XN-DEMO-001")
        self.assertFalse(saved["ready"])
        self.assertEqual(saved["status"], "transport_acknowledged")

        duplicate = FakeConnection(astm.B_ACK * (1 + frame_count))
        self.service.handle_records(duplicate, ["Q|1|^^XN-DEMO-001^M"])
        self.assertEqual(duplicate.sent, [])

    def test_portal_stages_then_requires_explicit_manual_arm(self):
        selectra = SelectraHostQueryServer(self.store, embedded=True)
        client = create_app(self.store, selectra, xn330_service=self.service).test_client()
        payload = {**ORDER, "sample_id": "XN-PORTAL-001"}
        staged = client.post("/api/xn330/orders", json=payload)
        self.assertEqual(staged.status_code, 201)
        self.assertFalse(staged.get_json()["order"]["ready"])

        rejected = client.post("/api/xn330/orders/XN-PORTAL-001/arm", json={})
        self.assertEqual(rejected.status_code, 400)
        armed = client.post(
            "/api/xn330/orders/XN-PORTAL-001/arm",
            json={"confirmation": "ARM XN330 ORDER"},
        )
        self.assertEqual(armed.status_code, 200)
        self.assertTrue(self.store.get_xn330_order("XN-PORTAL-001")["ready"])

    def test_authenticated_api_stages_fns_unarmed(self):
        selectra = SelectraHostQueryServer(self.store, embedded=True)
        client = create_app(
            self.store, selectra, xn330_service=self.service,
            order_api_token="xn-api-token",
        ).test_client()
        payload = {
            **ORDER,
            "sample_id": "XN-API-001",
            "external_order_id": "FNS-001",
            "tests": [{"service_tarification_id": 421}],
        }
        response = client.post(
            "/api/v1/orders/xn330", json=payload,
            headers={"X-API-TOKEN": "xn-api-token"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {
            "ok": True, "analyzer": "xn330",
            "sample_id": "XN-API-001", "state": "staged",
        })
        saved = self.store.get_xn330_order("XN-API-001")
        self.assertFalse(saved["ready"])
        self.assertIn("WBC", saved["tests"])
        self.assertIn("NEUT%", saved["tests"])


if __name__ == "__main__":
    unittest.main()
