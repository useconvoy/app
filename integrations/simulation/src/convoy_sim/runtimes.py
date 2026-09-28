"""Worker factories selected by the operator, never by an inference request.

This reference deliberately uses the upstream scripted expert. The worker's
Runtime protocol also accepts customer factories with verified model weights.
"""

import hashlib
import inspect
from importlib.metadata import version

import numpy as np
from convoy_contracts.execution import PROFILE
from metaworld.policies.sawyer_pick_place_v3_policy import SawyerPickPlaceV3Policy

# Bind the upstream policy implementation and this adapter's transform. The
# environment dependencies are pinned separately in the release manifest.
SCRIPTED_DIGEST = hashlib.sha256(
    (inspect.getsource(SawyerPickPlaceV3Policy) + "\nclip[-1,1];float32;metaworld=3.1.1;adapter=v1").encode()
).hexdigest()


class ScriptedRuntime:
    runtime = "metaworld-scripted-v1"
    artifact_sha256 = SCRIPTED_DIGEST

    def __init__(self):
        if version("metaworld") != "3.1.1":
            raise ValueError("this worker requires the pinned MetaWorld 3.1.1 runtime")
        self.policy = SawyerPickPlaceV3Policy()

    def get_action(self, observation: list[float]) -> list[float]:
        return np.clip(self.policy.get_action(np.asarray(observation)), -1, 1).astype(np.float32).tolist()


def scripted() -> ScriptedRuntime:
    return ScriptedRuntime()


def reference_manifest() -> dict:
    return {
        "schema_version": 1,
        "profile": PROFILE,
        "policy": {"runtime": ScriptedRuntime.runtime, "artifact_sha256": SCRIPTED_DIGEST},
        "environment": {"name": "pick-place-v3", "metaworld": "3.1.1", "mujoco": "3.3.0"},
        "execution": {"max_steps": 500, "decision_timeout_ms": 1000, "mission_timeout_s": 120},
    }
