"""Corpus generator tests (Python port).

Covers: mulberry32 PRNG parity with the TS implementation, regeneration
parity against the committed corpus under scenarios/, Python-side
determinism, and pydantic schema validity of every emitted file.
"""

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convoy_evals.corpus.generate import (  # noqa: E402
    PackManifest,
    compute_pack_hash,
    generate_corpus,
)
from convoy_evals.corpus.prng import create_prng, mulberry32  # noqa: E402
from convoy_evals.schema.scenario import AnswerKey, EvalSetConfig, Scenario  # noqa: E402

# The TS run parameters that produced the committed corpus: t0 = today - 5d
# = 2026-07-28 (see generateCorpus in src/corpus/generate.ts and
# facts.t0 in scenarios/keys/renewal-12.key.json).
TODAY = "2026-08-02"
COMMITTED = ROOT / "scenarios"

SCENARIO_FILES = [
    "renewal-golden-3.scenario.json",
    "renewal-gauntlet-12.scenario.json",
    "renewal-silent-carrier.scenario.json",
]
JSON_FILES = SCENARIO_FILES + ["keys/renewal-12.key.json", "sets/renewal-prep-v1.json"]

# Ground truth captured from the TS implementation (src/corpus/prng.ts) via:
#   node -e 'function mulberry32(seed){let a=seed>>>0;return()=>{
#     a=(a+0x6d2b79f5)>>>0;let t=a;t=Math.imul(t^(t>>>15),t|1);
#     t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
#     const r=mulberry32(7);
#     for(let i=0;i<8;i++)console.log(r().toPrecision(17));'
MULBERRY32_SEED7_FLOATS = [
    0.011704753153026104,
    0.061958257574588060,
    0.97690763277933002,
    0.69902870571240783,
    0.52144526853226125,
    0.40552168805152178,
    0.46623263251967728,
    0.23992518591694534,
]
# Same stream as raw uint32s (float * 2**32 is exact — the division by 2**32
# in mulberry32 is lossless in IEEE-754 doubles).
MULBERRY32_SEED7_UINTS = [
    50271532,
    266108690,
    4195786334,
    3002305430,
    2239590375,
    1741702388,
    2002453909,
    1030470827,
]


def list_files_rec(root: Path):
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")
    )


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("corpus-py")
    result = generate_corpus(str(out), packets=12, seed=7, today=TODAY)
    return out, result


# ---------------------------------------------------------------------------
# 1. PRNG parity
# ---------------------------------------------------------------------------


class TestPrngParity:
    def test_mulberry32_seed7_matches_ts_ground_truth(self):
        rand = mulberry32(7)
        got = [rand() for _ in range(len(MULBERRY32_SEED7_FLOATS))]
        # Exact equality, not approx: the port must be bit-identical.
        assert got == MULBERRY32_SEED7_FLOATS
        assert [math.floor(v * 4294967296) for v in got] == MULBERRY32_SEED7_UINTS

    def test_prng_facade_consumes_the_same_stream(self):
        prng = create_prng(7)
        # int(min, max) = min + floor(next() * span), one draw per call.
        expected_ints = [
            lo + math.floor(f * (hi - lo + 1))
            for f, (lo, hi) in zip(MULBERRY32_SEED7_FLOATS[:3], [(1, 12), (1, 28), (900, 24000)])
        ]
        assert [prng.int(1, 12), prng.int(1, 28), prng.int(900, 24000)] == expected_ints
        assert prng.uniform(1.5, 25) == 1.5 + MULBERRY32_SEED7_FLOATS[3] * 23.5

    def test_shuffle_is_fisher_yates_over_the_stream(self):
        # First draws of the seed-7 generator drive the carrier shuffle in
        # buildPackets; the committed corpus pins the outcome (POL-101 =
        # Blue Heron Casualty etc.), so pin the shuffle here too.
        prng = create_prng(7)
        shuffled = prng.shuffle(
            (
                "Atlas Mutual",
                "Keystone National",
                "Granite Peak Insurance",
                "Harborline Underwriters",
                "Blue Heron Casualty",
                "Meridian Specialty",
            )
        )
        assert shuffled == [
            "Blue Heron Casualty",
            "Keystone National",
            "Granite Peak Insurance",
            "Harborline Underwriters",
            "Meridian Specialty",
            "Atlas Mutual",
        ]

    def test_prng_guards(self):
        prng = create_prng(1)
        with pytest.raises(ValueError):
            prng.int(2, 1)
        with pytest.raises(ValueError):
            prng.pick([])


