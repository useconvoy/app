# The operator resolves and records the official JetPack 6.2 image digest.
ARG BASE_IMAGE=nvcr.io/nvidia/pytorch@sha256:05bb8855c5ab94a556653fe4f7206e922c8ec93fdb0c88a02c2e013e20cff1d2
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 libegl1 libgl1 \
    && rm -rf /var/lib/apt/lists/*
# Use an isolated stable CUDA 12.6 stack. The vendor image supplies graphics
# libraries, but its Torch build cannot bridge NumPy 2 and is not inherited.
RUN python3 -m venv /opt/experiment
ENV PATH="/opt/experiment/bin:${PATH}"
RUN python -m pip install --no-cache-dir \
      'https://download-r2.pytorch.org/whl/cu126/torch-2.10.0%2Bcu126-cp312-cp312-manylinux_2_28_aarch64.whl#sha256=81264238b3d8840276dd30c31f393e325b8f5da6390d18ac2a80dacecfd693ea' \
      'https://download-r2.pytorch.org/whl/cu126/torchvision-0.25.0%2Bcu126-cp312-cp312-manylinux_2_28_aarch64.whl#sha256=968f7dea6a16c8127d23416a55845dffcfe1b08dee7f50cb8cdeb950683a6752'
RUN python -m pip install --no-cache-dir \
       'lerobot[smolvla]==0.6.1' 'metaworld==3.0.0' 'mujoco==3.3.0' \
       'gymnasium==1.2.1' 'numpy==2.2.6' 'transformers==5.5.4' \
       'safetensors==0.8.0' 'huggingface-hub==1.33.0' 'pillow==12.3.0' \
       'pydantic>=2,<3' 'imageio==2.37.0' 'imageio-ffmpeg==0.6.0' \
    && python -m pip check \
    && python -m pip freeze > /opt/experiment-packages.txt
ENV MUJOCO_GL=egl HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
ENV PYTHONPATH=/repo/integrations/lerobot/experiments:/repo/integrations/lerobot/src:/repo/control-plane/contracts
WORKDIR /repo
ENTRYPOINT ["python", "-m", "jetson_timing.run"]
