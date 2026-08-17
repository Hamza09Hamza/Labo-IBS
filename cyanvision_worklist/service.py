"""Persistent CYANVision worklist queue delivered through QRY/DSR/ACK."""

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
        self._pending_orders = []
        self._pending_query_segments = []
        self._last_status = "empty"
        self._control_id_second = None
        self._control_id_sequence = 0

    def status(self) -> dict:
        with self._lock:
            return {
                "listener_port": self.port,
                "armed": self._armed,
                "pending_ack": bool(self._pending_control_id),
                "status": self._last_status,
                "order": dict(self._order) if self._order else None,
                "api_ready_orders": len([
                    order for order in self.store.list_ready_cyanvision_orders()
                    if order.get("source") == "api"
                ]),
            }

    def set_instrument_port(self, port: int):
        with self._lock:
            self.port = int(port)

    def _next_control_id(self) -> str:
        """Return a unique, CY014-compliant MSH.10 value for this process.

        CY014 permits at most 20 characters in MSH.10 and requires MSA.2 to
        echo that exact value.  A microsecond timestamp exceeded the limit;
        a seconds-only timestamp fits but collides when continuation items are
        acknowledged and sent within the same second.  Two prefix characters,
        a 14-digit timestamp, and a four-digit per-second sequence use the
        full limit and remain unique for 10,000 DSR messages per second.
        """
        second = datetime.now().strftime("%Y%m%d%H%M%S")
        with self._lock:
            if second != self._control_id_second:
                self._control_id_second = second
                self._control_id_sequence = 0
            else:
                self._control_id_sequence += 1
            if self._control_id_sequence > 9999:
                raise RuntimeError("CYANVision control-ID capacity exceeded for one second")
            return f"CV{second}{self._control_id_sequence:04d}"

    def stage_and_arm(self, order: dict) -> dict:
        saved = self.store.upsert_cyanvision_order(order, source="manual", ready=True)
        with self._lock:
            self._order = dict(saved)
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
        if sample_id:
            self.store.cancel_cyanvision_order(sample_id)
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
            self._handle_ack(connection, segments)
            return True
        return False

    def _handle_query(self, connection, segments: list[str]):
        query_control_id = protocol.control_id(segments) or "0"
        ready_orders = self.store.list_ready_cyanvision_orders()
        with self._lock:
            self._pending_orders = [dict(order) for order in ready_orders]
            self._pending_query_segments = list(segments)
        order = ready_orders[0] if ready_orders else None
        response_control_id = self._next_control_id()
        if order:
            self.store.mark_cyanvision_query(order["sample_id"])
            with self._lock:
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
            continuation_pointer=(
                ready_orders[1]["sample_id"] if len(ready_orders) > 1 else ""
            ),
        )
        connection.sendall(protocol.frame(response))
        event_kind = "cyanvision_worklist_sent" if order else "cyanvision_no_worklist"
        message = (
            f"Sent DSR^Q03 worklist item 1 of {len(ready_orders)}; waiting for ACK^Q03 {response_control_id}"
            if order else "No CYANVision worklist was armed; sent final DSR^Q03 with QAK status NF"
        )
        self.store.add_event("host", event_kind, sample_id, message, "\n".join(response))

    def _handle_ack(self, connection, segments: list[str]):
        code, acknowledged_id, text = protocol.acknowledgement(segments)
        with self._lock:
            expected = self._pending_control_id
            sample_id = self._pending_sample_id
            # CYANVision does not reliably echo the DSR's MSH.10 back in
            # MSA.2: observed firmware instead echoes the DSC continuation
            # pointer, or sends an unfilled "#parMessageId#" template, and
            # its own MSH.10 is likewise a blank placeholder. The worklist
            # loop is single-flight (one outstanding DSR per connection),
            # so any ACK received while a request is pending is treated as
            # the reply to that request regardless of the ID it carries.
            matches = bool(expected)
            if matches and code == "AA":
                self._pending_control_id = None
                self._pending_sample_id = None
                if self._pending_orders and self._pending_orders[0]["sample_id"] == sample_id:
                    self._pending_orders.pop(0)
                remaining = [dict(order) for order in self._pending_orders]
                query_segments = list(self._pending_query_segments)
            elif matches:
                # A negative application ACK means the analyzer understood
                # the envelope but rejected this content. Stop automatic
                # retries so a malformed order cannot be offered repeatedly.
                self._armed = False
                self._pending_control_id = None
                self._pending_sample_id = None
                self._pending_orders = []
                self._pending_query_segments = []
                self._last_status = "rejected"
                remaining = []
                query_segments = []
            else:
                remaining = []
                query_segments = []
        if matches and code == "AA":
            self.store.mark_cyanvision_delivered(sample_id)
            with self._lock:
                if self._order and self._order.get("sample_id") == sample_id:
                    self._armed = False
            self.store.add_event(
                "instrument", "cyanvision_worklist_acknowledged", sample_id,
                "CYANVision positively acknowledged this worklist item; it is now delivered",
                "\n".join(segments),
            )
            if remaining:
                self._send_next_order(connection, remaining[0], query_segments, len(remaining) > 1)
            else:
                with self._lock:
                    self._pending_query_segments = []
                    self._last_status = "acknowledged"
        elif matches:
            self.store.mark_cyanvision_rejected(
                sample_id, f"MSA {code or '(empty)'}: {text}",
            )
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

    def _send_next_order(self, connection, order: dict, query_segments: list[str], has_more: bool):
        response_control_id = self._next_control_id()
        self.store.mark_cyanvision_query(order["sample_id"])
        with self._lock:
            self._pending_control_id = response_control_id
            self._pending_sample_id = order["sample_id"]
            self._last_status = "waiting_for_ack"
            next_sample = self._pending_orders[1]["sample_id"] if has_more else ""
        response = protocol.build_dsr(
            order,
            query_segments,
            response_control_id,
            protocol.control_id(query_segments) or "0",
            continuation_pointer=next_sample,
        )
        connection.sendall(protocol.frame(response))
        self.store.add_event(
            "host", "cyanvision_worklist_sent", order["sample_id"],
            f"Sent next DSR^Q03 worklist item; waiting for ACK^Q03 {response_control_id}",
            "\n".join(response),
        )

    def connection_closed(self):
        with self._lock:
            if not self._pending_control_id:
                return
            sample_id = self._pending_sample_id
            self._pending_control_id = None
            self._pending_sample_id = None
            self._pending_orders = []
            self._pending_query_segments = []
            self._last_status = "armed"
        self.store.add_event(
            "system", "cyanvision_ack_missing", sample_id,
            "CYANVision connection closed before ACK^Q03; worklist remains armed for retry",
        )
