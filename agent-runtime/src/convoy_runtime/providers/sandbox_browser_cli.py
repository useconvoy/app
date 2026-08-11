"""Small stdlib CDP client executed by the promoted sandbox_browser tool."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        self.sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80))
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("CDP websocket upgrade failed")
        self.next_id = 1

    def _frame(self, payload: bytes) -> bytes:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65_536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        header.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bytes(header)

    def _read_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("CDP websocket closed")
            data += chunk
        return data

    def _read(self) -> dict[str, Any]:
        first, second = self._read_exact(2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        masked = bool(second & 0x80)
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if first & 0x0F == 0x8:
            raise RuntimeError("CDP websocket closed")
        return json.loads(payload)

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        call_id = self.next_id
        self.next_id += 1
        payload = json.dumps({"id": call_id, "method": method, "params": params or {}}).encode()
        self.sock.sendall(self._frame(payload))
        while True:
            message = self._read()
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"]))
            return message.get("result") or {}


def _page(cdp_url: str) -> CdpClient:
    with urllib.request.urlopen(cdp_url.rstrip("/") + "/json", timeout=5) as response:
        targets = json.loads(response.read())
    target = next(entry for entry in targets if entry.get("type") == "page")
    return CdpClient(str(target["webSocketDebuggerUrl"]))


def run(spec: dict[str, Any]) -> dict[str, Any]:
    client = _page(os.environ["CONVOY_CDP_URL"])
    action = str(spec.get("action", "snapshot"))
    if action == "navigate":
        result = client.call("Page.navigate", {"url": str(spec["url"])})
        time.sleep(float(spec.get("waitSeconds", 1)))
        return {"action": action, "url": spec["url"], "result": result}
    if action == "snapshot":
        result = client.call(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
            },
        )
        return {"action": action, "html": result["result"].get("value", "")}
    if action == "click":
        selector = json.dumps(str(spec["selector"]))
        expression = (
            "(() => { const e=document.querySelector(" + selector + "); "
            "if(!e) return false; e.click(); return true; })()"
        )
        result = client.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return {"action": action, "clicked": bool(result["result"].get("value"))}
    if action == "type":
        selector = json.dumps(str(spec["selector"]))
        text = json.dumps(str(spec.get("text", "")))
        expression = (
            "(() => { const e=document.querySelector(" + selector + "); "
            "if(!e) return false; e.focus(); e.value=" + text + "; "
            "e.dispatchEvent(new Event('input',{bubbles:true})); "
            "e.dispatchEvent(new Event('change',{bubbles:true})); return true; })()"
        )
        result = client.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return {"action": action, "typed": bool(result["result"].get("value"))}
    if action == "screenshot":
        result = client.call("Page.captureScreenshot", {"format": "png"})
        output = Path("outputs/browser-screenshot.png")
        output.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(result["data"])
        output.write_bytes(data)
        return {"action": action, "output": str(output), "sha256": hashlib.sha256(data).hexdigest()}
    raise ValueError(f"unsupported browser action {action!r}")


if __name__ == "__main__":
    print(json.dumps(run(json.loads(sys.argv[1])), sort_keys=True))
