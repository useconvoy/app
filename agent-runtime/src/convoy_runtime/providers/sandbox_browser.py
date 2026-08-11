"""Chromium session + allowlist proxy used by both sandbox providers.

This module is stdlib-only because the ECS provider ships it into the
credential-free sandbox task alongside its runner. Browser profile state is
kept under the sandbox workspace when persistence is enabled, so the normal
workspace snapshot chain is also the browser-session checkpoint chain.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, cast


class BrowserUnavailableError(RuntimeError):
    pass


class _EgressPolicy:
    def __init__(self, domains: list[str]) -> None:
        self.domains = {domain.strip().lower().rstrip(".") for domain in domains if domain.strip()}

    def allows(self, host: str) -> bool:
        normalized = host.strip().lower().rstrip(".")
        return normalized in self.domains or any(
            normalized.endswith("." + domain) for domain in self.domains
        )


class _ProxyHandler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self) -> None:
        policy = cast("_ProxyServer", self.server).policy
        first = self.rfile.readline(65_536)
        if not first:
            return
        try:
            method, target, _version = first.decode("latin1").strip().split(" ", 2)
            headers: list[bytes] = []
            while True:
                line = self.rfile.readline(65_536)
                headers.append(line)
                if line in (b"\r\n", b"\n", b""):
                    break
            if method.upper() == "CONNECT":
                host, _, raw_port = target.rpartition(":")
                if not host:
                    host, raw_port = raw_port, "443"
                port = int(raw_port or "443")
            else:
                if "://" not in target:
                    raise ValueError("proxy request must use absolute-form URL")
                authority = target.split("://", 1)[1].split("/", 1)[0]
                host, separator, raw_port = authority.rpartition(":")
                if not separator:
                    host, raw_port = authority, "80"
                port = int(raw_port)
        except (UnicodeDecodeError, ValueError):
            self.wfile.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return

        if not policy.allows(host):
            self.wfile.write(
                b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n"
                b"blocked by convoy browser egress policy\n"
            )
            return
        try:
            upstream = socket.create_connection((host, port), timeout=self.timeout)
        except OSError:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            return

        with upstream:
            if method.upper() == "CONNECT":
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.wfile.flush()
            else:
                upstream.sendall(first + b"".join(headers))
            sockets = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], self.timeout)
                if not readable:
                    return
                for source in readable:
                    try:
                        chunk = source.recv(65_536)
                    except OSError:
                        return
                    if not chunk:
                        return
                    destination = upstream if source is self.connection else self.connection
                    destination.sendall(chunk)


class _ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, policy: _EgressPolicy) -> None:
        self.policy = policy
        super().__init__(("127.0.0.1", 0), _ProxyHandler)


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _chromium_binary(explicit: str = "") -> str:
    candidates = [
        explicit,
        os.environ.get("CONVOY_CHROMIUM_BINARY", ""),
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise BrowserUnavailableError(
        "this environment enables a browser but no Chromium executable was found; "
        "set CONVOY_CHROMIUM_BINARY or use the Convoy sandbox image"
    )


def normalize_policy(policy: Any) -> dict[str, Any]:
    if policy is None:
        return {"allowedDomains": [], "persistProfile": True}
    if hasattr(policy, "model_dump"):
        raw = policy.model_dump(mode="json", by_alias=True)
    else:
        raw = dict(policy)
    return {
        "allowedDomains": list(raw.get("allowedDomains") or raw.get("allowed_domains") or []),
        "persistProfile": bool(raw.get("persistProfile", raw.get("persist_profile", True))),
    }


class BrowserRuntime:
    """One Chromium process and one per-session egress proxy."""

    def __init__(self, workspace: Path, policy: Any, *, chromium_binary: str = "") -> None:
        self.workspace = workspace
        self.policy = normalize_policy(policy)
        self.chromium_binary = chromium_binary
        self._proxy: _ProxyServer | None = None
        self._proxy_thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._cdp_port = 0
        self._ephemeral_profile: Path | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.policy["allowedDomains"])

    @property
    def profile_dir(self) -> Path:
        if self.policy["persistProfile"]:
            return self.workspace / ".convoy/browser/profile"
        if self._ephemeral_profile is None:
            self._ephemeral_profile = Path(tempfile.mkdtemp(prefix="convoy-browser-profile-"))
        return self._ephemeral_profile

    @property
    def job_env(self) -> dict[str, str]:
        if not self.enabled or self._proxy is None or not self._cdp_port:
            return {}
        proxy_url = f"http://127.0.0.1:{int(self._proxy.server_address[1])}"
        return {
            "CONVOY_CDP_URL": f"http://127.0.0.1:{self._cdp_port}",
            "CONVOY_BROWSER_PROFILE_DIR": str(self.profile_dir),
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "NO_PROXY": "127.0.0.1,localhost",
        }

    def start(self) -> None:
        if not self.enabled or self._process is not None:
            return
        control = self.workspace / ".convoy/browser"
        control.mkdir(parents=True, exist_ok=True)
        (control / "policy.json").write_text(json.dumps(self.policy, sort_keys=True))
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        if self._proxy is None:
            self._proxy = _ProxyServer(_EgressPolicy(self.policy["allowedDomains"]))
            self._proxy_thread = threading.Thread(target=self._proxy.serve_forever, daemon=True)
            self._proxy_thread.start()
        self._cdp_port = _available_port()
        proxy_url = f"http://127.0.0.1:{int(self._proxy.server_address[1])}"
        command = [
            _chromium_binary(self.chromium_binary),
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self._cdp_port}",
            "--user-data-dir=" + str(self.profile_dir),
            "--proxy-server=" + proxy_url,
            "--proxy-bypass-list=<-loopback>",
            "about:blank",
        ]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self._cdp_port}/json/version", timeout=0.5
                ) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        self.close()
        raise BrowserUnavailableError("Chromium did not expose its debugging endpoint")

    def stop_browser(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._process = None
        self._cdp_port = 0

    def close(self) -> None:
        self.stop_browser()
        if self._proxy is not None:
            self._proxy.shutdown()
            self._proxy.server_close()
            self._proxy = None
        self._proxy_thread = None
        if self._ephemeral_profile is not None:
            shutil.rmtree(self._ephemeral_profile, ignore_errors=True)
            self._ephemeral_profile = None
