import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import capture_listener
from labo_bridge import server
from labo_bridge.decoders import wato_ex35


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


if __name__ == "__main__":
    unittest.main()
