"""The four demo deployment configurations, motor-policy hooks, and eval slices.

"Edge Qwen" (``edge_qwen_edge_skills``) calls the real model: every skill decision
is a request to Qwen2.5-1.5B-Instruct on a connected Jetson through Convoy's
device chat API (``device_planner``), with no latency model and no stand-in.
The other three configurations use the rule-based stand-in with modeled latency.

Latency numbers and their sources (modeled configurations):

* Edge planner (the skill router of "Edge Qwen + GPT Astra"): Qwen2.5-1.5B-Instruct
  Q4_K_M with llama.cpp CUDA on a Jetson Orin Nano Super 8 GB, Convoy's own
  measurement (control-plane/docs/VERIFICATION.md): completion p50 122 ms / p95
  636 ms in a 1,666-request soak, p50 181 / p95 900 ms in the deploy smoke test.
  Modeled as p50 150 ms, p95 700 ms.
* Cloud planner: GPT-6 Astra at low reasoning effort. Artificial Analysis (OpenAI
  API, read 2026-10-01) reports a median time to first answer token of 2.96 s and
  43.2 output tokens/s; a ~40-token skill call adds ~0.9 s, so p50 3.9 s. It
  publishes medians only: p95 6.0 s is an assumption (about 1.5x the median).
  Plus a 30/90 ms (p50/p95) site-to-cloud round trip. Third-party API numbers,
  not measurements from a robot site. A hosted API serves both arms' calls at
  once; the edge model runs one call at a time on the Jetson.
* SmolVLA: the only local checkpoint is lerobot/smolvla_metaworld (single Sawyer
  arm, 4-D Cartesian action, one 480x480 corner camera). Nothing here trains it
  for a two-arm, 14-D joint robot, so it is reported unavailable rather than given
  invented competence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .planning import LatencyModel, NetworkModel, PlannerProfile

EDGE_QWEN = PlannerProfile(
    name="edge-qwen2.5-1.5b",
    model="Qwen2.5-1.5B-Instruct Q4_K_M, llama.cpp CUDA, Jetson Orin Nano Super 8 GB",
    placement="edge",
    latency=LatencyModel(0.150, 0.700, floor_s=0.06),
    timeout_s=3.0,
    evidence="Convoy Jetson soak: p50 122 ms / p95 636 ms; deploy smoke p50 181 / p95 900 ms",
    concurrency=1,
)
EDGE_QWEN_DEVICE = PlannerProfile(
    name="edge-qwen2.5-1.5b-device",
    model="Qwen2.5-1.5B-Instruct Q4_K_M, llama.cpp CUDA, Jetson Orin Nano Super 8 GB (connected device)",
    placement="edge",
    latency=None,  # every call is measured; nothing is drawn from a model
    timeout_s=45.0,  # the client's deadline for a terminal result (device_planner.FailurePolicy)
    evidence="real calls through Convoy's device chat API; end-to-end round trip and on-device latency measured per call",
    concurrency=1,
    source="device",
)
CLOUD_ASTRA = PlannerProfile(
    name="cloud-gpt-astra",
    model="GPT-6 Astra (low reasoning effort), hosted API",
    placement="cloud",
    latency=LatencyModel(3.9, 6.0, floor_s=1.5),
    timeout_s=12.0,
    connect_timeout_s=3.0,
    evidence=("Artificial Analysis (OpenAI API): median time to first answer token 2.96 s, 43.2 tok/s, "
              "+~0.9 s for ~40 tokens; p95 6.0 s assumed (medians only published)"),
    concurrency=2,
)


@dataclass(frozen=True)
class MotorPolicy:
    """What executes a skill call on the robot.

    ``runtime`` "scripted" uses the IK skill library; "learned" runs a
    ``policies.SkillPolicy`` (registered by name) through ``LearnedSkill`` with
    chunked actions, ``latency`` per inference and a 1 s validity horizon.
    """

    name: str
    placement: str
    available: bool
    reason: str = ""
    embodiment: str = "wheeled-bimanual-2x6dof-parallel-gripper"
    runtime: str = "scripted"
    latency: LatencyModel | None = None


SCRIPTED_SKILLS = MotorPolicy("scripted-ik-skills", "edge", True,
                              "closed-form IK skill library reading simulator state (privileged)")
SMOLVLA = MotorPolicy(
    "smolvla-450m", "edge", False,
    "no checkpoint for this embodiment: lerobot/smolvla_metaworld is single-arm Sawyer, 4-D Cartesian action, "
    "one 480x480 camera; it has not been trained or evaluated on a two-arm 14-D joint robot",
    runtime="learned",
    # Unused while no checkpoint exists. Convoy's measured worker inference for the MetaWorld checkpoint
    # on a development host (integrations/lerobot/README.md): p50 749 ms, p95 ~1169 ms per decision.
    latency=LatencyModel(0.749, 1.169))


@dataclass(frozen=True)
class DeploymentConfig:
    id: str
    label: str
    skill_planner: PlannerProfile  # chooses each skill call, closed loop, blocking
    motor: MotorPolicy
    task_planner: PlannerProfile | None = None  # optional decomposition / verification planner
    task_planner_deadline_s: float = 8.0  # how long the start waits for the task plan
    verify_every: int = 8  # completed skills between asynchronous verification calls
    retry_backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
    description: str = ""
    deployment: str = ""  # where each model runs, as the platform's configuration label shows it
    policy: str = ""  # what executes the skills, as the platform's policy label shows it


SCRIPTED_POLICY = "Scripted IK skills on sim state; planner choices: rule-based stand-in, modeled latency"
DEVICE_PLANNER_POLICY = "Scripted IK skills on sim state; planner: Qwen2.5-1.5B on the Jetson, real calls, measured latency"

CONFIGS: dict[str, DeploymentConfig] = {
    c.id: c for c in (
        DeploymentConfig(
            "edge_qwen_edge_skills", "Edge Qwen", EDGE_QWEN_DEVICE, SCRIPTED_SKILLS,
            description=("Qwen2.5-1.5B on a connected Jetson chooses every skill call from a text description "
                         "of the simulator state (real calls, measured round trips); scripted skills execute it."),
            deployment="Edge: Qwen2.5-1.5B (Jetson Orin Nano) · Cloud: none", policy=DEVICE_PLANNER_POLICY),
        DeploymentConfig(
            "edge_qwen_cloud_astra", "Edge Qwen + GPT Astra", EDGE_QWEN, SCRIPTED_SKILLS,
            task_planner=CLOUD_ASTRA,
            description=("GPT Astra decomposes the task before the start (waits up to 8 s) and re-verifies every "
                         "8 skills without blocking; Qwen routes each skill call and keeps working when the "
                         "cloud is unreachable."),
            deployment="Edge: Qwen2.5-1.5B (Jetson Orin Nano) · Cloud: GPT-6 Astra (low)", policy=SCRIPTED_POLICY),
        DeploymentConfig(
            "cloud_astra_only", "GPT Astra", CLOUD_ASTRA, SCRIPTED_SKILLS,
            description=("Every skill call is a blocking cloud call; during an outage the robot holds and "
                         "retries with 1-8 s backoff."),
            deployment="Edge: none · Cloud: GPT-6 Astra (low)", policy=SCRIPTED_POLICY),
        DeploymentConfig(
            "edge_smolvla_cloud_astra", "Edge SmolVLA + GPT Astra", CLOUD_ASTRA, SMOLVLA,
            description=("GPT Astra plans; SmolVLA on the Jetson would execute skills. No checkpoint exists for "
                         "this robot, so skill dispatch reports the policy unavailable."),
            deployment="Edge: SmolVLA-450M (Jetson Orin Nano) · Cloud: GPT-6 Astra (low)",
            policy="SmolVLA-450M: no checkpoint for this two-arm robot (unavailable, not faked)"),
    )
}


@dataclass(frozen=True)
class SliceSpec:
    id: str
    pills: int = 24
    center: tuple[float, float] = (0.49, 0.0)
    half: tuple[float, float] = (0.10, 0.22)
    near_bottle: int = 0
    lighting: str = "nominal"
    outages: tuple[tuple[float, float], ...] = ()
    description: str = ""
    short: str = ""  # a few words for labels

    def network(self) -> NetworkModel:
        return NetworkModel(outages=self.outages)


SLICES: dict[str, SliceSpec] = {
    s.id: s for s in (
        SliceSpec("nominal", description="24 pills scattered over 0.20 x 0.44 m of the mat in front of the bottle",
                  short="nominal"),
        SliceSpec("pill_count_30", pills=30, description="30 pills in the nominal region", short="30 pills"),
        SliceSpec("scatter_wide", center=(0.48, 0.0), half=(0.13, 0.27),
                  description="24 pills spread to the edge of the reachable mat (0.26 x 0.54 m)", short="wide scatter"),
        SliceSpec("scatter_tight", center=(0.50, 0.0), half=(0.055, 0.085),
                  description="24 pills clustered in 0.11 x 0.17 m: many touching neighbours", short="tight cluster"),
        SliceSpec("lighting_dim", lighting="dim", description="dim warm light (camera frames only; skills read state)",
                  short="dim light"),
        SliceSpec("pill_near_bottle", near_bottle=4, description="4 of 24 pills within 4-16 mm of the bottle wall",
                  short="pills by the bottle"),
        SliceSpec("network_outage", outages=((15.0, 45.0),),
                  description="site-to-cloud link down from t=15 s to t=45 s", short="cloud outage 15–45 s"),
    )
}

DEFAULT_HORIZON_S = 150.0


@dataclass(frozen=True)
class EpisodeSpec:
    config: DeploymentConfig
    slice: SliceSpec
    seed: int
    horizon_s: float = DEFAULT_HORIZON_S
    extra: dict = field(default_factory=dict)


def release_manifest(config: DeploymentConfig) -> dict:
    """Immutable description of what an episode ran; its SHA-256 is the release digest."""
    from dataclasses import asdict

    from . import physics as P
    from .robot import RobotSpec

    def planner(profile: PlannerProfile | None) -> dict | None:
        if profile is None:
            return None
        out = {"name": profile.name, "model": profile.model, "placement": profile.placement,
               "timeout_s": profile.timeout_s, "concurrency": profile.concurrency, "evidence": profile.evidence}
        if profile.source == "device":
            from .device_planner import settings

            return {**out, "latency": "measured per call (no latency model)",
                    "decision_policy": "the model on the connected device, through the device chat API",
                    "request": settings()}
        return {**out, "latency_p50_s": profile.latency.p50_s, "latency_p95_s": profile.latency.p95_s,
                "decision_policy": "deterministic rule-based stand-in (GreedyPillPlanner)"}

    return {
        "schema_version": 1,
        "profile": "bimanual-pill-task-v1",
        "task": "pills_to_bottle",
        "config": {"id": config.id, "label": config.label, "description": config.description},
        "skill_planner": planner(config.skill_planner),
        "task_planner": planner(config.task_planner),
        "motor_policy": asdict(config.motor),
        "physics": {"timestep_s": P.TIMESTEP_S, "integrator": P.INTEGRATOR, "solver": P.SOLVER,
                    "iterations": P.SOLVER_ITERATIONS, "tolerance": P.SOLVER_TOLERANCE, "cone": P.CONE,
                    "impratio": P.IMPRATIO, "noslip_iterations": P.NOSLIP_ITERATIONS, "contact_solref": list(P.CONTACT_SOLREF),
                    "contact_solimp": list(P.CONTACT_SOLIMP)},
        "robot": asdict(RobotSpec()),
        "camera": {"name": "head_camera", "fovy_deg": RobotSpec().head_camera_fovy},
    }
