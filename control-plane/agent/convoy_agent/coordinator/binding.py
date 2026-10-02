"""Small local-owner handoff; no endpoints or credentials enter durable observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class BindingObservation:
    release_digest: str
    profile: str
    action_artifact_sha256: str
    planner_artifact_sha256: str | None


@dataclass(frozen=True)
class PreparedBinding:
    binding_id: str
    worker: object
    planner: object | None
    observation: BindingObservation
    adapter_factory: Callable | None = None


class BundleOwner(Protocol):
    def prepare(self, deployment: dict, manifest: dict) -> PreparedBinding:
        """Check a live bundle on every call; change binding_id after any restart.

        Called only while locally idle and with no admitted/unresolved mission.
        Arguments are snapshots. The owner must not mutate immutable release data.
        Returned candidates gain execution authority only after coordinator fences,
        probes, and durable observation. Registered profiles also supply an adapter
        factory bound to their verified immutable model bytes.
        """
        ...
