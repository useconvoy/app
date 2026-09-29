# JetPack 6.1/6.2 CUDA 12.6 userspace, with the Jetson-specific (sm_87) wheels.
ARG BASE_IMAGE=nvcr.io/nvidia/l4t-jetpack@sha256:34ccf0f3b63c6da9eee45f2e79de9bf7fdf3beda9abfd72bbf285ae9d40bb673
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-pip libosmesa6 libegl1 libgl1 libopenblas0 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/experiment
ENV PATH="/opt/experiment/bin:${PATH}"
RUN python -m pip install --no-cache-dir \
    'https://pypi.jetson-ai-lab.io/jp6/cu126/+f/62a/1beee9f2f1470/torch-2.8.0-cp310-cp310-linux_aarch64.whl#sha256=62a1beee9f2f147076a974d2942c90060c12771c94740830327cae705b2595fc' \
    'https://pypi.jetson-ai-lab.io/jp6/cu126/+f/907/c4c1933789645/torchvision-0.23.0-cp310-cp310-linux_aarch64.whl#sha256=907c4c1933789645ebb20dd9181d40f8647978e6bd30086ae7b01febb937d2d1'
# LeRobot 0.4.4 supports Python 3.10 and the saved SmolVLA processors. This is a
# separately identified experiment, not a version change to the qualified app.
RUN python -m pip install --no-cache-dir \
    'lerobot[smolvla]==0.4.4' 'metaworld==3.0.0' 'mujoco==3.3.0' \
    'gymnasium==1.2.1' 'numpy==2.2.6' 'transformers==4.57.1' \
    'safetensors==0.8.0' 'huggingface-hub==0.35.3' 'pillow==12.3.0' \
    'pydantic>=2,<3' 'imageio==2.37.0' 'imageio-ffmpeg==0.6.0' \
    && python -m pip check \
    && python -m pip freeze > /opt/experiment-packages.txt
ENV MUJOCO_GL=egl HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
ENV PYTHONPATH=/repo/integrations/lerobot/experiments:/repo/integrations/lerobot/src:/repo/control-plane/contracts
WORKDIR /repo
ENTRYPOINT ["python", "-m", "jetson_timing.run"]
