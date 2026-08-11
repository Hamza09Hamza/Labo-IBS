"""Small non-blocking HTTP publisher used by clinical device collectors."""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


class PortalPublisher:
    """Post normalized batches without delaying a medical-device socket.

    The capture loop only enqueues a small dictionary. A daemon worker handles
    localhost HTTP, drops the oldest batch if the queue ever backs up, and
    rate-limits connection-error noise when the portal is not running.
    """

    def __init__(self, base_url: str, chamber_id: int, source: str):
        self.endpoint = (
            base_url.rstrip("/") + f"/api/chambers/{int(chamber_id)}/readings"
        )
        self.source = source
        self._queue = queue.Queue(maxsize=120)
        self._last_error_at = 0.0
        self._thread = threading.Thread(
            target=self._run,
            name=f"portal-publisher-{source}-chamber-{chamber_id}",
            daemon=True,
        )
        self._thread.start()

    def publish(self, *, readings=None, patient=None, alarms=None):
        payload = {
            "source": self.source,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if readings is not None:
            payload["readings"] = readings
        if patient is not None:
            payload["patient"] = patient
        if alarms is not None:
            payload["alarms"] = alarms
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                pass

    def _run(self):
        while True:
            payload = self._queue.get()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                self.endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=0.75) as response:
                    response.read()
            except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                now = time.monotonic()
                if now - self._last_error_at >= 30:
                    print(f"[portal] could not publish {self.source} readings: {exc}")
                    self._last_error_at = now
