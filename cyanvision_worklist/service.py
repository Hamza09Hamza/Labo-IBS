"""One controlled CYANVision worklist item, delivered on the next QRY^Q02."""

from __future__ import annotations

import threading
from datetime import datetime

from . import protocol


class CyanVisionWorklistService:
    def __init__(self, event_store, port: int = 6004):
        self.store = event_store
        self.port = int(port)
        self._lock = threading.Lock()
        self._order = None
        self._armed = False
        self._pending_control_id = None
        self._pending_sample_id = None
        self._last_status = "empty"

    def status(self) -> dict:
        with self._lock:
            return {
                "listener_port": self.port,
                "armed": self._armed,
                "pending_ack": bool(self._pending_control_id),
                "status": self._last_status,
                "order": dict(self._order) if self._order else None,
            }

    def set_instrument_port(self, port: int):
        with self._lock:
            self.port = int(port)

    def stage_and_arm(self, order: dict) -> dict:
        with self._lock:
            self._order = dict(order)
            self._armed = True
            self._pending_control_id = None
            self._pending_sample_id = None
            self._last_status = "armed"
        self.store.add_event(
            "local", "cyanvision_worklist_armed", order["sample_id"],
            f"CYANVision one-load worklist armed for {order['sample_id']} / {order['test_code']}",
            "\n".join(self.preview(order)),
        )
        return self.status()

    def disarm(self) -> dict:
        with self._lock:
            sample_id = self._order.get("sample_id") if self._order else None
            self._armed = False
            self._pending_control_id = None
            self._pending_sample_id = None
            self._last_status = "disarmed"
        self.store.add_event(
            "local", "cyanvision_worklist_disarmed", sample_id,
            "CYANVision one-load worklist manually disarmed",
        )
        return self.status()

    @staticmethod
    def preview(order: dict) -> list[str]:
        query = [
            "MSH|^~\\&|CYPRESS|CYANVISION|||||QRY^Q02|QUERY-ID|P|2.3.1",
            "QRD|20070723170000|R|D|1|||RD||OTH|||T|",
            "QRF|CyanVision|20070723000000|20070723170000|||RCT|COR|ALL||",
        ]
        return protocol.build_dsr(order, query, "WORKLIST-ID", "QUERY-ID")

    def handle_message(self, connection, segments: list[str]) -> bool:
        kind = protocol.message_type(segments)
        if kind == "QRY^Q02":
            self._handle_query(connection, segments)
            return True
        if kind in {"ACK^Q03", "ACK"}:
            self._handle_ack(segments)
            return True
        return False

    def _handle_query(self, connection, segments: list[str]):
        query_control_id = protocol.control_id(segments) or "0"
        with self._lock:
            order = dict(self._order) if self._armed and self._order else None
            response_control_id = "CV" + datetime.now().strftime("%Y%m%d%H%M%S%f")
            if order:
                self._pending_control_id = response_control_id
                self._pending_sample_id = order["sample_id"]
                self._last_status = "waiting_for_ack"
        sample_id = order["sample_id"] if order else None
        self.store.add_event(
            "instrument", "cyanvision_query_received", sample_id,
            f"CYANVision requested a LIS worklist (control {query_control_id})",
            "\n".join(segments),
        )
        response = protocol.build_dsr(
            order, segments, response_control_id, query_control_id,
        )
        connection.sendall(protocol.frame(response))
        event_kind = "cyanvision_worklist_sent" if order else "cyanvision_no_worklist"
        message = (
            f"Sent one final DSR^Q03 worklist item; waiting for ACK^Q03 {response_control_id}"
            if order else "No CYANVision worklist was armed; sent final DSR^Q03 with QAK status NF"
        )
        self.store.add_event("host", event_kind, sample_id, message, "\n".join(response))

    def _handle_ack(self, segments: list[str]):
        code, acknowledged_id, text = protocol.acknowledgement(segments)
        with self._lock:
            expected = self._pending_control_id
            sample_id = self._pending_sample_id
            matches = bool(expected and acknowledged_id == expected)
            if matches and code == "AA":
                self._armed = False
                self._pending_control_id = None
                self._pending_sample_id = None
                self._last_status = "acknowledged"
            elif matches:
                # A negative application ACK means the analyzer understood
                # the envelope but rejected this content. Stop automatic
                # retries so a malformed order cannot be offered repeatedly.
                self._armed = False
                self._pending_control_id = None
                self._pending_sample_id = None
                self._last_status = "rejected"
        if matches and code == "AA":
            self.store.add_event(
                "instrument", "cyanvision_worklist_acknowledged", sample_id,
                "CYANVision positively acknowledged the worklist; the one-load order is now disarmed",
                "\n".join(segments),
            )
        elif matches:
            self.store.add_event(
                "instrument", "cyanvision_worklist_rejected", sample_id,
                f"CYANVision rejected the worklist with MSA code {code or '(empty)'}: {text}",
                "\n".join(segments),
            )
        else:
            self.store.add_event(
                "instrument", "cyanvision_ack_unmatched", None,
                f"Received ACK for {acknowledged_id or '(empty)'}, expected {expected or '(none)'}",
                "\n".join(segments),
            )

    def connection_closed(self):
        with self._lock:
            if not self._pending_control_id:
                return
            sample_id = self._pending_sample_id
            self._pending_control_id = None
            self._pending_sample_id = None
            self._last_status = "armed"
        self.store.add_event(
            "system", "cyanvision_ack_missing", sample_id,
            "CYANVision connection closed before ACK^Q03; worklist remains armed for retry",
        )
