import os
import tempfile
import unittest

from selectra_host_query import protocol
from selectra_host_query.app import create_app
from selectra_host_query.server import PROBE_PATIENT_NAME, SelectraHostQueryServer
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
        message = "H|\\^&\rQ|1|^HQ-DEMO-001^\rL|1|F"
        frame = protocol.build_frame(1, message)
        self.assertEqual(protocol.decode_frame(frame), message)
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
        self.assertIn("BENCH PATIENT", records[1])
        self.assertIn("HQ-DEMO-001", records[2])
        fields = records[2].split("|")
        self.assertEqual(fields[4], "^^^Gly\\^^^Crea")
        self.assertEqual(fields[5], "R")
        self.assertEqual(fields[11], "N")
        self.assertEqual(fields[25], "Q")
        self.assertIn("|||WINLAB|||||PROM||P|LIS2-A|", records[0])
        self.assertEqual(records[3], "L|1|F")

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

    def test_armed_query_sends_one_complete_message_frame(self):
        service = SelectraHostQueryServer(self.store, armed=True)
        connection = FakeConnection(bytes([protocol.ACK]) * 2)
        service._handle_records(connection, ["Q|1|^HQ-DEMO-001^"])
        self.assertEqual(len(connection.sent), 3)
        self.assertEqual(connection.sent[0], protocol.B_ENQ)
        self.assertEqual(connection.sent[-1], protocol.B_EOT)
        records = protocol.split_records(protocol.decode_frame(connection.sent[1]))
        self.assertEqual([record[0] for record in records], ["H", "P", "O", "L"])
        self.assertEqual(
            self.store.get_order("HQ-DEMO-001")["status"],
            "transport_acknowledged",
        )

    def test_api_stages_and_simulates_an_exact_id(self):
        service = SelectraHostQueryServer(self.store, armed=False)
        client = create_app(self.store, service).test_client()
        response = client.post("/api/orders", json={**ORDER, "sample_id": "HQ-DEMO-002"})
        self.assertEqual(response.status_code, 201)
        simulation = client.post("/api/simulate-query", json={"sample_id": "HQ-DEMO-002"})
        self.assertEqual(simulation.status_code, 200)
        self.assertEqual(simulation.get_json()["response_records"][-1], "L|1|F")
        simulated_order = self.store.get_order("HQ-DEMO-002")
        self.assertEqual(simulated_order["query_count"], 0)
        self.assertEqual(simulated_order["status"], "staged")
        missing = client.post("/api/simulate-query", json={"sample_id": "UNKNOWN"})
        self.assertEqual(missing.status_code, 404)

    def test_api_rejects_delimiters_but_auto_fills_empty_tests(self):
        service = SelectraHostQueryServer(self.store, armed=False)
        client = create_app(self.store, service).test_client()
        invalid = client.post("/api/orders", json={**ORDER, "sample_id": "BAD|ID"})
        self.assertEqual(invalid.status_code, 400)
        too_long = client.post("/api/orders", json={**ORDER, "sample_id": "1234567890123"})
        self.assertEqual(too_long.status_code, 400)
        unknown_test = client.post(
            "/api/orders", json={**ORDER, "sample_id": "HQ-UNKNOWN", "tests": ["NOT-INSTALLED"]}
        )
        self.assertEqual(unknown_test.status_code, 400)
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

    def test_continuous_probe_requires_confirmation_and_answers_every_unknown_q(self):
        self.assertEqual(PROBE_PATIENT_NAME, "APPELLE MANEL/FODHIL")
        self.assertEqual(len(PROBE_PATIENT_NAME), 20)
        service = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        client = create_app(self.store, service).test_client()
        rejected = client.post("/api/continuous-probe", json={"armed": True})
        self.assertEqual(rejected.status_code, 400)

        armed = client.post(
            "/api/continuous-probe",
            json={"armed": True, "confirmation": "ARM CONTINUOUS PROBE"},
        )
        self.assertEqual(armed.status_code, 200)
        self.assertTrue(armed.get_json()["probe_armed"])
        selected_tests = armed.get_json()["probe_tests"]
        self.assertEqual(len(selected_tests), 3)

        connection = FakeConnection(bytes([protocol.ACK]) * 2)
        service.handle_records(connection, ["Q|1|^REMOTE123||ALL||||||||0"])
        self.assertTrue(service.status()["probe_armed"])
        records = protocol.split_records(protocol.decode_frame(connection.sent[1]))
        self.assertEqual(records[1], "P|1")
        order_fields = records[2].split("|")
        self.assertEqual(order_fields[2], "REMOTE123")
        self.assertEqual(len(order_fields[4].split("\\")), 3)
        self.assertEqual(order_fields[5], "R")
        self.assertEqual(order_fields[11], "A")
        self.assertEqual(order_fields[15], "Normal")
        self.assertEqual(order_fields[16], "APPELLE MANEL/FODHIL")
        self.assertEqual(order_fields[25], "Q")
        self.assertEqual(
            self.store.get_order("REMOTE123")["status"],
            "transport_acknowledged",
        )

        second_connection = FakeConnection(bytes([protocol.ACK]) * 2)
        service.handle_records(second_connection, ["Q|1|^REMOTE124||ALL||||||||0"])
        self.assertEqual(len(second_connection.sent), 3)
        second_records = protocol.split_records(protocol.decode_frame(second_connection.sent[1]))
        self.assertEqual(second_records[1], "P|1")
        self.assertEqual(second_records[2].split("|")[16], "APPELLE MANEL/FODHIL")
        self.assertEqual(second_records[2].split("|")[2], "REMOTE124")
        self.assertTrue(service.status()["probe_armed"])

        disarmed = client.post("/api/continuous-probe", json={"armed": False})
        self.assertEqual(disarmed.status_code, 200)
        self.assertFalse(service.status()["probe_armed"])
        third_connection = FakeConnection()
        service.handle_records(third_connection, ["Q|1|^REMOTE125||ALL||||||||0"])
        self.assertEqual(third_connection.sent, [])

    def test_application_rejection_marks_transport_acknowledged_order_rejected(self):
        service = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        service.set_probe_armed(True)
        connection = FakeConnection(bytes([protocol.ACK]) * 2)
        service.handle_records(connection, ["Q|1|^REMOTE123||ALL||||||||0"])
        self.assertEqual(
            self.store.get_order("REMOTE123")["status"],
            "transport_acknowledged",
        )

        rejection = [
            "H|\\^&|||PROM^4.3.13||||1.5|WINLAB||P|LIS2-A|20260812164529",
            "P|1||||APPELLE MANEL/FODHIL|||M",
            "O|1|REMOTE123|||R||||||||||||||||||||X",
            "L|1|F",
        ]
        service.handle_records(FakeConnection(), rejection)
        order = self.store.get_order("REMOTE123")
        self.assertEqual(order["status"], "rejected")
        self.assertIn("O-26=X", order["last_error"])
        events = self.store.list_events()
        self.assertTrue(any(event["kind"] == "application_rejected" for event in events))


if __name__ == "__main__":
    unittest.main()
