import os
import tempfile
import unittest

from selectra_host_query import protocol
from selectra_host_query.app import create_app
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore


ORDER = {
    "sample_id": "HQ-DEMO-001",
    "patient_id": "P-DEMO-001",
    "family_name": "BENCH",
    "given_name": "PATIENT",
    "birth_date": "1980-06-15",
    "sex": "U",
    "specimen_type": "SERUM",
    "tests": ["Glucose pap sl", "Creatinine"],
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


class BenchCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BenchStore(os.path.join(self.temp.name, "bench.db"))
        self.store.upsert_order(ORDER)

    def tearDown(self):
        self.temp.cleanup()

    def test_astm_frame_round_trip_and_checksum_rejection(self):
        frame = protocol.build_frame(1, "Q|1|^HQ-DEMO-001^")
        self.assertEqual(protocol.decode_frame(frame), "Q|1|^HQ-DEMO-001^")
        damaged = frame[:5] + b"X" + frame[6:]
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            protocol.decode_frame(damaged)

    def test_query_candidates_and_exact_store_match(self):
        candidates = protocol.query_candidates("Q|1|^HQ-DEMO-001^")
        self.assertIn("HQ-DEMO-001", candidates)
        self.assertEqual(self.store.resolve_candidates(candidates)["sample_id"], "HQ-DEMO-001")
        self.assertIsNone(self.store.resolve_candidates(["HQ-DEMO", "001"]))

    def test_order_records_contain_demographics_sample_and_tests(self):
        records = protocol.build_order_records(ORDER)
        self.assertEqual([record[0] for record in records], ["H", "P", "O", "L"])
        self.assertIn("P-DEMO-001", records[1])
        self.assertIn("BENCH^PATIENT", records[1])
        self.assertIn("HQ-DEMO-001", records[2])
        # Universal test ID field (O record field [4]) is left blank: every
        # real O record this Selectra has been captured sending on its own
        # leaves this field empty, even when reporting a specific completed
        # test result (see protocol.build_order_records for the evidence).
        self.assertEqual(records[2].split("|")[4], "")

    def test_disarmed_query_builds_but_sends_nothing(self):
        service = SelectraHostQueryServer(self.store, armed=False)
        connection = FakeConnection()
        service._handle_records(connection, ["Q|1|^HQ-DEMO-001^"])
        self.assertEqual(connection.sent, [])
        events = self.store.list_events()
        self.assertTrue(any(event["kind"] == "response_blocked" for event in events))

    def test_armed_unknown_id_sends_no_patient_or_order_data(self):
        service = SelectraHostQueryServer(self.store, armed=True)
        connection = FakeConnection()
        service.handle_records(connection, ["Q|1|^UNKNOWN||ALL||||||||0"])
        self.assertEqual(connection.sent, [])
        events = self.store.list_events()
        self.assertTrue(any(event["kind"] == "query_unmatched" for event in events))

    def test_armed_query_sends_one_variant_and_advances_on_retry(self):
        variants = protocol.build_order_variants(ORDER)
        service = SelectraHostQueryServer(self.store, armed=True)

        # First query: exactly one ASTM transaction, using variant 0.
        connection = FakeConnection(bytes([protocol.ACK]) * 5)
        service._handle_records(connection, ["Q|1|^HQ-DEMO-001^"])
        self.assertEqual(connection.sent.count(protocol.B_ENQ), 1)
        self.assertEqual(connection.sent.count(protocol.B_EOT), 1)
        payloads = [protocol.decode_frame(frame) for frame in connection.sent[1:-1]]
        self.assertEqual([payload[0] for payload in payloads], [record[0] for record in variants[0][1]])
        self.assertEqual(self.store.get_order("HQ-DEMO-001")["status"], "delivered")

        # Retrying the same sample ID advances to the next variant.
        connection2 = FakeConnection(bytes([protocol.ACK]) * 5)
        service._handle_records(connection2, ["Q|1|^HQ-DEMO-001^"])
        payloads2 = [protocol.decode_frame(frame) for frame in connection2.sent[1:-1]]
        self.assertEqual([payload[0] for payload in payloads2], [record[0] for record in variants[1][1]])
        self.assertEqual(self.store.get_order("HQ-DEMO-001")["query_count"], 2)

    def test_api_stages_and_simulates_an_exact_id(self):
        service = SelectraHostQueryServer(self.store, armed=False)
        client = create_app(self.store, service).test_client()
        response = client.post("/api/orders", json={**ORDER, "sample_id": "HQ-DEMO-002"})
        self.assertEqual(response.status_code, 201)
        simulation = client.post("/api/simulate-query", json={"sample_id": "HQ-DEMO-002"})
        self.assertEqual(simulation.status_code, 200)
        self.assertEqual(simulation.get_json()["response_records"][-1], "L|1|N")
        missing = client.post("/api/simulate-query", json={"sample_id": "UNKNOWN"})
        self.assertEqual(missing.status_code, 404)

    def test_api_rejects_delimiters_but_auto_fills_empty_tests(self):
        service = SelectraHostQueryServer(self.store, armed=False)
        client = create_app(self.store, service).test_client()
        invalid = client.post("/api/orders", json={**ORDER, "sample_id": "BAD|ID"})
        self.assertEqual(invalid.status_code, 400)
        # Leaving tests empty no longer rejects the order: a random test is
        # auto-filled so an operator only has to type the sample ID.
        no_tests = client.post("/api/orders", json={**ORDER, "sample_id": "HQ-DEMO-003", "tests": []})
        self.assertEqual(no_tests.status_code, 201)
        self.assertEqual(len(no_tests.get_json()["order"]["tests"]), 1)

    def test_api_auto_fills_demographics_when_only_sample_id_given(self):
        service = SelectraHostQueryServer(self.store, armed=False)
        client = create_app(self.store, service).test_client()
        response = client.post("/api/orders", json={"sample_id": "HQ-DEMO-004"})
        self.assertEqual(response.status_code, 201)
        order = response.get_json()["order"]
        self.assertEqual(order["sample_id"], "HQ-DEMO-004")
        self.assertTrue(order["patient_id"])
        self.assertTrue(order["family_name"])
        self.assertTrue(order["given_name"])
        self.assertTrue(order["birth_date"])
        self.assertIn(order["sex"], {"M", "F"})
        self.assertEqual(len(order["tests"]), 1)

    def test_api_requires_explicit_confirmation_to_arm_real_replies(self):
        service = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        client = create_app(self.store, service).test_client()

        rejected = client.post("/api/live-responses", json={"armed": True})
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(service.armed)

        armed = client.post(
            "/api/live-responses",
            json={"armed": True, "confirmation": "ARM SELECTRA"},
        )
        self.assertEqual(armed.status_code, 200)
        self.assertTrue(service.armed)

        disarmed = client.post("/api/live-responses", json={"armed": False})
        self.assertEqual(disarmed.status_code, 200)
        self.assertFalse(service.armed)


if __name__ == "__main__":
    unittest.main()
