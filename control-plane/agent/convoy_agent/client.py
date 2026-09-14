"""Outbound HTTPS client (stdlib). Retries with jittered exponential backoff; tracks the server clock
offset; never logs credentials. All calls are bounded in time."""

from __future__ import annotations

import http.client
import json
import logging
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import __version__
from .ids import parse_iso_ts

log = logging.getLogger("convoy.agent.client")


class ApiError(Exception):
    def __init__(self, status: int, body: Any):
        super().__init__(f"HTTP {status}: {str(body)[:200]}")
        self.status = status
        self.body = body


class Transient(Exception):
    pass


class _Inflight:
    """Sockets of requests currently in flight on one Client. A stop cannot interrupt a thread blocked in
    a TLS handshake or a read by setting an Event: the thread only notices when the call returns.
    Shutting the socket down from the stopping thread makes that call fail at once (the peer is gone
    as far as the caller is concerned), which turns the I/O timeout into an upper bound rather than
    the expected wait. The owning thread still closes its own socket afterwards.

    Deliberately LOCK-FREE: `abort()` may run inside a signal handler on the very thread that is in
    the middle of `add()`/`discard()`, so no lock may be taken here. `set.add`, `set.discard` and
    `list(set)` are single opcodes that complete under the GIL, which is all the atomicity needed.
    Not covered: the connect/DNS phase before a socket exists (bounded by the socket timeout)."""

    def __init__(self) -> None:
        self._socks: set[socket.socket] = set()
        self.stop_requested: Callable[[], bool] = lambda: False  # the client's stop predicate

    def add(self, sock: socket.socket) -> None:
        # a socket stays registered for the whole life of its file descriptor: http.client "closes" the
        # connection object as soon as the response takes the socket over, but the response's makefile
        # keeps the transport open and reads the BODY through it. Closed descriptors are pruned here.
        for old in list(self._socks):
            if old.fileno() < 0:
                self._socks.discard(old)
        self._socks.add(sock)

    def discard(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        self._socks.discard(sock)

    def abort(self) -> int:
        socks = list(self._socks)  # atomic snapshot; no lock
        n = 0
        for sk in socks:
            if sk.fileno() < 0:
                continue
            try:
                # the plain-socket shutdown, also for TLS sockets: it wakes the blocked call with a clean
                # EOF/reset without detaching the SSL object the owning thread is still using
                socket.socket.shutdown(sk, socket.SHUT_RDWR)
                n += 1
            except OSError:
                pass  # not connected yet, or already closed by its owner
        return n

    def __len__(self) -> int:
        return sum(1 for s in list(self._socks) if s.fileno() >= 0)

    def register(self, sock: socket.socket) -> None:
        """Register, then re-check the stop predicate: a stop that raced ahead of this registration
        (abort() found nothing to shut down) is applied here, so no request survives past a stop for
        longer than its next I/O boundary."""
        self.add(sock)
        if self.stop_requested():
            try:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
            except OSError:
                pass
            raise OSError("stopping: control-plane request abandoned before I/O")


def _tracked_connections(registry: _Inflight):
    """http.client connection classes that register their sockets with `registry` for the lifetime of
    the connection: the plain socket from connect() and the TLS socket from the handshake on, so an
    abort reaches a handshake in progress as well as a stalled response."""

    class TrackedSSLSocket(ssl.SSLSocket):
        def do_handshake(self, block: bool = False) -> None:
            registry.register(self)
            super().do_handshake(block)

        def _real_close(self, *args, **kwargs) -> None:
            # the descriptor actually goes away only when the last makefile() reader is closed too:
            # unregister here, never in close() (which the connection calls while the body is unread)
            registry.discard(self)
            super()._real_close(*args, **kwargs)

    class TrackedHTTPConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            super().connect()
            registry.register(self.sock)

    class TrackedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self) -> None:
            # the stdlib sequence (TCP connect, then wrap with SNI); the TLS socket registers itself
            # through the context's sslsocket_class during the handshake and stays registered until closed
            http.client.HTTPConnection.connect(self)
            plain = self.sock
            registry.register(plain)
            try:
                server_hostname = self._tunnel_host if self._tunnel_host else self.host
                self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)
            finally:
                registry.discard(plain)  # detached by wrap_socket: the TLS socket owns the descriptor
            registry.register(self.sock)

    class TrackedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(TrackedHTTPConnection, req)

    class TrackedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(TrackedHTTPSConnection, req, context=self._context)

    return TrackedSSLSocket, TrackedHTTPHandler, TrackedHTTPSHandler


