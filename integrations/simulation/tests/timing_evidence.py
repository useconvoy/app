"""Persist only measurement summaries, never test credentials or enrollment state."""
import json
import platform

import mujoco


def record(directory, name, summary):
    evidence = {"case": name, "host": {"machine": platform.machine(), "platform": platform.platform(),
                                       "python": platform.python_version(), "mujoco": mujoco.__version__},
                "summary": summary}
    (directory / f"timing-{name}.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
