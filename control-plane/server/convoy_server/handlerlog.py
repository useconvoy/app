"""Structured completion records for the device-facing write handlers (report, spool).

What they prove: that the server-side handler/service call for one device request RETURNED after its
transaction committed, with the device id, the safe sequence/lane fields, the applied/accepted/committed
result and the handler's elapsed monotonic time. What they are NOT: client-observed latency, nor an
isolated COMMIT latency (the elapsed time spans validation, the transaction and the response build).
Nothing from the request body, no tokens, nonces, boot ids, names, queries or exception text is ever
logged; failures carry only the HTTP status and the error class name."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("convoy.handler")

# the only keys a completion record may carry: everything else is dropped, never logged
ALLOWED = {
    "handler", "device_id", "seq", "lane", "records", "loss_ranges", "applied", "accepted",
    "committed_seq", "deferred", "rejected", "outcome", "status", "error_class", "started_utc",
    "handler_elapsed_ms",
}  # fmt: skip


def _safe_int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class HandlerTimer:
    """`with HandlerTimer("report", device_id, seq=...) as h:` around the service call; `h.done(...)`
    after it returned (i.e. after commit). A failure inside the block is logged with status and error
    class only, then re-raised."""

    def __init__(self, handler: str, device_id: str, **fields: Any):
        self.fields: dict[str, Any] = {"handler": handler, "device_id": device_id}
        for k, v in fields.items():
            if k in ALLOWED and v is not None:
                self.fields[k] = v
        self.t0 = time.monotonic()
        self.started = datetime.now(timezone.utc)
        self._done = False

    def __enter__(self) -> HandlerTimer:
        return self

    def done(self, **result: Any) -> None:
        rec = {**self.fields}
        for k, v in result.items():
            if k in ALLOWED and v is not None:
                rec[k] = v
        rec["outcome"] = "committed"
        self._emit(rec)
        self._done = True

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._done:
            return
        rec = {**self.fields, "outcome": "failed"}
        if exc is not None:
            rec["error_class"] = type(exc).__name__
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if _safe_int(status) is not None:
                rec["status"] = status
        self._emit(rec)

    def _emit(self, rec: dict[str, Any]) -> None:
        rec["started_utc"] = self.started.isoformat()
        rec["handler_elapsed_ms"] = round((time.monotonic() - self.t0) * 1000, 3)
        safe = {k: v for k, v in rec.items() if k in ALLOWED}
        log.info(
            "handler %s", " ".join(f"{k}={safe[k]}" for k in sorted(safe)), extra={"handler_record": safe}
        )
