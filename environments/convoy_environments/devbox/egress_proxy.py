"""Domain-allowlist egress proxy (HTTP CONNECT + plain HTTP forward).

Chromium in the devbox is launched with --proxy-server=http://127.0.0.1:<port>
so every request — including DNS-over-HTTPS fallbacks and subresources —
passes this check. Blocked requests get 403 and a structured line on stdout
(the devbox log stream Aneesh's runtime already captures); wiring blocks into
the event log as first-class events is a follow-up that needs a
policy_violation event type in convoy_core.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Iterable, Optional, Tuple


class EgressPolicy:
    """Allowlist with exact and subdomain matching: "dmv.ca.gov" allows
    dmv.ca.gov and www.dmv.ca.gov, never evil-dmv.ca.gov.example.com."""

    def __init__(self, allowed_domains: Iterable[str]) -> None:
        self._domains = {d.strip().lower().rstrip(".") for d in allowed_domains if d.strip()}

    def allows(self, host: str) -> bool:
        host = host.strip().lower().rstrip(".").split(":")[0]
        if not host:
            return False
        if host in self._domains:
            return True
        return any(host.endswith("." + d) for d in self._domains)


def _log_block(host: str) -> None:
    line = {"component": "egress_proxy", "action": "blocked", "host": host,
            "at": datetime.now(timezone.utc).isoformat()}
    print(json.dumps(line), file=sys.stdout, flush=True)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _parse_target(request_line: str) -> Tuple[str, str, int]:
    """→ (method, host, port). Raises ValueError on garbage."""
    parts = request_line.strip().split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method, target = parts[0].upper(), parts[1]
    if method == "CONNECT":
        host, _, port = target.partition(":")
        return method, host, int(port or 443)
    # absolute-form proxy request: GET http://host[:port]/path
    if "://" in target:
        rest = target.split("://", 1)[1]
        hostport = rest.split("/", 1)[0]
        host, _, port = hostport.partition(":")
        return method, host, int(port or 80)
    raise ValueError("plain-path request without Host routing unsupported")


async def _handle(policy: EgressPolicy, reader: asyncio.StreamReader,
                  writer: asyncio.StreamWriter) -> None:
    try:
        first = (await reader.readline()).decode("latin1")
        method, host, port = _parse_target(first)
    except Exception:
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    # drain request headers (CONNECT) — we tunnel after the blank line
    header_lines = [first]
    while True:
        line = await reader.readline()
        header_lines.append(line.decode("latin1"))
        if line in (b"\r\n", b"\n", b""):
            break

    if not policy.allows(host):
        _log_block(host)
        writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\nblocked by convoy egress policy\r\n")
        await writer.drain()
        writer.close()
        return

    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
    except OSError:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    if method == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
    else:
        upstream_writer.write("".join(header_lines).encode("latin1"))
        await upstream_writer.drain()

    await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))


async def run_proxy(allowed_domains: Iterable[str], host: str = "127.0.0.1",
                    port: int = 3128) -> asyncio.AbstractServer:
    policy = EgressPolicy(allowed_domains)

    async def handler(r, w):
        await _handle(policy, r, w)

    return await asyncio.start_server(handler, host, port)


def main() -> None:  # pragma: no cover — process entrypoint
    import os

    domains = os.environ.get("CONVOY_ALLOWED_DOMAINS", "").split(",")
    port = int(os.environ.get("CONVOY_PROXY_PORT", "3128"))

    async def serve():
        server = await run_proxy(domains, port=port)
        async with server:
            await server.serve_forever()

    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()