class Client:
    def __init__(
        self,
        base_url: str,
        credential: str | None = None,
        *,
        timeout: float = 20.0,
        ca_file: str | None = None,
        insecure: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.credential = credential
        self.timeout = timeout
        self.server_offset_s: float | None = None
        self.last_server_time: float | None = None
        # Trust is split by origin: the control plane may be signed by a private CA supplied via
        # --ca-file, which is ADDED to the system roots (never replaces them); public artifact origins
        # (Hugging Face / CDN redirects) validate against the system roots only. Hostname and
        # certificate validation stay on for both. `insecure` only ever relaxes the control-plane
        # context (LAN self-signed), never the public one.
        self.public_ctx: ssl.SSLContext = ssl.create_default_context()
        self.ctx: ssl.SSLContext | None = None
        if self.base_url.startswith("https"):
            self.ctx = ssl.create_default_context()
            if ca_file:
                self.ctx.load_verify_locations(cafile=ca_file)
            if insecure:
                self.ctx.check_hostname = False
                self.ctx.verify_mode = ssl.CERT_NONE
        self.attempts = 0
        # Stop awareness (bounded shutdown): `stop` is the agent's stop Event. No new attempt starts once
        # it is set, retry backoff waits on it instead of sleeping, and `abort_inflight()` wakes any
        # request already blocked in a connect/handshake/read. Verification is untouched: the tracked
        # socket classes only register sockets; both contexts keep hostname and certificate checks.
        self.stop: threading.Event | None = None
        # a plain flag a signal handler may set without touching any lock (the Event is set by the
        # agent's loop afterwards); either makes the client stop
        self.stop_flag = False
        self._inflight = _Inflight()
        self._inflight.stop_requested = self.stopping
        sslsock_cls, self._http_handler, self._https_handler = _tracked_connections(self._inflight)
        self.public_ctx.sslsocket_class = sslsock_cls
        if self.ctx is not None:
            self.ctx.sslsocket_class = sslsock_cls
        self._opener = urllib.request.build_opener(
            self._http_handler(), self._https_handler(context=self.ctx)
        )

    def stopping(self) -> bool:
        return self.stop_flag or (self.stop is not None and self.stop.is_set())

    def abort_inflight(self) -> int:
        """Wake every request of this client that is blocked in I/O (see _Inflight); returns how many
        sockets were shut down. Signal-handler safe: sets the plain flag and takes no lock."""
        self.stop_flag = True
        return self._inflight.abort()

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        raw: bytes | None = None,
        content_type: str | None = None,
        stream: bool = False,
    ):
        url = f"{self.base_url}{path}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", f"convoy-agent/{__version__}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", content_type or "application/json")
        if self.credential:
            req.add_header("Authorization", f"Bearer {self.credential}")
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = payload[:200].decode("utf-8", "replace")
            if e.code in (502, 503, 504, 429):
                raise Transient(f"HTTP {e.code}") from e
            raise ApiError(e.code, parsed) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, OSError) as e:
            raise Transient(str(e)[:200]) from e
        if stream:
            return resp
        try:
            payload = resp.read()
        except (http.client.HTTPException, OSError, ssl.SSLError) as e:
            # the body ended early (peer reset, or the transport shut down by a stop): transient
            raise Transient(f"response body incomplete: {str(e)[:120]}") from e
        try:
            out = json.loads(payload) if payload else {}
        except json.JSONDecodeError as e:
            raise Transient("non-JSON response") from e
        if isinstance(out, dict) and out.get("server_time"):
            self._note_server_time(out["server_time"])
        return out

    def _note_server_time(self, iso: str) -> None:
        try:
            st = parse_iso_ts(iso)
        except Exception:
            return
        self.last_server_time = st
        self.server_offset_s = st - time.time()

    def call(self, method: str, path: str, body: Any = None, *, retries: int = 4, **kw) -> Any:
        """Bounded retry. With a stop Event set: no new attempt is started, a backoff in progress ends
        immediately, and an attempt that was aborted by `abort_inflight()` surfaces as Transient without
        further retries, so the caller's wall time after a stop is at most the aborted attempt's return."""
        delay = 1.0
        for attempt in range(retries + 1):
            if self.stopping():
                raise Transient("stopping: no further control-plane attempts")
            self.attempts += 1
            try:
                return self._request(method, path, body, **kw)
            except Transient as e:
                if attempt == retries or self.stopping():
                    raise
                sleep = min(30.0, delay) * (0.5 + random.random())
                log.debug("transient %s on %s %s; retry in %.1fs", e, method, path, sleep)
                # sliced so a stop requested by a signal handler (plain flag, no Event) ends the
                # backoff within one slice
                end = time.monotonic() + sleep
                while True:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    if self.stop is not None:
                        self.stop.wait(min(0.25, left))
                    else:
                        time.sleep(min(0.25, left))
                    if self.stopping():
                        raise Transient("stopping: retry backoff interrupted") from e
                delay *= 2
        raise Transient("unreachable")

    def get(self, path: str, **kw) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, body: Any, **kw) -> Any:
        return self.call("POST", path, body, **kw)

    def open_stream(
        self,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        use_credential: bool = False,
        ctx: ssl.SSLContext | None = None,
    ):
        """Open a streaming GET (for downloads). Redirects are NOT followed automatically. The private
        control-plane trust applies only to the control-plane origin; every other origin uses the
        system roots (public trust), so a private --ca-file never breaks public model downloads."""
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", f"convoy-agent/{__version__}")
        for k, v in (extra_headers or {}).items():
            req.add_header(k, v)
        if use_credential and self.credential:
            req.add_header("Authorization", f"Bearer {self.credential}")
        opener = urllib.request.build_opener(
            _NoRedirect(), self._http_handler(), self._https_handler(context=ctx or self.context_for(url))
        )
        return opener.open(req, timeout=self.timeout)

    def context_for(self, url: str) -> ssl.SSLContext:
        from .urlpolicy import same_origin

        if self.ctx is not None and same_origin(url, self.base_url):
            return self.ctx
        return self.public_ctx


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None
