import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from cyanvision_worklist import protocol
from cyanvision_worklist.service import CyanVisionWorklistService
from labo_bridge import server
from labo_bridge.protocols import hl7_mllp
from selectra_host_query.app import create_app
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore


ORDER = {
    "sample_id": "CYAN-DEMO-01",
    "given_name": "MANEL",
    "family_name": "APPELLE FODHIL",
    "birth_date": "1980-06-15",
    "sex": "F",
    "test_code": "ALP",
}

QUERY = [
    "MSH|^~\\&|CYPRESS|CYANVISION|||||QRY^Q02|QUERY-17|P|2.3.1",
    "QRD|20070723170000|R|D|1|||RD||OTH|||T|",
    "QRF|CyanVision|20070723000000|20070723170000|||RCT|COR|ALL||",
]


class FakeConnection:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


def unframe(payload):
    messages = list(hl7_mllp.iter_messages(payload))
    assert len(messages) == 1
    return hl7_mllp.split_segments(messages[0][0])


class CyanVisionWorklistCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = BenchStore(os.path.join(self.temp.name, "bench.db"))
        self.service = CyanVisionWorklistService(self.store, port=6004)

    def tearDown(self):
        self.temp.cleanup()

    def test_manual_dsr_layout_and_mllp_envelope(self):
        records = protocol.build_dsr(ORDER, QUERY, "DSR-18", "QUERY-17")
        fields = records[0].split("|")
        self.assertEqual(fields[2:6], ["", "", "CYPRESS", "CYANVISION"])
        self.assertEqual(fields[8:12], ["DSR^Q03", "DSR-18", "P", "2.3.1"])
        self.assertEqual(records[1], "MSA|AA|QUERY-17|Message accepted|||0|")
        self.assertEqual(records[3], "QAK|SR|OK|")
        self.assertEqual(records[4:6], QUERY[1:3])
        self.assertEqual(
            records[6:14],
            [
                "DSP|1||CYAN-DEMO-01|||",
                "DSP|2||Y|||",
                "DSP|3||MANEL|||",
                "DSP|4||APPELLE FODHIL|||",
                "DSP|5||F|||",
                "DSP|6||19800615000000|||",
                "DSP|7||1|||",
                "DSP|8||ALP|||",
            ],
        )
        self.assertEqual(records[-1], "DSC||")
        self.assertEqual(unframe(protocol.frame(records)), records)

    def test_one_order_disarms_only_after_matching_positive_ack(self):
        self.service.stage_and_arm(ORDER)
        connection = FakeConnection()
        self.assertTrue(self.service.handle_message(connection, QUERY))
        self.assertEqual(len(connection.sent), 1)
        sent = unframe(connection.sent[0])
        response_id = protocol.control_id(sent)
        self.assertEqual(protocol.message_type(sent), "DSR^Q03")
        self.assertEqual(self.service.status()["status"], "waiting_for_ack")
        self.assertTrue(self.service.status()["armed"])

        wrong_ack = [
            "MSH|^~\\&|CYPRESS|CYANVISION|||||ACK^Q03|A1|P|2.3.1",
            "MSA|AA|SOME-OTHER-ID|Message accepted|||0|",
        ]
        self.service.handle_message(connection, wrong_ack)
        self.assertTrue(self.service.status()["armed"])

        matching_ack = [
            "MSH|^~\\&|CYPRESS|CYANVISION|||||ACK^Q03|A2|P|2.3.1",
            f"MSA|AA|{response_id}|Message accepted|||0|",
        ]
        self.service.handle_message(connection, matching_ack)
        status = self.service.status()
        self.assertFalse(status["armed"])
        self.assertFalse(status["pending_ack"])
        self.assertEqual(status["status"], "acknowledged")

    def test_connection_loss_keeps_order_armed_for_retry(self):
        self.service.stage_and_arm(ORDER)
        self.service.handle_message(FakeConnection(), QUERY)
        self.service.connection_closed()
        status = self.service.status()
        self.assertTrue(status["armed"])
        self.assertFalse(status["pending_ack"])
        self.assertEqual(status["status"], "armed")

    def test_unarmed_query_returns_final_no_data_dataset(self):
        connection = FakeConnection()
        self.service.handle_message(connection, QUERY)
        records = unframe(connection.sent[0])
        self.assertIn("QAK|SR|NF|", records)
        self.assertFalse(any(record.startswith("DSP|") for record in records))
        self.assertEqual(records[-1], "DSC||")

    def test_web_api_validates_stages_and_disarms_one_order(self):
        selectra = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        client = create_app(self.store, selectra, self.service).test_client()

        missing_confirmation = client.post("/api/cyanvision/worklist", json=ORDER)
        self.assertEqual(missing_confirmation.status_code, 400)
        invalid_ascii = client.post(
            "/api/cyanvision/worklist",
            json={**ORDER, "family_name": "HÉLÈNE", "confirmation": "ARM CYANVISION WORKLIST"},
        )
        self.assertEqual(invalid_ascii.status_code, 400)
        invalid_mllp_control = client.post(
            "/api/cyanvision/worklist",
            json={**ORDER, "sample_id": "BAD\u000bID", "confirmation": "ARM CYANVISION WORKLIST"},
        )
        self.assertEqual(invalid_mllp_control.status_code, 400)
        unknown_code = client.post(
            "/api/cyanvision/worklist",
            json={**ORDER, "test_code": "ALP-TYPO", "confirmation": "ARM CYANVISION WORKLIST"},
        )
        self.assertEqual(unknown_code.status_code, 400)
        self.assertIn("exact codes", unknown_code.get_json()["error"])

        staged = client.post(
            "/api/cyanvision/worklist",
            json={**ORDER, "confirmation": "ARM CYANVISION WORKLIST"},
        )
        self.assertEqual(staged.status_code, 201)
        body = staged.get_json()
        self.assertTrue(body["armed"])
        self.assertEqual(
            {key: body["order"][key] for key in ORDER},
            ORDER,
        )
        self.assertEqual(body["response_preview"][-1], "DSC||")

        disarmed = client.delete("/api/cyanvision/worklist")
        self.assertEqual(disarmed.status_code, 200)
        self.assertFalse(disarmed.get_json()["armed"])

    @patch("selectra_host_query.app.pg.list_observed_test_codes")
    def test_web_api_lists_mapped_and_observed_cyanvision_codes(self, observed):
        observed.return_value = [
            {
                "code": "LIPASE", "display_name": "LIPASE",
                "last_seen": "2026-08-10T15:10:12", "source": "pending",
            },
            {
                "code": "ALP", "display_name": "Phosphatases alcalines",
                "last_seen": "2026-07-22T15:20:45", "source": "matched",
            },
        ]
        selectra = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        client = create_app(self.store, selectra, self.service).test_client()
        response = client.get("/api/cyanvision/tests")
        self.assertEqual(response.status_code, 200)
        tests = {item["code"]: item for item in response.get_json()["tests"]}
        self.assertTrue(tests["ALP"]["mapped"])
        self.assertTrue(tests["ALP"]["observed"])
        self.assertTrue(tests["LIPASE"]["observed"])
        self.assertFalse(tests["LIPASE"]["mapped"])

    def test_standalone_selectra_ui_reports_cyanvision_unavailable_without_error(self):
        selectra = SelectraHostQueryServer(self.store, armed=False, embedded=True)
        client = create_app(self.store, selectra).test_client()
        response = client.get("/api/cyanvision/worklist")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"available": False})

    def test_existing_cyanvision_port_dispatches_query_dsr_and_ack(self):
        self.service.stage_and_arm(ORDER)
        host_socket, instrument_socket = socket.socketpair()
        host_socket.settimeout(2)
        instrument_socket.settimeout(2)
        previous = server._cyanvision_worklist_service
        server.configure_cyanvision_worklist(self.service)

        def serve():
            with patch.object(server, "_write_session_file"):
                server._handle_hl7(
                    host_socket,
                    ("10.10.12.52", 50000),
                    server.MACHINES["cyanvision"],
                    "cyanvision",
                    quiet=True,
                )

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            instrument_socket.sendall(protocol.frame(QUERY))
            response = b""
            while hl7_mllp.B_FS not in response:
                response += instrument_socket.recv(4096)
            records = unframe(response)
            self.assertEqual(protocol.message_type(records), "DSR^Q03")
            self.assertFalse(any("ACK|" in record for record in records))

            response_id = protocol.control_id(records)
            ack = [
                "MSH|^~\\&|CYPRESS|CYANVISION|||||ACK^Q03|ACK-19|P|2.3.1",
                f"MSA|AA|{response_id}|Message accepted|||0|",
            ]
            instrument_socket.sendall(protocol.frame(ack))
            instrument_socket.shutdown(socket.SHUT_WR)
            worker.join(timeout=2)
        finally:
            instrument_socket.close()
            host_socket.close()
            server.configure_cyanvision_worklist(previous)

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.service.status()["status"], "acknowledged")

    def test_continuation_control_ids_are_unique_and_within_cy014_limit(self):
        first = {**ORDER, "sample_id": "CYAN-CONTROL-001"}
        second = {**ORDER, "sample_id": "CYAN-CONTROL-002", "test_code": "CRE"}
        self.store.upsert_cyanvision_order(first, source="api", ready=True)
        self.store.upsert_cyanvision_order(second, source="api", ready=True)
        connection = FakeConnection()

        self.service.handle_message(connection, QUERY)
        first_records = unframe(connection.sent[0])
        first_id = protocol.control_id(first_records)
        self.assertEqual(len(first_id), 20)

        self.service.handle_message(connection, [
            "MSH|^~\\&|CYPRESS|CYANVISION|||||ACK^Q03|ACK-1|P|2.3.1",
            f"MSA|AA|{first_id}|Message accepted|||0|",
        ])
        second_records = unframe(connection.sent[1])
        second_id = protocol.control_id(second_records)
        self.assertEqual(len(second_id), 20)
        self.assertNotEqual(second_id, first_id)


if __name__ == "__main__":
    unittest.main()
