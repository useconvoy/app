"""Separate CUDA/CPU experiment identity; the qualified CPU runtime is untouched."""

import json
import os
import time
from pathlib import Path


def normalization_stats(root, pipeline, registry):
    """Same verified saved statistics, using the processor's public override API.

    LeRobot's default state loader round-trips tensors through Torch's NumPy C
    bridge. Python lists preserve these float32 values and avoid that ABI path.
    """
    from safetensors.torch import load_file
    entries = json.loads((root / f"{pipeline}.json").read_text())["steps"]
    entry, = [s for s in entries if s["registry_name"] == registry]
    tensors = load_file(root / entry["state_file"])
    stats = {}
    for flat_name, tensor in tensors.items():
        name, statistic = flat_name.rsplit(".", 1)
        stats.setdefault(name, {})[statistic] = tensor.tolist()
    return stats, tensors


class VisualPolicy:
    def __init__(self, assets: str, device: str, chunk_size: int, precision="float32",
                 camera_size=480, vision_size=512, denoise_steps=10):
        from convoy_lerobot.artifact import verify_assets

        root = Path(assets)
        verify_assets(root)
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        import numpy as np
        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import make_pre_post_processors
        from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; no silent CPU fallback")
        if device == "cuda":
            # The Jetson wheel has a NumPy-1 C bridge; dependencies use NumPy 2.
            # Use the supported Python buffer protocol, not that incompatible C
            # bridge. Probe the actual adapter and CUDA kernels before weights.
            probe = torch.frombuffer(memoryview(np.ones((4, 4), dtype=np.float32)),
                                     dtype=torch.float32).reshape(4, 4).to(device)
            _ = probe.sum().item()
            result = (probe @ probe).cpu().tolist()
            if result != [[4.0] * 4] * 4:
                raise RuntimeError("CUDA/buffer preflight produced incorrect values")
            del probe, result
        self.np, self.torch, self.device, self.chunk_size = np, torch, device, chunk_size
        self.precision = precision
        self.camera_size = camera_size
        self.verify_post_batch = True
        if precision != "float32" and device != "cuda":
            raise ValueError("mixed precision requires the explicit CUDA experiment")
        cfg = PreTrainedConfig.from_pretrained(str(root / "checkpoint"))
        if not isinstance(cfg, SmolVLAConfig):
            raise ValueError("checkpoint must identify a SmolVLA configuration")
        # Load/convert on CPU first: from_pretrained otherwise allocates a full
        # float32 GPU model before a requested reduced-precision conversion.
        cfg.device, cfg.load_vlm_weights = "cpu", False
        cfg.vlm_model_name = str(root / "base-assets")
        cfg.compile_model = False
        cfg.n_action_steps = chunk_size
        cfg.resize_imgs_with_padding = (vision_size, vision_size)
        cfg.num_steps = denoise_steps
        if chunk_size > cfg.chunk_size:
            raise ValueError("requested chunk exceeds checkpoint horizon")
        self.policy = SmolVLAPolicy.from_pretrained(
            str(root / "checkpoint"), config=cfg, strict=True, local_files_only=True,
        ).to(dtype=getattr(torch, precision)).to(device=device).eval()
        cfg.device = device
        pre_stats, pre_tensors = normalization_stats(root / "checkpoint", "policy_preprocessor", "normalizer_processor")
        post_stats, post_tensors = normalization_stats(root / "checkpoint", "policy_postprocessor", "unnormalizer_processor")
        self.pre, self.post = make_pre_post_processors(
            cfg, str(root / "checkpoint"),
            preprocessor_overrides={
                "device_processor": {"device": device},
                "tokenizer_processor": {"tokenizer_name": str(root / "base-assets")},
                "normalizer_processor": {"stats": pre_stats},
            },
            postprocessor_overrides={"device_processor": {"device": "cpu"},
                                     "unnormalizer_processor": {"stats": post_stats}},
        )
        for pipeline, expected in ((self.pre, pre_tensors), (self.post, post_tensors)):
            actual = {key: value for step in pipeline.steps for key, value in step.state_dict().items()}
            if actual.keys() != expected.keys() or any(not torch.equal(actual[k].cpu(), v) for k, v in expected.items()):
                raise ValueError("normalization statistics changed during buffer-adapter setup")
        self.reset()
        self.infer(np.zeros((camera_size, camera_size, 3), dtype=np.uint8), [0.0] * 4)
        self.reset()
        import gc
        gc.collect()
        if device == "cuda":
            # Return unused warmup allocations before the camera reserves its
            # framebuffer on this shared-memory device. Never flush per tick.
            torch.cuda.empty_cache()

    def reset(self):
        self.policy.reset()
        self.torch.manual_seed(0)
        if self.device == "cuda":
            self.torch.cuda.manual_seed_all(0)

    def infer(self, pixels, state):
        np, torch = self.np, self.torch
        if pixels.dtype != np.uint8 or pixels.shape != (self.camera_size, self.camera_size, 3):
            raise ValueError("camera frame does not match the configured RGB uint8 shape")
        # Equivalent CHW uint8 -> float32 / 255 transform, without depending on
        # Torch's NumPy C ABI. frombuffer retains the backing object's lifetime.
        image = torch.frombuffer(memoryview(np.ascontiguousarray(pixels.transpose(2, 0, 1))),
                                 dtype=torch.uint8).reshape(3, self.camera_size, self.camera_size).float() / 255
        inputs = self.pre({
            "observation.image": image,
            "observation.state": torch.tensor(state, dtype=torch.float32),
            "task": "Pick and place a puck to a goal",
        })
        from contextlib import nullcontext
        amp = (torch.autocast("cuda", dtype=getattr(torch, self.precision))
               if self.precision != "float32" else nullcontext())
        with torch.inference_mode(), amp:
            normalized = self.policy.predict_action_chunk(inputs)
            batched = self.post(normalized[0, :self.chunk_size]).detach().cpu().float().reshape(self.chunk_size, 4)
            if self.verify_post_batch:
                rows = torch.stack([self.post(normalized[:, i]).detach().cpu().float().reshape(4)
                                    for i in range(self.chunk_size)])
                if not torch.equal(batched, rows):
                    raise ValueError("batched action processing differs from checkpoint row processing")
                self.verify_post_batch = False
            actions = batched.tolist()
        values = np.asarray(actions)
        if values.shape != (self.chunk_size, 4) or not np.isfinite(values).all():
            raise ValueError("invalid policy chunk")
        return np.clip(values, -1, 1).astype(np.float32).tolist()


