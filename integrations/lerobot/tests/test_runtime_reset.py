"""The integration's reset hook clears queues and reseeds each mission; no weights in CI."""

from convoy_contracts.execution import canonical_digest, validate_manifest

from convoy_lerobot.artifact import ARTIFACT, ARTIFACT_SHA256, reference_manifest
from convoy_lerobot.runtime import SmolVLARuntime


def test_runtime_reset_clears_policy_state_and_reseeds_each_mission():
    calls = []

    class Policy:
        def reset(self):
            calls.append("reset")

    class Torch:
        def manual_seed(self, seed):
            calls.append(("seed", seed))

    runtime = SmolVLARuntime.__new__(SmolVLARuntime)
    runtime.policy, runtime.torch = Policy(), Torch()
    runtime.reset_session({"mission_id": "one"})
    runtime.reset_session({"mission_id": "two"})
    assert calls == ["reset", ("seed", 0), "reset", ("seed", 0)]
    assert runtime.owner == {"mission_id": "two"}
    assert ARTIFACT["execution"]["actions_per_replan"] == 1
    assert ARTIFACT_SHA256 == canonical_digest(ARTIFACT)
    assert validate_manifest(reference_manifest())["policy"]["artifact_sha256"] == ARTIFACT_SHA256
