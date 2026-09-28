"""Untrusted HTTP error text and malformed responses do not become episode data."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from convoy_agent.coordinator.transport import JsonHTTP, RemoteError, TransportError


def test_http_failure_keeps_status_but_never_remote_payload_and_bounds_json():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            status, content = {
                "/denied": (401, b'{"detail":"private-token-and-model-text"}'),
                "/malformed": (200, b"private-token-and-model-text"),
                "/oversized": (200, b"x" * 131073),
                "/truncated": (200, b'{"value":1}'),
            }[self.path]
            self.send_response(status)
            self.send_header("Content-Length", str(len(content) + (100 if self.path == "/truncated" else 0)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = JsonHTTP(f"http://127.0.0.1:{server.server_port}", "local-test-token")
    try:
        with pytest.raises(RemoteError) as denied:
            client.get("/denied")
        assert denied.value.status == 401 and str(denied.value) == "remote HTTP 401"
        for path in ("/malformed", "/oversized", "/truncated"):
            with pytest.raises(TransportError) as invalid:
                client.get(path)
            assert invalid.value.category == str(invalid.value) == "protocol"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
    with pytest.raises(TransportError) as disconnected:
        client.get("/denied")
    assert disconnected.value.category == "transport"
