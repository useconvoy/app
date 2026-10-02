"""Explicit controlled reference policy, not a learned robotics model."""
import json
import os
from pathlib import Path

from convoy_contracts.execution import _keys, _number, canonical_digest
from convoy_contracts.registered import REGISTERED_PROFILE


class JointTargetRuntime:
    profile = REGISTERED_PROFILE
    runtime = "convoy-joint-target-reference-v1"

    def __init__(self, target):
        _keys(target, {"target_joint_positions"}, "reference policy artifact")
        values = target["target_joint_positions"]
        if not isinstance(values, list) or not 1 <= len(values) <= 128:
            raise ValueError("reference policy requires 1–128 target positions")
        for value in values:
            _number(value, "target position", -1e6, 1e6)
        self.target = values.copy()
        self.artifact_sha256 = canonical_digest(target)

    def reset_session(self, identity):
        pass

    def get_action(self, observation):
        return self.target.copy()


def from_file():
    path = Path(os.environ["CONVOY_JOINT_REFERENCE_FILE"])
    if path.stat().st_size > 16384:
        raise ValueError("reference policy artifact is too large")
    return JointTargetRuntime(json.loads(path.read_text()))
