"""ACK-Manager: Pending-Map + Retransmission mit exponential backoff.

Nicht asyncio-basiert, damit der LoRa-Sender-Thread ihn direkt verwenden kann.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config_loader import AckConfig
from .protocol import Frame

log = logging.getLogger(__name__)


@dataclass
class Pending:
    frame: Frame
    sent_at: float
    retries: int
    next_retry_at: float


class AckManager:
    def __init__(self, cfg: AckConfig, sender: Callable[[Frame], bool]):
        self.cfg = cfg
        self._sender = sender
        self._pending: dict[int, Pending] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ack-mgr", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def next_seq(self) -> int:
        with self._lock:
            self._seq = (self._seq + 1) & 0xFF
            return self._seq

    def send_reliable(self, frame: Frame) -> None:
        """Sendet einen Frame mit ACK-Erwartung. Retransmits laufen im Hintergrund."""
        frame.ack_req = True
        now = time.time()
        with self._lock:
            self._pending[frame.seq] = Pending(
                frame=frame, sent_at=now, retries=0,
                next_retry_at=now + self.cfg.timeout_ms / 1000.0,
            )
        self._sender(frame)
        log.info("TX-reliable %s", frame)

    def send_fire_and_forget(self, frame: Frame) -> None:
        frame.ack_req = False
        self._sender(frame)
        log.info("TX %s", frame)

    def on_ack(self, ack_frame: Frame) -> None:
        with self._lock:
            entry = self._pending.pop(ack_frame.seq, None)
        if entry is None:
            log.debug("ACK für unbekannte seq=%d (evtl. spätes ACK)", ack_frame.seq)
            return
        log.info("ACK ok seq=%d rtt=%.0fms retries=%d",
                 ack_frame.seq, (time.time() - entry.sent_at) * 1000, entry.retries)

    # ------------------------------------------------------------ retry loop
    def _run(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.05)
            now = time.time()
            to_retry: list[Pending] = []
            with self._lock:
                for seq, p in list(self._pending.items()):
                    if now >= p.next_retry_at:
                        if p.retries >= self.cfg.max_retries:
                            log.warning("Dropping frame after %d retries: %s",
                                        p.retries, p.frame)
                            self._pending.pop(seq, None)
                            continue
                        to_retry.append(p)
            for p in to_retry:
                p.retries += 1
                p.frame.retry = True
                p.sent_at = time.time()
                backoff = self.cfg.timeout_ms / 1000.0 * (self.cfg.backoff_factor ** p.retries)
                p.next_retry_at = p.sent_at + backoff
                log.info("Retry %d/%d seq=%d (next in %.2fs)",
                         p.retries, self.cfg.max_retries, p.frame.seq, backoff)
                self._sender(p.frame)
