"""Clearly-labelled synthetic streams for developing the portal off-site."""

import math
import random
import threading
import time

from clinical_portal.store import store


ROOMS = {
    1: {"patient": "Demo Patient A", "id": "DEMO-001", "hr": 76, "rr": 15, "spo2": 98,
        "sys": 121, "dia": 76, "temp": 36.7, "etco2": 36, "ppeak": 19, "vte": 485},
    2: {"patient": "Demo Patient B", "id": "DEMO-002", "hr": 88, "rr": 17, "spo2": 97,
        "sys": 132, "dia": 82, "temp": 37.0, "etco2": 34, "ppeak": 22, "vte": 510},
    3: {"patient": "Demo Patient C", "id": "DEMO-003", "hr": 69, "rr": 14, "spo2": 99,
        "sys": 114, "dia": 70, "temp": 36.5, "etco2": 38, "ppeak": 18, "vte": 460},
}


def _wave(base, elapsed, period, amplitude, offset=0.0):
    return base + math.sin((elapsed + offset) * (2 * math.pi / period)) * amplitude


def run_demo(stop_event: threading.Event, interval: float = 1.0):
    """Continuously populate all rooms until the shared stop event is set."""
    rng = random.Random(1203)
    started = time.monotonic()
    nibp_tick = -1
    store.set_demo_mode(True)
    print("[demo] SIMULATED DATA enabled — no medical devices will be contacted")

    while not stop_event.is_set():
        elapsed = time.monotonic() - started
        current_nibp_tick = int(elapsed // 15)

        for chamber_id, room in ROOMS.items():
            phase = chamber_id * 2.7
            hr = round(_wave(room["hr"], elapsed, 13 + chamber_id, 3.2, phase) + rng.uniform(-0.7, 0.7))
            rr = round(_wave(room["rr"], elapsed, 19, 1.1, phase), 1)
            spo2 = round(_wave(room["spo2"], elapsed, 23, 0.7, phase))
            pulse = hr + rng.choice((-1, 0, 0, 0, 1))
            pi = round(_wave(6.2 - chamber_id * 0.35, elapsed, 11, 0.8, phase), 2)
            temp = round(_wave(room["temp"], elapsed, 48, 0.12, phase), 1)

            # Periodically demonstrate the real uMEC no-signal behavior in
            # Chamber 3. It remains visibly fake and is never averaged.
            sensor_demo = chamber_id == 3 and 38 <= (elapsed % 55) < 45
            if sensor_demo:
                spo2 = -100
                pulse = -100
                pi = -100

            umec_readings = [
                {"code": "101", "value": hr, "unit": "bpm"},
                {"code": "151", "value": rr, "unit": "rpm"},
                {"code": "160", "value": spo2, "unit": "%", "valid": not sensor_demo},
                {"code": "161", "value": pulse, "unit": "bpm", "valid": not sensor_demo},
                {"code": "162", "value": pi, "unit": "%", "valid": not sensor_demo},
                {"code": "200", "value": temp, "unit": "°C"},
            ]
            if current_nibp_tick != nibp_tick:
                sys = round(_wave(room["sys"], elapsed, 31, 4, phase))
                dia = round(_wave(room["dia"], elapsed, 29, 3, phase))
                mean = round((sys + 2 * dia) / 3)
                umec_readings.extend([
                    {"code": "170", "value": sys, "unit": "mmHg"},
                    {"code": "171", "value": dia, "unit": "mmHg"},
                    {"code": "172", "value": mean, "unit": "mmHg"},
                ])

            umec_alarms = ([{
                "code": "DEMO-SENSOR",
                "text": "SIMULATION: SpO₂ sensor disconnected",
                "level": "technical",
            }] if sensor_demo else [])
            store.ingest(chamber_id, {
                "source": "umec12",
                "patient": {"id": room["id"], "name": room["patient"]},
                "readings": umec_readings,
                "alarms": umec_alarms,
            })

            etco2 = round(_wave(room["etco2"], elapsed, 16, 2.1, phase), 1)
            ppeak = round(_wave(room["ppeak"], elapsed, 21, 1.6, phase), 1)
            peep = round(_wave(5, elapsed, 30, 0.25, phase), 1)
            vte = round(_wave(room["vte"], elapsed, 18, 18, phase))
            mv = round((vte * rr) / 1000, 2)
            fio2 = round(_wave(45 + chamber_id * 3, elapsed, 37, 2, phase), 1)

            pressure_demo = chamber_id == 2 and 18 <= (elapsed % 45) < 26
            wato_alarms = ([{
                "code": "DEMO-PRESSURE",
                "text": "SIMULATION: airway pressure warning",
                "level": "device",
            }] if pressure_demo else [])
            store.ingest(chamber_id, {
                "source": "wato",
                "readings": [
                    {"code": "MDC_CONC_AWAY_CO2_ET", "value": etco2, "unit": "mmHg"},
                    {"code": "MDC_VENT_PRESS_MAX", "value": ppeak, "unit": "cmH₂O"},
                    {"code": "MDC_VENT_PRESS_AWAY_END_EXP_POS", "value": peep, "unit": "cmH₂O"},
                    {"code": "MDC_VOL_AWAY_TIDAL", "value": vte, "unit": "mL"},
                    {"code": "MDC_VOL_MINUTE_AWAY", "value": mv, "unit": "L/min"},
                    {"code": "MDC_VENT_RESP_RATE", "value": rr, "unit": "rpm"},
                    {"code": "MDC_CONC_AWAY_O2_INSP", "value": fio2, "unit": "%"},
                    {"code": "MDC_CONC_MAC", "value": round(_wave(1.05, elapsed, 28, .08, phase), 2)},
                ],
                "alarms": wato_alarms,
            })

        nibp_tick = current_nibp_tick
        stop_event.wait(interval)

    store.set_demo_mode(False)
    print("[demo] simulated stream stopped")
