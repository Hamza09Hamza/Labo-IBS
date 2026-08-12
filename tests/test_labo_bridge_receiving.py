import unittest
import tempfile
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import capture_listener
from labo_bridge import server
from labo_bridge.decoders import wato_ex35
from labo_bridge.protocols import astm
from selectra_host_query import protocol as host_query_protocol
from selectra_host_query.server import SelectraHostQueryServer
from selectra_host_query.store import BenchStore


class FakeAstmConnection:
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.sent = []

    def recv(self, size):
        if not self.incoming:
            return b""
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def sendall(self, data):
        self.sent.append(data)


class QueryRecorder:
    def __init__(self):
        self.calls = []

    def handle_records(self, connection, records):
        self.calls.append((connection, records))


class ReceivingMergeCase(unittest.TestCase):
    @patch.object(server.pg, "write_sample")
    def test_cyanvision_prefers_pid_patient_id_as_sample_id(self, write_sample):
        session = server._Session("cyanvision", "10.10.12.52", quiet=True)
        session.handle_event({"kind": "patient", "patient_id": "2608029203", "patient_name": "TEST"})
        session.handle_event({"kind": "order", "sample_id": "2228"})

        self.assertEqual(session.sample_id, "2608029203")
        self.assertEqual(write_sample.call_args.args[1], "2608029203")

    @patch.object(server.pg, "write_sample")
    def test_cyanvision_falls_back_to_obr_when_pid_is_missing(self, write_sample):
        session = server._Session("cyanvision", "10.10.12.52", quiet=True)
        session.handle_event({"kind": "order", "sample_id": "2228"})

        self.assertEqual(session.sample_id, "2228")
        self.assertEqual(write_sample.call_args.args[1], "2228")

    @patch.object(server.pg, "write_sample")
    def test_other_machines_keep_their_order_sample_id(self, write_sample):
        session = server._Session("xs500i", "10.10.12.60", quiet=True)
        session.handle_event({"kind": "patient", "patient_id": "PATIENT-ID", "patient_name": "TEST"})
        session.handle_event({"kind": "order", "sample_id": "ORDER-ID"})

        self.assertEqual(session.sample_id, "ORDER-ID")
        self.assertEqual(write_sample.call_args.args[1], "ORDER-ID")

    def test_capture_listener_accepts_tcp_and_udp_targets(self):
        self.assertEqual(
            capture_listener._parse_targets(["cyanvision:6004", "probe:udp:6011"]),
            {"cyanvision": ("tcp", 6004), "probe": ("udp", 6011)},
        )

    def test_wato_decoder_produces_readable_measurement(self):
        fields = "OBX|1|NM|151957^MDC_VENT_PRESS_MAX^MDC||15|266048^MDC_DIM_CM_H2O^MDC|||||F|||20260810120000+0100".split("|")
        reading = wato_ex35.decode_obx(fields)

        self.assertEqual(reading["label"], "Peak Airway Pressure")
        self.assertEqual(reading["value"], "15")
        self.assertEqual(reading["unit_label"], "cmH2O")

    def test_selectra_session_writes_exact_raw_diagnostic_bytes(self):
        session = server._Session("selectra", "10.10.12.52", quiet=True)
        session.raw_bytes = b"\x05\x02ASTM-DATA\x03\x04"
        session.raw_lines = ["H|\\^&", "L|1|N"]

        with tempfile.TemporaryDirectory() as directory, patch.object(server, "RESULTS_DIR", directory):
            server._write_session_file(session)
            files = list(Path(directory).glob("selectra_*.txt"))
            self.assertEqual(len(files), 1)
            report = files[0].read_text()

        self.assertIn("Source IP: 10.10.12.52", report)
        self.assertIn("b'\\x05\\x02ASTM-DATA\\x03\\x04'", report)

    def test_production_selectra_eot_hands_query_to_host_query_service(self):
        query = "Q|1|^HQ-DEMO-001||ALL||||||||0"
        incoming = (
            astm.B_ENQ
            + astm.build_frame(1, "H|\\^&|||PROM^4.3.13||||1.5|WINLAB||P|LIS2-A")
            + astm.build_frame(2, query)
            + astm.build_frame(3, "L|1|N")
            + astm.B_EOT
        )
        connection = FakeAstmConnection(incoming)
        recorder = QueryRecorder()
        previous = server._selectra_host_query_service
        server.configure_selectra_host_query(recorder)
        try:
            with patch.object(server, "_write_session_file"):
                server._handle_astm(
                    connection,
                    ("172.16.2.254", 50000),
                    server.MACHINES["selectra"],
                    "selectra",
                    quiet=True,
                )
        finally:
            server.configure_selectra_host_query(previous)

        self.assertEqual(len(recorder.calls), 1)
        self.assertIn(query, recorder.calls[0][1])
        self.assertEqual(connection.sent, [astm.B_ACK] * 4)

    def test_full_port_6003_query_and_order_download_handshake(self):
        order = {
            "sample_id": "HQ-DEMO-6003",
            "patient_id": "P-DEMO-6003",
            "family_name": "BENCH",
            "given_name": "PATIENT",
            "birth_date": "1980-06-15",
            "sex": "U",
            "specimen_type": "SERUM",
            "tests": ["Glucose pap sl", "Creatinine"],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = BenchStore(str(Path(directory) / "host_query.db"))
            store.upsert_order(order)
            service = SelectraHostQueryServer(store, armed=True, embedded=True)
            host_socket, instrument_socket = socket.socketpair()
            host_socket.settimeout(2)
            instrument_socket.settimeout(2)
            previous = server._selectra_host_query_service
            server.configure_selectra_host_query(service)

            def serve():
                with patch.object(server, "_write_session_file"):
                    server._handle_astm(
                        host_socket,
                        ("172.16.2.254", 50000),
                        server.MACHINES["selectra"],
                        "selectra",
                        quiet=True,
                    )

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                for payload in (
                    astm.B_ENQ,
                    astm.build_frame(1, "H|\\^&|||PROM^4.3.13||||1.5|WINLAB||P|LIS2-A"),
                    astm.build_frame(2, "Q|1|^HQ-DEMO-6003||ALL||||||||0"),
                    astm.build_frame(3, "L|1|N"),
                ):
                    instrument_socket.sendall(payload)
                    self.assertEqual(instrument_socket.recv(1), astm.B_ACK)
                instrument_socket.sendall(astm.B_EOT)

                self.assertEqual(instrument_socket.recv(1), astm.B_ENQ)
                instrument_socket.sendall(astm.B_ACK)
                response_records = []
                for _ in range(4):
                    frame = b""
                    while not frame.endswith(bytes([astm.CR, astm.LF])):
                        frame += instrument_socket.recv(4096)
                    response_records.append(host_query_protocol.decode_frame(frame))
                    instrument_socket.sendall(astm.B_ACK)
                self.assertEqual(instrument_socket.recv(1), astm.B_EOT)
            finally:
                instrument_socket.close()
                worker.join(timeout=2)
                host_socket.close()
                server.configure_selectra_host_query(previous)

        self.assertEqual([record[0] for record in response_records], ["H", "P", "O", "L"])
        self.assertIn("BENCH^PATIENT", response_records[1])
        self.assertIn("HQ-DEMO-6003", response_records[2])
        self.assertIn("^^^Glucose pap sl\\^^^Creatinine", response_records[2])


if __name__ == "__main__":
    unittest.main()
