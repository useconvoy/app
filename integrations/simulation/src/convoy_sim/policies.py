"""Small policy boundary; the first supplied policies do not contain learned weights."""

from typing import Protocol

import numpy as np
from metaworld.policies.sawyer_pick_place_v3_policy import SawyerPickPlaceV3Policy
from numpy.typing import NDArray


class Policy(Protocol):
    def get_action(self, observation: NDArray[np.float64]) -> NDArray: ...


class ScriptedPickPlace:
    def __init__(self) -> None:
        self.expert = SawyerPickPlaceV3Policy()

    def get_action(self, observation: NDArray[np.float64]) -> NDArray:
        # Upstream experts return unconstrained motion commands. This explicit
        # reference-policy transform is distinct from validating other policies.
        return np.clip(self.expert.get_action(observation), -1, 1).astype(np.float32)


class ZeroAction:
    def get_action(self, observation: NDArray[np.float64]) -> NDArray:
        return np.zeros(4, dtype=np.float32)


POLICIES = {"scripted": ScriptedPickPlace, "zero": ZeroAction}


def validate_action(action: NDArray) -> NDArray[np.float32]:
    values = np.asarray(action, dtype=np.float64)
    if values.shape != (4,) or not np.isfinite(values).all():
        raise ValueError("action must contain four finite numbers")
    if (np.abs(values) > 1).any():
        raise ValueError("action exceeds the normalized [-1, 1] bounds")
    return values.astype(np.float32)
