"""Simulated robot client: drives real gateway traffic (production mode) so probation has actual
requests to count. Runs as a daemon thread inside the simulated agent. All traffic is labelled by the
gateway's own spans; nothing here fabricates measurements."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

PROMPTS = [
    "What is the capital of France? Answer with one word.",
    "The operator says: stop immediately. Reply with the single word STOP or CONTINUE.",
    "Return JSON with a field 'action' set to 'dock'.",
    "What color is the sky on a clear day? One word.",
]


class RobotSim(threading.Thread):
    def __init__(self, gateway_port: int, interval_s: float = 2.0):
        super().__init__(daemon=True, name="robot-sim")
        self.port = gateway_port
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.stats = {"sent": 0, "ok": 0, "unavailable": 0, "errors": 0}

    def run(self) -> None:
        i = 0
        while not self.stop_event.is_set():
            body = {"messages": [{"role": "user", "content": PROMPTS[i % len(PROMPTS)]}], "max_tokens": 16}
            i += 1
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/v1/chat/completions",
                data=json.dumps(body).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            self.stats["sent"] += 1
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    r.read()
                    self.stats["ok"] += 1
            except urllib.error.HTTPError as e:
                if e.code == 503:
                    self.stats["unavailable"] += 1  # expected during cutover
                else:
                    self.stats["errors"] += 1
            except Exception:
                self.stats["errors"] += 1
            self.stop_event.wait(self.interval_s)

    def stop(self) -> None:
        self.stop_event.set()
