# The operator resolves and records the official JetPack 6.2 image digest.
ARG BASE_IMAGE=nvcr.io/nvidia/pytorch:25.06-py3-igpu
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 libegl1 libgl1 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/experiment
ENV PATH="/opt/experiment/bin:${PATH}"
# Preserve NVIDIA's CUDA-enabled builds; ordinary PyPI torch must not replace them.
RUN python -c 'import importlib.metadata as m; print("\n".join(f"{n}=={m.version(n)}" for n in ("torch","torchvision")))' > /tmp/ngc-constraints.txt \
    && python -m pip install --no-cache-dir --pre -c /tmp/ngc-constraints.txt \
       'lerobot[smolvla]==0.6.1' 'metaworld==3.0.0' 'mujoco==3.3.0' \
       'gymnasium==1.2.1' 'numpy==2.2.6' 'transformers==5.5.4' \
       'safetensors==0.8.0' 'huggingface-hub==1.33.0' 'pillow==12.3.0' \
       'pydantic>=2,<3' 'imageio==2.37.0' 'imageio-ffmpeg==0.6.0' \
    && python -m pip freeze > /opt/experiment-packages.txt
ENV MUJOCO_GL=egl HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
ENV PYTHONPATH=/repo/integrations/lerobot/experiments:/repo/integrations/lerobot/src:/repo/control-plane/contracts
WORKDIR /repo
ENTRYPOINT ["python", "-m", "jetson_timing.run"]
