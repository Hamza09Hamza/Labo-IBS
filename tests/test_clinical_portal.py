import json
import os
import tempfile
import unittest
from unittest.mock import patch

from clinical_portal import configuration
from clinical_portal.app import app
from clinical_portal.history import HistoryRecorder
from clinical_portal.store import store


UMEC_READINGS = {
    "source": "umec12",
    "readings": [
        {"code": "101", "value": 78},
        {"code": "160", "value": 97},
        {"code": "160", "value": -100},  # no-signal sentinel; must not count as valid
    ],
}


class ClinicalPortalCase(unittest.TestCase):
    def setUp(self):
        store.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.recorder = HistoryRecorder(store=store, db_path=os.path.join(self.temp.name, "history.db"))
        self.patcher = patch("clinical_portal.app.recorder", self.recorder)
        self.patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()
        store.clear()

    def test_latest_endpoint_returns_name_value_unit(self):
        store.ingest(1, UMEC_READINGS)
        response = self.client.get("/api/machines/1/umec12")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        by_name = {item["name"]: item for item in body["readings"]}
        self.assertEqual(by_name["Heart rate"]["value"], 78)
        self.assertEqual(by_name["Heart rate"]["unit"], "bpm")
        # the -100 sentinel reading must not surface as a valid latest value
        self.assertIsNone(by_name["Oxygen saturation"]["value"])

    def test_latest_endpoint_accepts_machine_name_case_insensitively(self):
        store.ingest(1, UMEC_READINGS)
        response = self.client.get("/api/machines/1/UMEC12")
        self.assertEqual(response.status_code, 200)

    def test_latest_endpoint_rejects_unknown_machine_name(self):
        response = self.client.get("/api/machines/1/not-a-real-machine")
        self.assertEqual(response.status_code, 404)
        self.assertIn("unknown machine name", response.get_json()["error"])

    def test_latest_endpoint_rejects_unknown_block(self):
        response = self.client.get("/api/machines/99/umec12")
        self.assertEqual(response.status_code, 404)

    def test_history_snapshot_persists_latest_and_stats(self):
        store.ingest(1, UMEC_READINGS)
        written = self.recorder.snapshot_once()
        self.assertGreater(written, 0)
        rows = self.recorder.recent(1, "umec12", code="101")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["latest_value"], 78)
        self.assertEqual(rows[0]["mean"], 78)
        self.assertEqual(rows[0]["count"], 1)

    def test_history_endpoint_returns_persisted_rows(self):
        store.ingest(1, UMEC_READINGS)
        self.recorder.snapshot_once()
        response = self.client.get("/api/machines/1/umec12/history?code=101")
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["latest_value"], 78)

    def test_ping_umec12_reports_unreachable_target_cleanly(self):
        # 192.0.2.0/24 is reserved (TEST-NET-1) and never routable, so this
        # exercises the real failure path without depending on any live host.
        with patch("clinical_portal.configuration.load_config", return_value={
            "web": {}, "chambers": [{
                "id": 1, "name": "Operation Block 1", "code": "OB-01",
                "umec12": {"enabled": True, "ip": "192.0.2.1"},
                "wato": {"enabled": False},
            }],
        }):
            response = self.client.post("/api/machines/1/umec12/ping")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["pingable"])
        self.assertFalse(body["ok"])
        self.assertIsNotNone(body["error"])

    def test_ping_umec12_without_configured_ip_is_rejected(self):
        with patch("clinical_portal.configuration.load_config", return_value={
            "web": {}, "chambers": [{
                "id": 1, "name": "Operation Block 1", "code": "OB-01",
                "umec12": {"enabled": False, "ip": ""},
                "wato": {"enabled": False},
            }],
        }):
            response = self.client.post("/api/machines/1/umec12/ping")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["pingable"])

    def test_ping_wato_reports_it_cannot_dial_out(self):
        response = self.client.post("/api/machines/1/wato/ping")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["pingable"])
        self.assertIn("cannot dial out", body["reason"])
        self.assertIn("device_state", body)

    def test_latest_and_ping_report_the_configured_machine_id(self):
        store.ingest(1, UMEC_READINGS)
        with patch("clinical_portal.configuration.load_config", return_value={
            "web": {}, "chambers": [{
                "id": 1, "name": "Operation Block 1", "code": "OB-01",
                "umec12": {"machine_id": "UMEC-42", "enabled": True, "ip": ""},
                "wato": {"enabled": False},
            }],
        }):
            latest = self.client.get("/api/machines/1/umec12").get_json()
            ping = self.client.post("/api/machines/1/umec12/ping").get_json()
        self.assertEqual(latest["machine_id"], "UMEC-42")
        self.assertEqual(ping["machine_id"], "UMEC-42")

    def test_ping_treats_machine_name_case_insensitively_not_just_lookup(self):
        # Regression: the route used to branch on the raw (possibly
        # uppercase) URL segment instead of the normalized source, so an
        # uppercase machine name silently fell into the WATO branch.
        with patch("clinical_portal.configuration.load_config", return_value={
            "web": {}, "chambers": [{
                "id": 1, "name": "Operation Block 1", "code": "OB-01",
                "umec12": {"enabled": True, "ip": "192.0.2.1"},
                "wato": {"enabled": False},
            }],
        }):
            response = self.client.post("/api/machines/1/UMEC12/ping")
        body = response.get_json()
        self.assertIn("ip", body)
        self.assertNotIn("reason", body)

    def test_history_by_machine_id_follows_a_device_across_blocks(self):
        # Simulate the same physical monitor being read while configured
        # under block 1, then moved and read again under block 2.
        with patch("clinical_portal.configuration.load_config", return_value={
            "web": {}, "chambers": [
                {"id": 1, "name": "Operation Block 1", "code": "OB-01",
                 "umec12": {"machine_id": "UMEC-ROAMING", "enabled": True, "ip": ""},
                 "wato": {"enabled": False}},
                {"id": 2, "name": "Operation Block 2", "code": "OB-02",
                 "umec12": {"machine_id": "UMEC-STATIONARY", "enabled": True, "ip": ""},
                 "wato": {"enabled": False}},
            ],
        }):
            store.ingest(1, UMEC_READINGS)
            self.recorder.snapshot_once()

        with patch("clinical_portal.configuration.load_config", return_value={
            "web": {}, "chambers": [
                {"id": 1, "name": "Operation Block 1", "code": "OB-01",
                 "umec12": {"machine_id": "UMEC-STATIONARY", "enabled": True, "ip": ""},
                 "wato": {"enabled": False}},
                {"id": 2, "name": "Operation Block 2", "code": "OB-02",
                 "umec12": {"machine_id": "UMEC-ROAMING", "enabled": True, "ip": ""},
                 "wato": {"enabled": False}},
            ],
        }):
            store.ingest(2, UMEC_READINGS)
            self.recorder.snapshot_once()
            response = self.client.get("/api/machines/by-id/UMEC-ROAMING/history?code=101")

        rows = response.get_json()["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["chamber_id"] for row in rows}, {1, 2})


class ConfigurationMachineIdCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp.name, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({
                "web": {"host": "0.0.0.0", "port": 5051},
                "chambers": [
                    {"id": 1, "name": "Operation Block 1", "code": "OB-01",
                     "umec12": {"machine_id": "UMEC-01", "enabled": True, "ip": "10.0.0.1"},
                     "wato": {"machine_id": "WATO-01", "enabled": False}},
                    {"id": 2, "name": "Operation Block 2", "code": "OB-02",
                     "umec12": {"machine_id": "UMEC-02", "enabled": False, "ip": ""},
                     "wato": {"machine_id": "WATO-02", "enabled": False}},
                ],
            }, handle)
        self.patcher = patch("clinical_portal.configuration.CONFIG_PATH", self.config_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_machine_id_can_be_reassigned(self):
        result = configuration.update_machine(1, "umec12", {"machine_id": "UMEC-99"})
        self.assertEqual(result["machines"]["umec12"]["machine_id"], "UMEC-99")

    def test_machine_id_cannot_collide_with_another_slot(self):
        with self.assertRaises(ValueError) as ctx:
            configuration.update_machine(1, "umec12", {"machine_id": "UMEC-02"})
        self.assertIn("already assigned", str(ctx.exception))

    def test_machine_id_rejects_empty_and_unsafe_characters(self):
        with self.assertRaises(ValueError):
            configuration.update_machine(1, "umec12", {"machine_id": ""})
        with self.assertRaises(ValueError):
            configuration.update_machine(1, "umec12", {"machine_id": "bad id!"})


if __name__ == "__main__":
    unittest.main()
