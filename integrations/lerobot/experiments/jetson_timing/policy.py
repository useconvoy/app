"""Separate CUDA/CPU experiment identity; the qualified CPU runtime is untouched."""

import os
import time
from pathlib import Path


class VisualPolicy:
    def __init__(self, assets: str, device: str, chunk_size: int):
        from convoy_lerobot.artifact import verify_assets

        root = Path(assets)
        verify_assets(root)
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        import numpy as np
        import torch
        from lerobot.policies.factory import make_pre_post_processors
        from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; no silent CPU fallback")
        if device == "cuda":
            # Device discovery does not prove that this wheel contains usable
            # Orin kernels. Check both the NumPy bridge and CUDA before weights.
            probe = torch.from_numpy(np.ones((4, 4), dtype=np.float32)).to(device)
            _ = probe.sum().item()
            result = (probe @ probe).cpu().numpy()
            if not np.all(result == 4):
                raise RuntimeError("CUDA/NumPy preflight produced incorrect values")
            del probe, result
        self.np, self.torch, self.device, self.chunk_size = np, torch, device, chunk_size
        cfg = SmolVLAConfig.from_pretrained(str(root / "checkpoint"))
        cfg.device, cfg.load_vlm_weights = device, False
        cfg.vlm_model_name = str(root / "base-assets")
        cfg.compile_model = False
        cfg.n_action_steps = chunk_size
        if chunk_size > cfg.chunk_size:
            raise ValueError("requested chunk exceeds checkpoint horizon")
        self.policy = SmolVLAPolicy.from_pretrained(
            str(root / "checkpoint"), config=cfg, strict=True, local_files_only=True,
        ).to(device=device, dtype=torch.float32).eval()
        self.pre, self.post = make_pre_post_processors(
            cfg, str(root / "checkpoint"),
            preprocessor_overrides={
                "device_processor": {"device": device},
                "tokenizer_processor": {"tokenizer_name": str(root / "base-assets")},
            },
            postprocessor_overrides={"device_processor": {"device": "cpu"}},
        )
        self.reset()
        self.infer(np.zeros((480, 480, 3), dtype=np.uint8), [0.0] * 4)
        self.reset()

    def reset(self):
        self.policy.reset()
        self.torch.manual_seed(0)
        if self.device == "cuda":
            self.torch.cuda.manual_seed_all(0)

    def infer(self, pixels, state):
        np, torch = self.np, self.torch
        inputs = self.pre({
            "observation.image": torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1))).float() / 255,
            "observation.state": torch.from_numpy(np.asarray(state, dtype=np.float32)),
            "task": "Pick and place a puck to a goal",
        })
        with torch.inference_mode():
            normalized = self.policy.predict_action_chunk(inputs)
            actions = [self.post(normalized[:, i]).detach().cpu().float().numpy().reshape(-1)
                       for i in range(self.chunk_size)]
        values = np.asarray(actions)
        if values.shape != (self.chunk_size, 4) or not np.isfinite(values).all():
            raise ValueError("invalid policy chunk")
        return np.clip(values, -1, 1).astype(np.float32).tolist()


def worker(connection, assets, device, chunk_size, delay_s, drop_every):
    """One outstanding request; no shared simulator state or unbounded queue."""
    try:
        policy = VisualPolicy(assets, device, chunk_size)
        gpu = None
        if device == "cuda":
            gpu = {"name": policy.torch.cuda.get_device_name(),
                   "capability": list(policy.torch.cuda.get_device_capability()),
                   "warmup_peak_allocated_bytes": policy.torch.cuda.max_memory_allocated()}
        connection.send({"ready": True, "device": device, "cuda_build": policy.torch.version.cuda,
                         "gpu": gpu, "dtype": "float32", "torch_threads": 2})
        while True:
            message = connection.recv()
            if message is None:
                break
            if message.get("reset"):
                policy.reset()
                connection.send({"reset": True})
                continue
            started = time.monotonic()
            actions = policy.infer(message["pixels"], message["state"])
            inference_s = time.monotonic() - started
            if delay_s:
                time.sleep(delay_s)
            connection.send({
                "sequence": message["sequence"], "observed_at": message["observed_at"],
                "actions": actions, "inference_s": inference_s,
                "dropped": bool(drop_every and (message["sequence"] + 1) % drop_every == 0),
            })
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        connection.send({"error": f"{type(error).__name__}: {error}"})
    finally:
        connection.close()
