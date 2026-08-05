"""Eval-corpus generator (Python port of src/corpus).

Same (seed, packets, today) → the same corpus the TypeScript generator
committed under scenarios/ (deep-equal parsed JSON; byte-identical pack files
and hashes).
"""

from .prng import Prng, Rand, create_prng, mulberry32

# Lazy re-exports from .generate (PEP 562): importing them eagerly here would
# trigger runpy's double-import RuntimeWarning when the CLI is invoked as
# `python -m convoy_evals.corpus.generate`.
_GENERATE_EXPORTS = frozenset(
    {
        "CorpusResult",
        "PackFileEntry",
        "PackManifest",
        "PackRecord",
        "PacketFacts",
        "compute_pack_hash",
        "generate_corpus",
        "iso_plus_days",
        "sha256_of_file",
    }
)


def __getattr__(name):
    if name in _GENERATE_EXPORTS:
        from . import generate as _generate

        return getattr(_generate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CorpusResult",
    "PackFileEntry",
    "PackManifest",
    "PackRecord",
    "PacketFacts",
    "Prng",
    "Rand",
    "compute_pack_hash",
    "create_prng",
    "generate_corpus",
    "iso_plus_days",
    "mulberry32",
    "sha256_of_file",
]
