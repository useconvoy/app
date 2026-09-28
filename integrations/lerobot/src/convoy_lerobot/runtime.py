"""Strict local checkpoint load; one newly inferred action per observation."""

from __future__ import annotations

import io
import os
from pathlib import Path

from convoy_contracts.execution import VISUAL_INSTRUCTION, VISUAL_PROFILE, visual_png_bytes

from .artifact import ARTIFACT_SHA256, RUNTIME, verify_assets, verify_versions


class SmolVLARuntime:
    runtime = RUNTIME
    artifact_sha256 = ARTIFACT_SHA256
    profile = VISUAL_PROFILE

    def __init__(self, root: Path):
        verify_versions()
        verify_assets(root)
        # All configurations, tokenizer assets, processors and weights are local.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import numpy as np
        import torch
        from lerobot.policies.factory import make_pre_post_processors
        from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

        self.np, self.torch = np, torch
        torch.set_num_threads(4)
        cfg = SmolVLAConfig.from_pretrained(str(root / "checkpoint"))
        cfg.device = "cpu"
        cfg.load_vlm_weights = False
        cfg.vlm_model_name = str(root / "base-assets")
        cfg.n_action_steps = 1
        cfg.compile_model = False
        self.policy = SmolVLAPolicy.from_pretrained(
            str(root / "checkpoint"), config=cfg, strict=True, local_files_only=True,
        ).float().eval()
        self.pre, self.post = make_pre_post_processors(
            cfg, str(root / "checkpoint"),
            preprocessor_overrides={
                "device_processor": {"device": "cpu"},
                "tokenizer_processor": {"tokenizer_name": str(root / "base-assets")},
            },
            postprocessor_overrides={"device_processor": {"device": "cpu"}},
        )
        self.owner = None
        self.reset_session(None)
        # Execute real weights before the worker can report ready. This synthetic
        # warmup is only a readiness check; task qualification uses rendered RGB.
        self._infer(np.zeros((480, 480, 3), dtype=np.uint8), [0.0] * 4, VISUAL_INSTRUCTION)
        self.reset_session(None)

    def reset_session(self, identity):
        self.policy.reset()
        self.torch.manual_seed(0)
        self.owner = identity

    def _infer(self, pixels, state, instruction):
        np, torch = self.np, self.torch
        inputs = self.pre({
            "observation.image": torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1))).float() / 255,
            "observation.state": torch.from_numpy(np.asarray(state, dtype=np.float32)),
            "task": instruction,
        })
        with torch.inference_mode():
            normalized = self.policy.select_action(inputs)
            action = self.post(normalized).detach().cpu().float().numpy().reshape(-1)
        if action.shape != (4,) or not np.isfinite(action).all():
            raise ValueError("policy returned invalid normalized action")
        return np.clip(action, -1, 1).astype(np.float32).tolist()

    def get_action(self, observation: dict) -> list[float]:
        if self.owner is None:
            raise ValueError("a mission session must own the policy before inference")
        from PIL import Image

        encoded = visual_png_bytes(observation["image_png_base64"])
        with Image.open(io.BytesIO(encoded)) as image:
            if image.format != "PNG" or image.mode != "RGB" or image.size != (480, 480):
                raise ValueError("image decode does not match the visual profile")
            pixels = self.np.asarray(image).copy()
        return self._infer(pixels, observation["state"], observation["instruction"])


def smolvla() -> SmolVLARuntime:
    root = os.environ.get("CONVOY_SMOLVLA_ASSETS")
    if not root:
        raise ValueError("CONVOY_SMOLVLA_ASSETS must name the verified local checkpoint asset directory")
    return SmolVLARuntime(Path(root).resolve())
