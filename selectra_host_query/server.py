"""Dedicated Selectra TCP listener and Host Query transaction service."""

from __future__ import annotations

import socket
import threading
from random import SystemRandom

from . import protocol


PROBE_PATIENT_NAME = "APPELLE MANEL/FODHIL"  # exactly 20 chars: analyser limit
PROBE_TEST_POOL = (
    "SGOT", "SGPT", "Phosphatase ALP", "Creatinine",
    "Glucose pap sl", "Uree uv sl", "Cholesterol", "GGT",
)
PROBE_TEST_COUNT = 3


class SelectraHostQueryServer:
    def __init__(self, store, host="0.0.0.0", port=6103, armed=False, embedded=False):
        self.store = store
        self.host = host
        self.port = int(port)
        self.armed = bool(armed)
        self.embedded = bool(embedded)
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error = None
        self._lock = threading.Lock()
        self._clients = 0
        self._last_peer = None
        self._probe_armed = False
        self._probe_tests = list(PROBE_TEST_POOL[:PROBE_TEST_COUNT])

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
                "probe_armed": self._probe_armed,
                "probe_patient_name": PROBE_PATIENT_NAME,
                "probe_tests": list(self._probe_tests),
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

    def set_probe_armed(self, armed: bool):
        """Enable or disable wildcard diagnostic replies to every Selectra Q."""
        with self._lock:
            self._probe_armed = bool(armed)
            if self._probe_armed:
                self._probe_tests = SystemRandom().sample(
                    list(PROBE_TEST_POOL), PROBE_TEST_COUNT,
                )
            tests = list(self._probe_tests)
        state = "ARMED for every Q until manually disarmed" if armed else "DISARMED"
        self.store.add_event(
            "local", "continuous_probe_mode", None,
            f"Automatic alert probe is {state}: {PROBE_PATIENT_NAME}; tests {tests}",
        )
        return self._probe_armed

    def _active_probe(self) -> tuple[bool, list[str]]:
        """Read the persistent wildcard-probe state for the current query."""
        with self._lock:
            if not self._probe_armed:
                return False, []
            return True, list(self._probe_tests)

    @staticmethod
    def _probe_order(sample_id: str, tests: list[str]) -> dict:
        return {
            "sample_id": sample_id,
            "patient_id": "CALL-TECH",
            "family_name": PROBE_PATIENT_NAME,
            "given_name": "",
            "birth_date": "",
            "sex": "M",
            "specimen_type": "Normal",
            "tests": tests,
            # Preserve the analyser's existing patient demographics. The
            # manual says conflicting names or birth dates produce O-26=X.
            "preserve_analyser_demographics": True,
            # Append the tests to an existing request (or create one when it
            # does not exist) instead of replacing the sample request.
            "action_code": "A",
            "outbound_specimen_type": "Normal",
            # Keep the human-visible diagnostic text in the documented O-17
            # Ordering Physician field, whose maximum is also 20 characters.
            "ordering_physician": PROBE_PATIENT_NAME,
        }

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
        socket accepted by LaboBridge on port 6003. A reply is possible only
        for an exact staged sample-ID match. Manual bench orders require the
        global arm switch; authenticated API orders carry their own persisted
        ready flag and therefore remain available across process restarts.
        """
        rejections = protocol.application_rejections(records)
        for rejection in rejections:
            sample_id = rejection["sample_id"]
            message = (
                "Selectra rejected the host order at application level (O-26=X); "
                "transport ACK alone did not mean the request was accepted"
            )
            if sample_id and self.store.get_order(sample_id):
                self.store.mark_rejected(sample_id, message)
            self.store.add_event(
                "instrument", "application_rejected", sample_id or None,
                message, rejection["record"],
            )

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
        probe_active, probe_tests = self._active_probe()
        is_probe = False
        order = None
        if probe_active:
            sample_id = next((value for value in candidates if 0 < len(value) <= 12), "")
            if not sample_id:
                self.store.add_event(
                    "system", "continuous_probe_rejected", None,
                    f"Continuous probe ignored a query with no valid 1-12 character sample ID: {candidates}",
                    "\n".join(query_records),
                )
                return
            order = self._probe_order(sample_id, probe_tests)
            self.store.upsert_order(order)
            is_probe = True
            self.store.add_event(
                "host", "continuous_probe_triggered", sample_id,
                f"Continuous probe is answering sample {sample_id}; sending {PROBE_PATIENT_NAME} with tests {probe_tests}",
            )
        else:
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
        response = protocol.build_order_records(order)
        is_api_order = order.get("source") == "api"
        api_ready = is_api_order and bool(order.get("ready"))
        may_send = is_probe or api_ready or (self.armed and not is_api_order)
        if not may_send:
            reason = (
                "API order is no longer ready (already delivered, cancelled, or rejected)"
                if is_api_order else "live responses are disarmed"
            )
            self.store.add_event("host", "response_blocked", sample_id,
                                 f"Order response built but not sent because {reason}",
                                 "\n".join(response))
            return
        if api_ready:
            self.store.add_event(
                "host", "api_order_triggered", sample_id,
                "Authenticated API order matched exactly and is being sent to Selectra",
            )
        try:
            self._send_transaction(connection, response, sample_id)
            self.store.mark_transport_acknowledged(sample_id)
            if is_probe:
                self.store.add_event(
                    "host", "continuous_probe_transport_acknowledged", sample_id,
                    "Selectra ACKed the continuous-probe frame; application acceptance is still pending",
                )
        except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            message = f"Host response failed: {exc}"
            self.store.mark_error(sample_id, message)
            self.store.add_event("system", "response_error", sample_id, message)

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
        # This Selectra carries the complete LIS2-A message in one frame. Its
        # real Q capture is one H/Q/L frame; sending H, P, O and L as four
        # independent frames yields four incomplete application messages that
        # can be ACKed at the transport layer and still be silently ignored.
        message = "\r".join(records)
        frame = protocol.build_frame(1, message)
        acknowledged = False
        for attempt in range(1, 4):
            connection.sendall(frame)
            self.store.add_event(
                "host", "frame_sent", sample_id,
                f"Sent complete H/P/O/L message in one ASTM frame (attempt {attempt})",
                "\n".join(records),
            )
            if self._recv_control(connection) == protocol.ACK:
                acknowledged = True
                break
        if not acknowledged:
            raise ConnectionError("Selectra rejected the complete order frame three times")
        connection.sendall(protocol.B_EOT)
        self.store.add_event(
            "host", "transport_acknowledged", sample_id,
            f"Selectra ACKed {len(records)} records in one message frame; released the line",
            "<EOT>",
        )
