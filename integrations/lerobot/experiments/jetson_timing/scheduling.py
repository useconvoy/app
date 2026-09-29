"""Bounded action playback using the simulator host's monotonic clock.

Chunk indices refer to observation time, not arrival time. Late prefixes are
discarded, and a chunk cannot extend its validity by arriving late. This is a
deliberately simple experimental overlap strategy, not RTC or a safety controller.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    sequence: int
    observed_at: float
    period: float
    actions: tuple[tuple[float, ...], ...]

    def __post_init__(self):
        if self.sequence < 0 or not math.isfinite(self.observed_at):
            raise ValueError("invalid observation identity")
        if not math.isfinite(self.period) or self.period <= 0 or not 1 <= len(self.actions) <= 100:
            raise ValueError("invalid chunk horizon")
        if any(len(a) != 4 or any(not math.isfinite(x) or abs(x) > 1 for x in a) for a in self.actions):
            raise ValueError("expected bounded finite Cartesian/gripper actions")


class Playback:
    def __init__(self, max_age: float):
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("max_age must be finite and positive")
        self.max_age = max_age
        self.chunk = None
        self.latest_sequence = -1
        self.last_slot = None
        self.gripper = 0.0

    def accept(self, chunk: Chunk, now: float) -> bool:
        if chunk.sequence <= self.latest_sequence:
            return False
        self.latest_sequence = chunk.sequence
        age = now - chunk.observed_at
        if age < 0 or age >= min(self.max_age, chunk.period * len(chunk.actions)):
            return False
        self.chunk = chunk
        self.last_slot = None
        return True

    def action(self, now: float):
        c = self.chunk
        age = now - c.observed_at if c else None
        if c and 0 <= age < self.max_age:
            slot = int(age / c.period)
            if slot < len(c.actions) and slot != self.last_slot:
                self.last_slot = slot
                value = c.actions[slot]
                self.gripper = value[3]
                return value, {"source_sequence": c.sequence, "chunk_index": slot, "observation_age_s": age}
        # MetaWorld-only fallback: zero Cartesian delta and retain gripper command.
        # This is not a certified physical stop, and physics continues to advance.
        return (0.0, 0.0, 0.0, self.gripper), {"fallback": True}
