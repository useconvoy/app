"""Run the enrolled device, preserving its identity and journal across restarts."""

import json
from pathlib import Path

from convoy_sim.managed import main

installation = json.loads(Path("/robot/installation.json").read_text())
raise SystemExit(main([
    "--data-dir", "/robot/device", "--robot-id", installation["robot_id"],
    "--worker-url", "https://inference:8443",
]))