# ---------------------------------------------------------------------------
# 2. Regeneration vs the committed corpus
# ---------------------------------------------------------------------------


class TestRegenerationMatchesCommitted:
    def test_file_trees_match(self, corpus):
        out, _ = corpus
        assert list_files_rec(out) == list_files_rec(COMMITTED)

    def test_json_files_parse_equal(self, corpus):
        out, _ = corpus
        for rel in JSON_FILES + ["packs/renewal-12/pack.json"]:
            regenerated = json.loads((out / rel).read_text(encoding="utf-8"))
            committed = json.loads((COMMITTED / rel).read_text(encoding="utf-8"))
            assert regenerated == committed, f"parsed JSON differs: {rel}"

    def test_pack_text_files_byte_identical(self, corpus):
        out, _ = corpus
        pack_rel = "packs/renewal-12"
        rels = [
            r for r in list_files_rec(COMMITTED / pack_rel) if r != "pack.json"
        ]
        assert rels, "committed pack has no text files?"
        for rel in rels:
            assert (out / pack_rel / rel).read_bytes() == (
                COMMITTED / pack_rel / rel
            ).read_bytes(), f"pack file differs: {pack_rel}/{rel}"

    def test_quarantine_byte_identical(self, corpus):
        out, _ = corpus
        assert (out / "quarantine.yaml").read_bytes() == (COMMITTED / "quarantine.yaml").read_bytes()

    def test_pack_hash_matches_committed_fixture(self, corpus):
        out, result = corpus
        assert result.t0 == "2026-07-28"
        assert compute_pack_hash(str(out / "packs" / "renewal-12")) == result.pack_hash
        for rel in SCENARIO_FILES:
            committed = json.loads((COMMITTED / rel).read_text(encoding="utf-8"))
            assert committed["fixture"]["pack"] == "renewal-12"
            assert committed["fixture"]["packHash"] == result.pack_hash, f"{rel} packHash"
            assert committed["answerKeyRef"]["path"] == "keys/renewal-12.key.json"
            assert committed["answerKeyRef"]["hash"] == result.key_hash, f"{rel} answerKeyRef.hash"

    def test_key_dates_materialized_pack_dates_templated(self, corpus):
        out, _ = corpus
        pack = (out / "packs/renewal-12/pack.json").read_text(encoding="utf-8")
        assert "{{t0+20d}}" in pack
        key = json.loads((out / "keys/renewal-12.key.json").read_text(encoding="utf-8"))
        assert "{{t0" not in json.dumps(key)
        assert key["facts"]["t0"] == "2026-07-28"
        assert key["perItem"]["packet/POL-101"]["expiring_date"] == "2026-08-17"


# ---------------------------------------------------------------------------
# 3. Python-side determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_runs_are_byte_identical(self, corpus, tmp_path):
        out_a, res_a = corpus
        out_b = tmp_path / "corpus-b"
        res_b = generate_corpus(str(out_b), packets=12, seed=7, today=TODAY)
        files_a = list_files_rec(out_a)
        assert files_a == list_files_rec(out_b)
        assert files_a
        for rel in files_a:
            assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), f"file differs: {rel}"
        assert res_a.pack_hash == res_b.pack_hash
        assert res_a.key_hash == res_b.key_hash
        assert res_a.written_files == res_b.written_files

    def test_result_written_files_lists_the_tree(self, corpus):
        out, result = corpus
        assert result.written_files == list_files_rec(out)


# ---------------------------------------------------------------------------
# 4. Every emitted file validates through the pydantic schemas
# ---------------------------------------------------------------------------


class TestSchemaValidity:
    def test_scenarios_validate(self, corpus):
        out, _ = corpus
        for rel in SCENARIO_FILES:
            Scenario.model_validate(json.loads((out / rel).read_text(encoding="utf-8")))

    def test_key_set_and_manifest_validate(self, corpus):
        out, _ = corpus
        AnswerKey.model_validate(
            json.loads((out / "keys/renewal-12.key.json").read_text(encoding="utf-8"))
        )
        EvalSetConfig.model_validate(
            json.loads((out / "sets/renewal-prep-v1.json").read_text(encoding="utf-8"))
        )
        PackManifest.model_validate(
            json.loads((out / "packs/renewal-12/pack.json").read_text(encoding="utf-8"))
        )

    def test_packets_below_three_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            generate_corpus(str(tmp_path / "x"), packets=2, seed=7, today=TODAY)
