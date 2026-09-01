"""Thread-safe realtime chamber store for the standalone clinical portal.

The laboratory bridge has several transport implementations, but the portal
must not care whether a value came from a Mindray PDS client connection or a
WATO HL7 listener. Capture processes post normalized batches here; this module
keeps a bounded rolling history and computes display-only summaries.

This is intentionally not a diagnostic engine. It never invents reference
ranges or clinical interpretations. Alarm state comes from the source device,
and invalid/missing values remain visibly unavailable.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone


WINDOWS = {0, 10, 20, 30, 60}
MAX_POINTS_PER_PARAMETER = 3600

CHAMBERS = {
    1: {"name": "Chamber 1", "code": "OR-01"},
    2: {"name": "Chamber 2", "code": "OR-02"},
    3: {"name": "Chamber 3", "code": "OR-03"},
}

SOURCES = {
    "umec12": {"label": "Mindray uMEC12", "kind": "Patient monitor"},
    "wato": {"label": "Mindray WATO EX-35", "kind": "Anesthesia workstation"},
}

# The uMEC values are confirmed from the monitor's real PDS stream. WATO
# names are the official MDC identifiers already used by capture_wato.py;
# unknown WATO OBX identifiers remain accepted and use their transmitted
# label, because hiding an unfamiliar but real measurement would be worse
# than displaying it without a friendly name.
PARAMETERS = {
    ("umec12", "101"): {"label": "Heart rate", "short": "HR", "unit": "bpm", "order": 10},
    ("umec12", "151"): {"label": "Respiration rate", "short": "RR", "unit": "rpm", "order": 20},
    ("umec12", "160"): {"label": "Oxygen saturation", "short": "SpO₂", "unit": "%", "order": 30},
    ("umec12", "161"): {"label": "Pulse rate", "short": "PR", "unit": "bpm", "order": 40},
    ("umec12", "162"): {"label": "Perfusion index", "short": "PI", "unit": "%", "order": 50},
    ("umec12", "170"): {"label": "NIBP systolic", "short": "SYS", "unit": "mmHg", "order": 60},
    ("umec12", "172"): {"label": "NIBP mean", "short": "MAP", "unit": "mmHg", "order": 61},
    ("umec12", "171"): {"label": "NIBP diastolic", "short": "DIA", "unit": "mmHg", "order": 62},
    ("umec12", "173"): {"label": "NIBP pulse", "short": "NIBP PR", "unit": "bpm", "order": 63},
    ("umec12", "200"): {"label": "Temperature 1", "short": "T1", "unit": "°C", "order": 70},
    ("umec12", "201"): {"label": "Temperature 2", "short": "T2", "unit": "°C", "order": 71},
    ("umec12", "202"): {"label": "Temperature difference", "short": "ΔT", "unit": "°C", "order": 72},
    ("wato", "MDC_VENT_PRESS_MAX"): {"label": "Peak airway pressure", "short": "Ppeak", "unit": "cmH₂O", "order": 10},
    ("wato", "MDC_PRESS_AWAY_INSP_MEAN"): {"label": "Mean airway pressure", "short": "Pmean", "unit": "cmH₂O", "order": 20},
    ("wato", "MDC_PRESS_RESP_PLAT"): {"label": "Plateau pressure", "short": "Pplat", "unit": "cmH₂O", "order": 30},
    ("wato", "MDC_VENT_PRESS_AWAY_END_EXP_POS"): {"label": "PEEP", "short": "PEEP", "unit": "cmH₂O", "order": 40},
    ("wato", "MDC_VOL_MINUTE_AWAY"): {"label": "Minute volume", "short": "MV", "unit": "L/min", "order": 50},
    ("wato", "MDC_VOL_AWAY_TIDAL"): {"label": "Expiratory tidal volume", "short": "VTe", "unit": "mL", "order": 60},
    ("wato", "MDC_VENT_RESP_RATE"): {"label": "Ventilator respiratory rate", "short": "RR", "unit": "rpm", "order": 70},
    ("wato", "MDC_FLOW_O2_FG"): {"label": "O₂ fresh gas flow", "short": "O₂ flow", "unit": "L/min", "order": 80},
    ("wato", "MDC_CONC_AWAY_O2_INSP"): {"label": "Inspired oxygen", "short": "FiO₂", "unit": "%", "order": 90},
    ("wato", "MDC_CONC_AWAY_O2_ET"): {"label": "End-tidal oxygen", "short": "EtO₂", "unit": "%", "order": 100},
    ("wato", "MDC_CONC_AWAY_CO2_ET"): {"label": "End-tidal CO₂", "short": "EtCO₂", "unit": "mmHg", "order": 110},
    ("wato", "MDC_CONC_AWAY_CO2_INSP"): {"label": "Inspired CO₂", "short": "FiCO₂", "unit": "mmHg", "order": 120},
    ("wato", "MDC_CO2_RESP_RATE"): {"label": "CO₂ respiratory rate", "short": "awRR", "unit": "rpm", "order": 130},
    ("wato", "MDC_CONC_MAC"): {"label": "Minimum alveolar concentration", "short": "MAC", "unit": "", "order": 140},
}

HEADLINE_KEYS = [
    ("umec12", "160"),
    ("umec12", "101"),
    ("umec12", "151"),
    ("wato", "MDC_CONC_AWAY_CO2_ET"),
]


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_timestamp(value):
    if not value:
        return _utcnow()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return _utcnow()


def _number(value):
    try:
        number = float(str(value).strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


class ClinicalStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._history = defaultdict(lambda: deque(maxlen=MAX_POINTS_PER_PARAMETER))
        self._device_seen = {}
        self._patients = {}
        self._alarms = defaultdict(list)
        self._demo_mode = False

    def set_demo_mode(self, enabled: bool):
        with self._lock:
            self._demo_mode = bool(enabled)

    def ingest(self, chamber_id: int, payload: dict):
        if chamber_id not in CHAMBERS:
            raise ValueError("unknown chamber")
        source = str(payload.get("source", "")).strip().lower()
        if source not in SOURCES:
            raise ValueError("source must be 'umec12' or 'wato'")

        stamp = _parse_timestamp(payload.get("timestamp"))
        readings = payload.get("readings") or []
        if not isinstance(readings, list):
            raise ValueError("readings must be a list")

        accepted = 0
        with self._lock:
            self._device_seen[(chamber_id, source)] = stamp

            patient = payload.get("patient")
            if isinstance(patient, dict):
                current = dict(self._patients.get(chamber_id, {}))
                for key in ("id", "name", "case_id"):
                    if key in patient:
                        current[key] = str(patient.get(key) or "").strip()
                current["updated_at"] = stamp
                self._patients[chamber_id] = current

            if "alarms" in payload:
                alarms = payload.get("alarms") or []
                if not isinstance(alarms, list):
                    raise ValueError("alarms must be a list")
                self._alarms[(chamber_id, source)] = [
                    {
                        "code": str(item.get("code", "")),
                        "text": str(item.get("text", "")).strip(),
                        "level": str(item.get("level", "device")).strip().lower(),
                        "timestamp": stamp,
                    }
                    for item in alarms if isinstance(item, dict) and item.get("text")
                ]

            for item in readings:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).strip()
                if not code:
                    continue
                meta = PARAMETERS.get((source, code), {})
                raw_value = item.get("value")
                numeric = _number(raw_value)
                valid = bool(item.get("valid", True)) and numeric is not None
                # uMEC12 uses -100 as an explicit no-signal/no-reading
                # sentinel for realtime SpO2 and unmeasured NIBP channels.
                if source == "umec12" and numeric == -100:
                    valid = False
                point_stamp = _parse_timestamp(item.get("timestamp") or stamp)
                self._history[(chamber_id, source, code)].append({
                    "timestamp": point_stamp,
                    "value": numeric,
                    "raw_value": "" if raw_value is None else str(raw_value),
                    "valid": valid,
                    "label": str(item.get("label") or meta.get("label") or code),
                    "short": str(item.get("short") or meta.get("short") or code),
                    "unit": str(item.get("unit") or meta.get("unit") or ""),
                    "order": int(meta.get("order", 999)),
                })
                accepted += 1
        return accepted

    # uMEC PDS normally reports once per second. WATO's documented Ethernet
    # HL7 menu offers 10 seconds as its fastest interval, so it must not
    # flash "delayed" between two perfectly normal reports.
    LIVE_AFTER = {"wato": 15}
    OFFLINE_AFTER = {"wato": 45}
    DEFAULT_LIVE_AFTER = 5
    DEFAULT_OFFLINE_AFTER = 30

    @classmethod
    def _device_state(cls, source, last_seen, now):
        if last_seen is None:
            return "offline"
        age = (now - last_seen).total_seconds()
        live_after = cls.LIVE_AFTER.get(source, cls.DEFAULT_LIVE_AFTER)
        offline_after = cls.OFFLINE_AFTER.get(source, cls.DEFAULT_OFFLINE_AFTER)
        if age <= live_after:
            return "live"
        if age <= offline_after:
            return "stale"
        return "offline"

    def _parameter_snapshot(self, source, points, window_seconds, now):
        latest = points[-1]
        offline_after = self.OFFLINE_AFTER.get(source, self.DEFAULT_OFFLINE_AFTER)
        # A reading is only "current" while its source device is reachable.
        # Once the device has gone quiet past its offline threshold, the last
        # value it ever reported must not keep being displayed as if live -
        # that would misrepresent an unreachable machine as a real patient
        # reading.
        reachable = (now - latest["timestamp"]).total_seconds() <= offline_after
        latest_valid = latest["valid"] and reachable

        cutoff = now - timedelta(seconds=window_seconds or 60)
        visible = [point for point in points if point["timestamp"] >= cutoff] if reachable else []
        valid = [point for point in visible if point["valid"]]

        if window_seconds == 0:
            stat_points = [latest] if latest_valid else []
        else:
            stat_points = valid
        values = [point["value"] for point in stat_points]

        return {
            "code": None,
            "label": latest["label"],
            "short": latest["short"],
            "unit": latest["unit"],
            "order": latest["order"],
            "latest": latest["value"] if latest_valid else None,
            "latest_raw": latest["raw_value"] if reachable else "",
            "valid": latest_valid,
            "last_seen": _iso(latest["timestamp"]),
            "mean": sum(values) / len(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "count": len(values),
            "history": [
                {"timestamp": _iso(point["timestamp"]), "value": point["value"]}
                for point in visible if point["valid"]
            ][-60:],
        }

    def chamber(self, chamber_id: int, window_seconds: int = 10):
        if chamber_id not in CHAMBERS:
            raise KeyError(chamber_id)
        if window_seconds not in WINDOWS:
            raise ValueError("unsupported window")

        now = _utcnow()
        with self._lock:
            devices = []
            parameter_lookup = {}
            all_alarms = []

            for source, source_meta in SOURCES.items():
                last_seen = self._device_seen.get((chamber_id, source))
                parameters = []
                for (cid, reading_source, code), points in self._history.items():
                    if cid != chamber_id or reading_source != source or not points:
                        continue
                    item = self._parameter_snapshot(source, list(points), window_seconds, now)
                    item["code"] = code
                    parameters.append(item)
                    parameter_lookup[(source, code)] = item
                parameters.sort(key=lambda item: (item["order"], item["label"].lower()))

                alarms = []
                for alarm in self._alarms.get((chamber_id, source), []):
                    alarm_item = dict(alarm)
                    alarm_item["timestamp"] = _iso(alarm_item["timestamp"])
                    alarm_item["source"] = source
                    alarms.append(alarm_item)
                    all_alarms.append(alarm_item)

                devices.append({
                    "source": source,
                    **source_meta,
                    "state": self._device_state(source, last_seen, now),
                    "last_seen": _iso(last_seen),
                    "parameters": parameters,
                    "alarms": alarms,
                })

            if all_alarms:
                state = "alarm"
            elif any(device["state"] == "live" for device in devices):
                state = "live"
            elif any(device["state"] == "stale" for device in devices):
                state = "stale"
            else:
                state = "offline"

            patient = dict(self._patients.get(chamber_id, {}))
            if patient.get("updated_at"):
                patient["updated_at"] = _iso(patient["updated_at"])

            return {
                "id": chamber_id,
                **CHAMBERS[chamber_id],
                "state": state,
                "patient": patient,
                "window_seconds": window_seconds,
                "devices": devices,
                "headline": [parameter_lookup[key] for key in HEADLINE_KEYS if key in parameter_lookup],
                "alarms": all_alarms,
            }

    def overview(self, window_seconds: int = 10):
        return {
            "generated_at": _iso(_utcnow()),
            "window_seconds": window_seconds,
            "demo_mode": self._demo_mode,
            "chambers": [self.chamber(chamber_id, window_seconds) for chamber_id in CHAMBERS],
        }

    def clear(self):
        """Test helper; production callers should never erase live history."""
        with self._lock:
            self._history.clear()
            self._device_seen.clear()
            self._patients.clear()
            self._alarms.clear()


store = ClinicalStore()
