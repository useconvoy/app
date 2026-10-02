"""Portable robot description: declared mechanics and pinned simulator inputs, not authority."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Positive = Annotated[FiniteFloat, Field(gt=0)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Spec):
    source: Literal["manufacturer", "imported", "measured", "estimated", "unknown"]
    note: str = Field(default="", max_length=1000)


class Joint(Spec):
    name: Name
    kind: Literal["revolute", "prismatic", "continuous", "fixed"]
    lower: FiniteFloat | None = None
    upper: FiniteFloat | None = None
    velocity_limit: Positive | None = None
    effort_limit: Positive | None = None
    evidence: Evidence

    @model_validator(mode="after")
    def limits(self):
        if (self.lower is None) != (self.upper is None):
            raise ValueError("joint bounds must be supplied together")
        if self.lower is not None and self.lower >= self.upper:
            raise ValueError("joint lower bound must be below upper bound")
        return self


class Sensor(Spec):
    name: Name
    kind: Literal["camera", "depth", "joint-state", "imu", "force-torque", "other"]
    frame: Name
    rate_hz: Positive | None = None
    evidence: Evidence


class Asset(Spec):
    # Reference only: the API never fetches URLs or executes an uploaded file.
    uri: str = Field(min_length=1, max_length=2048, pattern=r"^(https://|artifact:)")
    sha256: Digest
    format: Literal["urdf", "mjcf", "usd", "bundle"]


class SimulationModel(Spec):
    engine: Literal["mujoco", "isaac"]
    engine_version: Name
    asset: Asset
    controller: Name
    evidence: Evidence


class RobotProfileSpec(Spec):
    schema_version: Literal[1] = 1
    embodiment: Name
    description: str = Field(default="", max_length=2000)
    # Geometry/topology and detailed inertial parameters live in the pinned description asset.
    description_asset: Asset | None = None
    joints: list[Joint] = Field(default_factory=list, max_length=128)
    sensors: list[Sensor] = Field(default_factory=list, max_length=64)
    command_interface: Literal["skill", "cartesian-pose", "joint-position", "joint-velocity", "joint-torque"]
    adapter: Name
    control_rate_hz: Positive
    dynamics: Evidence
    simulations: list[SimulationModel] = Field(default_factory=list, max_length=2)
    # Existing execution profile contract, if the robot matches one of our supported runners.
    execution_profile: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def unique_names(self):
        for values, label in (([x.name for x in self.joints], "joint"),
                              ([x.name for x in self.sensors], "sensor"),
                              ([x.engine for x in self.simulations], "simulation engine")):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label}")
        if self.command_interface.startswith("joint-") and not self.joints:
            raise ValueError("joint command interfaces require named joints")
        return self


def simulation_readiness(spec: dict) -> dict:
    """A declared asset is deliberately not labelled runnable or physically qualified."""
    models = spec["simulations"]
    return {
        "state": "assets-declared" if models else "missing-assets",
        "engines": [m["engine"] for m in models],
        "runtime_verified": False,
        "dynamics_source": spec["dynamics"]["source"],
        "detail": "Runner must verify asset digest, load the model and validate interfaces before execution."
        if models else "Add a pinned MuJoCo or Isaac model to simulate this profile.",
    }
