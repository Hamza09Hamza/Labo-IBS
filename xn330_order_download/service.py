"""Exact-ID, manually armed XN-330 Host Query delivery service."""

from __future__ import annotations

import socket
import threading

from labo_bridge.protocols import astm

from . import protocol


class XN330OrderDownloadService:
    def __init__(self, store, port: int = 6001):
        self.store = store
        self.port = int(port)
        self._lock = threading.Lock()
        self._clients = 0
        self._last_peer = None

    def status(self) -> dict:
        with self._lock:
            return {
                "listener_port": self.port,
                "connected_clients": self._clients,
                "last_peer": self._last_peer,
                "armed_orders": len(self.store.list_ready_xn330_orders()),
            }

    def set_instrument_port(self, port: int):
        with self._lock:
            self.port = int(port)

    def client_connected(self, peer: str):
        with self._lock:
            self._clients += 1
            self._last_peer = peer
        self.store.add_event("instrument", "xn330_connected", None, f"XN-330 connected from {peer}")

    def client_disconnected(self, peer: str):
        with self._lock:
            self._clients = max(0, self._clients - 1)
        self.store.add_event("instrument", "xn330_disconnected", None, f"XN-330 disconnected: {peer}")

    def preview(self, sample_id: str) -> list[str]:
        order = self.store.get_xn330_order(sample_id)
        if not order:
            raise KeyError(sample_id)
        return protocol.build_order_records(order)

    def handle_records(self, connection: socket.socket, records: list[str]):
        queries = []
        for record in records:
            details = protocol.query_details(record)
            if details:
                queries.append((record, details))
        if not queries:
            # Unlike Selectra, we don't yet know whether/how the XN-330
            # reports application-level acceptance or rejection of a
            # downloaded order - a transport ACK only proves the analyzer
            # received the bytes, not that it queued the tests (this was
            # exactly the gap that hid Selectra's real rejection reason for
            # weeks). Capture whatever comes in shortly after a real
            # delivery so a genuine reply can be decoded from evidence, the
            # same way Selectra's O-26=X was found - not guessed at first.
            if records and self.store.has_recent_xn330_delivery():
                self.store.add_event(
                    "instrument", "xn330_post_delivery_batch", None,
                    f"Received {len(records)} non-query record(s) shortly after an XN-330 "
                    "order was delivered; capturing raw content to check for an "
                    "application-level accept/reject signal (shape not yet confirmed)",
                    "\n".join(records),
                )
            return

        raw_queries = "\n".join(record for record, _ in queries)
        sample_ids = list(dict.fromkeys(item["sample_id"] for _, item in queries))
        self.store.add_event(
            "instrument", "xn330_query_received", sample_ids[0] if len(sample_ids) == 1 else None,
            f"XN-330 requested analysis order data for {sample_ids}", raw_queries,
        )
        matches = []
        for record, details in queries:
            order = self.store.get_xn330_order(details["sample_id"])
            if order:
                matches.append((order, details["selector"], record))
        unique = {item[0]["sample_id"]: item for item in matches}
        if len(unique) != 1:
            self.store.add_event(
                "system", "xn330_query_unmatched", None,
                f"No single staged XN-330 order exactly matched {sample_ids}", raw_queries,
            )
            return

        order, selector, query_record = next(iter(unique.values()))
        sample_id = order["sample_id"]
        self.store.mark_xn330_query(sample_id)
        if not order.get("ready"):
            self.store.add_event(
                "host", "xn330_response_blocked", sample_id,
                "Matching XN-330 order is staged but not armed; no patient or test payload was sent",
                query_record,
            )
            return

        response = protocol.build_order_records(order, selector)
        self.store.add_event(
            "host", "xn330_order_triggered", sample_id,
            "Armed exact-ID XN-330 order matched and is being sent", "\n".join(response),
        )
        try:
            self._send_transaction(connection, response, sample_id)
            self.store.mark_xn330_delivered(sample_id)
        except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            message = f"XN-330 order response failed: {exc}"
            self.store.mark_xn330_error(sample_id, message)
            self.store.add_event("system", "xn330_response_error", sample_id, message)
            return

        # Real test history (2026-08-17, samples 2608217107-2608217109):
        #   1. Connection left open after EOT (original behavior) ->
        #      analyzer's own interface reports "host communication timeout".
        #   2. H-4 populated, connection still left open -> analyzer reports
        #      "TCP/IP transmission error on host computer" instead.
        #   3. Host force-closes the connection immediately after EOT ->
        #      the ORIGINAL "host communication timeout" error came back.
        # All three ACKed cleanly at the ASTM level every time; every error
        # is connection-related, not content-related, and forcing an
        # immediate close (attempt 3) did not fix it and reproduced attempt
        # 1's exact error - consistent with the analyzer still expecting
        # something (its own processing time, or to be the one to end the
        # session) that an instant unilateral close cuts off. Not closing
        # the connection ourselves at all here; leaving the socket for the
        # outer ASTM loop's normal idle handling (labo_bridge/server.py's
        # CONNECTION_IDLE_TIMEOUT_SECONDS) to let the XN-330 close its own
        # side in its own time, or time out naturally without a comm error
        # attributable to an abrupt host-side close.

    def _recv_control(self, connection: socket.socket) -> int:
        while True:
            data = connection.recv(1)
            if not data:
                raise ConnectionError("XN-330 closed the connection while an ACK was expected")
            byte = data[0]
            self.store.add_event(
                "instrument", "xn330_control", None,
                protocol.CONTROL_NAMES.get(byte, f"0x{byte:02X}"), protocol.visible_bytes(data),
            )
            if byte in (astm.ACK, astm.NAK):
                return byte

    def _send_transaction(self, connection: socket.socket, records: list[str], sample_id: str):
        connection.sendall(astm.B_ENQ)
        self.store.add_event("host", "xn330_enq", sample_id, "Requested the XN-330 ASTM line", "<ENQ>")
        if self._recv_control(connection) != astm.ACK:
            raise ConnectionError("XN-330 rejected host ENQ")

        frames = protocol.build_message_frames(records)
        for frame_index, frame in enumerate(frames, start=1):
            acknowledged = False
            for attempt in range(1, 4):
                connection.sendall(frame)
                self.store.add_event(
                    "host", "xn330_frame_sent", sample_id,
                    f"Sent XN-330 ASTM frame {frame_index}/{len(frames)} (attempt {attempt})",
                    protocol.visible_bytes(frame),
                )
                if self._recv_control(connection) == astm.ACK:
                    acknowledged = True
                    break
            if not acknowledged:
                raise ConnectionError(f"XN-330 rejected ASTM frame {frame_index} three times")

        connection.sendall(astm.B_EOT)
        self.store.add_event(
            "host", "xn330_transport_acknowledged", sample_id,
            f"XN-330 ACKed {len(frames)} frame(s); order consumed and line released", "<EOT>",
        )
