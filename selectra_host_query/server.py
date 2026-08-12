"""Dedicated Selectra TCP listener and Host Query transaction service."""

from __future__ import annotations

import socket
import threading
import time

from . import protocol


class SelectraHostQueryServer:
    def __init__(self, store, host="0.0.0.0", port=6103, armed=False, embedded=False,
                 variant_delay_seconds=1.0):
        self.store = store
        self.host = host
        self.port = int(port)
        self.armed = bool(armed)
        self.embedded = bool(embedded)
        self.variant_delay_seconds = float(variant_delay_seconds)
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error = None
        self._lock = threading.Lock()
        self._clients = 0
        self._last_peer = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, name="selectra-host-query", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3):
            raise RuntimeError("Selectra listener did not become ready within 3 seconds")
        if self._startup_error:
            raise OSError(
                f"Could not open Selectra listener on {self.host}:{self.port}: "
                f"{self._startup_error}"
            ) from self._startup_error

    def stop(self):
        self._stop.set()
        listener = self._listener
        if listener:
            try:
                listener.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def status(self):
        with self._lock:
            return {
                "listener_host": self.host,
                "listener_port": self.port,
                "armed": self.armed,
                "connected_clients": self._clients,
                "last_peer": self._last_peer,
                "running": self.embedded or bool(self._thread and self._thread.is_alive()),
            }

    def set_armed(self, armed: bool):
        """Enable or disable real order replies without restarting the bridge."""
        with self._lock:
            self.armed = bool(armed)
        state = "ARMED" if self.armed else "DISARMED"
        self.store.add_event(
            "local", "live_response_mode", None,
            f"Selectra exact-ID order replies are now {state}",
        )
        return self.armed

    def set_instrument_port(self, port: int):
        """Keep the page in sync when LaboBridge changes its live port."""
        with self._lock:
            self.port = int(port)

    def client_connected(self, peer: str):
        """Track a client owned by the production port-6003 listener."""
        with self._lock:
            self._clients += 1
            self._last_peer = peer
        self.store.add_event(
            "instrument", "connected", None,
            f"Selectra TCP client connected from {peer}",
        )

    def client_disconnected(self, peer: str):
        with self._lock:
            self._clients = max(0, self._clients - 1)
        self.store.add_event(
            "instrument", "disconnected", None,
            f"Selectra TCP client disconnected: {peer}",
        )

    def preview(self, sample_id: str, simulated=False):
        order = self.store.get_order(sample_id)
        if not order:
            raise KeyError(sample_id)
        records = protocol.build_order_records(order)
        if simulated:
            self.store.mark_query(sample_id)
            self.store.mark_delivered(sample_id, simulated=True)
            self.store.add_event("simulator", "query", sample_id,
                                 "Simulated an exact-ID Selectra query", f"Q|1|^{sample_id}^")
            self.store.add_event("host", "response_preview", sample_id,
                                 f"Built {len(records)} LIS2-A order records; no network bytes sent",
                                 "\n".join(records))
        return records

    def _run(self):
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen(5)
            listener.settimeout(1)
            self._listener = listener
        except OSError as exc:
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        self.store.add_event("system", "listener_started", None,
                             f"Selectra test listener opened on {self.host}:{self.port}; live responses {'ARMED' if self.armed else 'DISARMED'}")
        try:
            while not self._stop.is_set():
                try:
                    connection, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                thread = threading.Thread(
                    target=self._handle_client, args=(connection, address),
                    name=f"selectra-client-{address[0]}", daemon=True,
                )
                thread.start()
        finally:
            try:
                listener.close()
            except OSError:
                pass

    def _handle_client(self, connection: socket.socket, address):
        peer = f"{address[0]}:{address[1]}"
        self.client_connected(peer)
        connection.settimeout(90)
        buffer = b""
        records = []
        try:
            while not self._stop.is_set():
                data = connection.recv(4096)
                if not data:
                    break
                buffer += data
                self.store.add_event("instrument", "bytes", None, f"Received {len(data)} byte(s)", protocol.visible_bytes(data))
                while buffer:
                    first = buffer[0]
                    if first == protocol.ENQ:
                        connection.sendall(protocol.B_ACK)
                        self.store.add_event("host", "ack", None, "Acknowledged Selectra ENQ", "<ACK>")
                        buffer = buffer[1:]
                    elif first == protocol.EOT:
                        buffer = buffer[1:]
                        self.store.add_event("instrument", "eot", None, "Selectra completed its transaction", "<EOT>")
                        self.handle_records(connection, records)
                        records = []
                    elif first == protocol.STX:
                        end = buffer.find(bytes([protocol.LF]))
                        if end < 0:
                            break
                        frame, buffer = buffer[:end + 1], buffer[end + 1:]
                        try:
                            payload = protocol.decode_frame(frame)
                        except ValueError as exc:
                            connection.sendall(protocol.B_NAK)
                            self.store.add_event("host", "nak", None, str(exc), protocol.visible_bytes(frame))
                            continue
                        connection.sendall(protocol.B_ACK)
                        decoded = protocol.split_records(payload)
                        records.extend(decoded)
                        self.store.add_event("instrument", "frame", None,
                                             f"Accepted frame containing {len(decoded)} record(s)", payload)
                    elif first in (protocol.ACK, protocol.NAK):
                        self.store.add_event("instrument", "control", None,
                                             protocol.CONTROL_NAMES[first], protocol.visible_bytes(buffer[:1]))
                        buffer = buffer[1:]
                    else:
                        self.store.add_event("instrument", "unexpected_byte", None,
                                             f"Discarded unexpected byte 0x{first:02X}")
                        buffer = buffer[1:]
        except (ConnectionResetError, TimeoutError, socket.timeout, OSError) as exc:
            self.store.add_event("system", "connection_closed", None, f"Selectra connection ended: {exc}")
        finally:
            try:
                connection.close()
            except OSError:
                pass
            self.client_disconnected(peer)

    def handle_records(self, connection: socket.socket, records: list[str]):
        """Process one complete analyzer batch after its EOT.

        In embedded mode ``connection`` is the existing production Selectra
        socket accepted by LaboBridge on port 6003.  A reply is possible only
        for an exact staged sample-ID match and only while explicitly armed.
        """
        query_records = [record for record in records if record.lstrip("01234567").startswith("Q|")]
        if not query_records:
            if records:
                self.store.add_event("system", "non_query_batch", None,
                                     f"Received {len(records)} record(s), but no Q record; no order response sent",
                                     "\n".join(records))
            return
        candidates = []
        for record in query_records:
            candidates.extend(protocol.query_candidates(record))
        candidates = list(dict.fromkeys(candidates))
        self.store.add_event(
            "instrument", "query_received", None,
            f"Selectra requested order data for candidates: {candidates}",
            "\n".join(query_records),
        )
        order = self.store.resolve_candidates(candidates)
        if not order:
            self.store.add_event("system", "query_unmatched", None,
                                 f"No single staged order exactly matched query candidates: {candidates}",
                                 "\n".join(query_records))
            return
        sample_id = order["sample_id"]
        self.store.mark_query(sample_id)
        self.store.add_event("instrument", "query_matched", sample_id,
                             f"Matched exact sample ID {sample_id}", "\n".join(query_records))
        variants = protocol.build_order_variants(order)
        if not self.armed:
            self.store.add_event("host", "response_blocked", sample_id,
                                 f"{len(variants)} order-record variant(s) built but not sent because live responses are disarmed",
                                 "\n\n".join(f"--- {label} ---\n" + "\n".join(records) for label, records in variants))
            return
        # Send every candidate schema variant, one full ASTM transaction each,
        # back to back on this same connection, with a 1s pause between them
        # so an operator watching the Selectra's screen can see exactly when
        # each variant lands and tell which one (if any) actually changes
        # anything. Each variant is clearly labeled in the trace so it's easy
        # to match "what appeared on screen" to "which shape caused it".
        for variant_index, (label, response) in enumerate(variants):
            self.store.add_event("host", "variant_attempt", sample_id,
                                 f"Sending brute-force schema variant {variant_index + 1}/{len(variants)}: {label}",
                                 "\n".join(response))
            try:
                self._send_transaction(connection, response, sample_id)
            except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                message = f"Variant {variant_index + 1}/{len(variants)} ({label}) failed: {exc}"
                self.store.mark_error(sample_id, message)
                self.store.add_event("system", "response_error", sample_id, message)
                return
            if variant_index < len(variants) - 1 and self.variant_delay_seconds > 0:
                time.sleep(self.variant_delay_seconds)
        self.store.mark_delivered(sample_id)

    # Kept for callers/tests written before the production bridge integration.
    _handle_records = handle_records

    def _recv_control(self, connection: socket.socket) -> int:
        while True:
            data = connection.recv(1)
            if not data:
                raise ConnectionError("Selectra closed the connection while an ACK was expected")
            byte = data[0]
            self.store.add_event("instrument", "control", None,
                                 protocol.CONTROL_NAMES.get(byte, f"0x{byte:02X}"), protocol.visible_bytes(data))
            if byte in (protocol.ACK, protocol.NAK):
                return byte

    def _send_transaction(self, connection: socket.socket, records: list[str], sample_id: str):
        connection.sendall(protocol.B_ENQ)
        self.store.add_event("host", "enq", sample_id, "Requested the LIS2-A line", "<ENQ>")
        if self._recv_control(connection) != protocol.ACK:
            raise ConnectionError("Selectra rejected host ENQ")
        for index, record in enumerate(records, start=1):
            frame = protocol.build_frame(index, record)
            acknowledged = False
            for attempt in range(1, 4):
                connection.sendall(frame)
                self.store.add_event("host", "frame_sent", sample_id,
                                     f"Sent order frame {index}/{len(records)} (attempt {attempt})", record)
                if self._recv_control(connection) == protocol.ACK:
                    acknowledged = True
                    break
            if not acknowledged:
                raise ConnectionError(f"Selectra rejected order frame {index} three times")
        connection.sendall(protocol.B_EOT)
        self.store.add_event("host", "response_delivered", sample_id,
                             f"Delivered {len(records)} order records and released the line", "<EOT>")