def worker(connection, requests, assets, device, chunk_size, delay_s, drop_every, precision,
           camera_size, vision_size, denoise_steps):
    """One outstanding request; no shared simulator state or unbounded queue."""
    try:
        policy = VisualPolicy(assets, device, chunk_size, precision, camera_size, vision_size, denoise_steps)
        gpu = None
        if device == "cuda":
            gpu = {"name": policy.torch.cuda.get_device_name(),
                   "capability": list(policy.torch.cuda.get_device_capability()),
                   "warmup_peak_allocated_bytes": policy.torch.cuda.max_memory_allocated(),
                   "allocated_bytes": policy.torch.cuda.memory_allocated(),
                   "reserved_bytes": policy.torch.cuda.memory_reserved(),
                   "free_and_total_bytes": list(policy.torch.cuda.mem_get_info())}
        connection.send({"ready": True, "device": device, "cuda_build": policy.torch.version.cuda,
                         "gpu": gpu, "weight_dtype": precision, "compute_precision": precision,
                         "array_adapter": "python-buffer-chw-uint8-float32-divide255; python-list-state-and-actions",
                         "torch_threads": 2})
        while True:
            message = requests.recv()
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
                "inference_started_at": started, "inference_finished_at": started + inference_s,
                "render_started_at": message["render_started_at"],
                "render_finished_at": message["render_finished_at"], "render_s": message["render_s"],
                "sent_at": time.monotonic(),
                "dropped": bool(drop_every and (message["sequence"] + 1) % drop_every == 0),
            })
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        import traceback
        traceback.print_exc()
        diagnostics = {}
        for name in ("memory.current", "memory.max", "memory.events"):
            path = Path("/sys/fs/cgroup") / name
            if path.exists():
                diagnostics[name] = path.read_text().strip()
        try:
            import torch
            diagnostics["cuda"] = {"allocated": torch.cuda.memory_allocated(),
                                   "reserved": torch.cuda.memory_reserved(),
                                   "free_total": list(torch.cuda.mem_get_info())}
        except Exception:
            pass
        print(json.dumps({"failure_resource_state": diagnostics}), flush=True)
        connection.send({"error": f"{type(error).__name__}: {error}"})
    finally:
        connection.close()
        requests.close()
