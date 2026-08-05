"""Seeded PRNG for corpus generation — mulberry32, exact JS parity.

Port of src/corpus/prng.ts. Every stochastic choice in the generator flows
through ONE Prng instance in a fixed call order, which is what makes
`same seed → identical corpus` hold — including across the TS and Python
implementations. All arithmetic mirrors JavaScript 32-bit integer semantics
via explicit `& 0xFFFFFFFF` masking; the final division by 2**32 is exact in
IEEE-754 double precision, so the float stream is bit-identical to the TS
generator's.
"""

from __future__ import annotations

import math
from typing import Callable, List, Sequence, TypeVar

_MASK = 0xFFFFFFFF

Rand = Callable[[], float]

T = TypeVar("T")


def mulberry32(seed: int) -> Rand:
    """mulberry32: fast 32-bit seeded generator, uniform in [0, 1).

    Bit-for-bit port of the TS version:
        a = (a + 0x6d2b79f5) >>> 0
        t = Math.imul(t ^ (t >>> 15), t | 1)
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296

    `Math.imul` (signed 32-bit multiply) and `>>>`/`|`/`^` (ToInt32/ToUint32
    coercions) are congruent mod 2**32 to plain Python integer ops masked with
    `& 0xFFFFFFFF`, so working on the unsigned bit patterns reproduces the JS
    stream exactly.
    """
    a = seed & _MASK

    def next_() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & _MASK
        t = a
        t = ((t ^ (t >> 15)) * (t | 1)) & _MASK
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & _MASK)) & _MASK
        return ((t ^ (t >> 14)) & _MASK) / 4294967296

    return next_


class Prng:
    """Prng facade over mulberry32 — mirrors src/corpus/prng.ts createPrng."""

    def __init__(self, seed: int) -> None:
        self._next = mulberry32(seed)

    def next(self) -> float:
        """Uniform float in [0, 1)."""
        return self._next()

    def int(self, min_: int, max_: int) -> int:
        """Uniform integer in [min, max] (both inclusive)."""
        if max_ < min_:
            raise ValueError(f"prng.int: max {max_} < min {min_}")
        # Mirrors `min + Math.floor(next() * (max - min + 1))` — the float
        # multiply is IEEE double in both languages, so results are identical.
        return min_ + math.floor(self._next() * (max_ - min_ + 1))

    def uniform(self, min_: float, max_: float) -> float:
        """Uniform float in [min, max)."""
        return min_ + self._next() * (max_ - min_)

    def pick(self, items: Sequence[T]) -> T:
        """Pick one element. Raises on empty input."""
        if len(items) == 0:
            raise ValueError("prng.pick: empty array")
        return items[self.int(0, len(items) - 1)]

    def shuffle(self, items: Sequence[T]) -> List[T]:
        """Fisher-Yates shuffle into a NEW list (input untouched)."""
        out = list(items)
        for i in range(len(out) - 1, 0, -1):
            j = self.int(0, i)
            out[i], out[j] = out[j], out[i]
        return out


def create_prng(seed: int) -> Prng:
    return Prng(seed)
