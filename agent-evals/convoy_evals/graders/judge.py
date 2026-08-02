"""Fresh-context LLM judge — pinned model + content-addressed prompt.

Isolation guarantee lives in the TYPE: JudgeInput has constructors only for
artifact / key_excerpt / world — there is deliberately no way to feed the judge
a transcript, messages, plan text, or model_call events. Keep it that way.

cacheKey = sha256(promptHash + modelId + bundleHash). cache-only mode (PR CI)
NEVER bills the API: a miss is a loud 'error' verdict. live mode makes
N=samples temperature-0 calls (httpx), majority verdict, mean score, 2-1 split
-> lowConfidence, and writes the aggregated result back to the cache.

Port of src/graders/judge.ts.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from ..sandbox.api import GradeRecord
from ..schema.scenario import JudgeGrader
from .calibration import CalibrationStore, empty_calibration_store, judge_is_calibrated
from .end_state import artifact_satisfies, file_for_artifact, find_artifact_events
from .util import (
    GraderOutcome,
    ItemCtx,
    canonical_json,
    clamp01,
    ev_note,
    make_key_resolver,
    sha256_hex,
)
from ..schema.verdict import JudgeRationaleEvidence

# Aggregated N-sample result — the unit the cache stores (one JSON file per key):
#   {"verdicts": ["pass"|"fail", ...], "score": float, "rationales": [str, ...]}
JudgeCacheEntry = Dict[str, Any]

# ---------------------------------------------------------------------------
# Evidence bundle — built STRICTLY from spec.inputs
# ---------------------------------------------------------------------------


def build_evidence_bundle(
    spec: JudgeGrader, record: GradeRecord, item_ctx: Optional[ItemCtx]
) -> List[Any]:
    rk = make_key_resolver(record.answerKey, item_ctx)
    bundle: List[Any] = []
    for input_ in spec.inputs:
        if input_.kind == "artifact":
            hits = [
                e
                for e in find_artifact_events(record, input_.selector, item_ctx)
                if artifact_satisfies(record, e, input_.selector)
            ]
            latest = hits[-1] if hits else None
            file = file_for_artifact(record, latest) if latest is not None else None
            bundle.append(
                {
                    "kind": "artifact",
                    "tag": latest.tag if latest is not None else None,
                    "hash": latest.hash if latest is not None else None,
                    "content": file.content if file is not None else None,
                }
            )
        elif input_.kind == "key_excerpt":
            ref = input_.ref["$key"]
            bundle.append({"kind": "key_excerpt", "ref": ref, "value": rk(ref)})
        else:  # world
            bundle.append(
                {"kind": "world", "query": input_.query, "result": record.worldQuery(input_.query)}
            )
    return bundle


def judge_cache_key(
    spec: JudgeGrader, record: GradeRecord, item_ctx: Optional[ItemCtx]
) -> str:
    """sha256(promptHash + modelId + bundleHash) — exported so tests can pre-seed the cache."""
    bundle_hash = sha256_hex(canonical_json(build_evidence_bundle(spec, record, item_ctx)))
    return sha256_hex(spec.promptHash + spec.model + bundle_hash)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _outcome_from_entry(entry: JudgeCacheEntry, advisory: bool) -> GraderOutcome:
    verdicts = list(entry.get("verdicts", []))
    passes = sum(1 for v in verdicts if v == "pass")
    fails = len(verdicts) - passes
    status = "pass" if passes > fails else "fail"
    evidence: List[Any] = [
        JudgeRationaleEvidence(kind="judge_rationale", sampleIdx=i, text=text)
        for i, text in enumerate(entry.get("rationales", []))
    ]
    if len(evidence) == 0:
        evidence.append(ev_note("judge verdict with no stored rationale"))
    outcome = GraderOutcome(
        status=status, score=clamp01(float(entry.get("score", 0))), evidence=evidence
    )
    if passes > 0 and fails > 0:
        outcome.lowConfidence = True
    if advisory:
        outcome.advisory = True
    return outcome


# ---------------------------------------------------------------------------
# Live sampling (Anthropic messages API via httpx)
# ---------------------------------------------------------------------------


def _parse_sample(text: str, pass_at: float) -> Dict[str, Any]:
    json_match = re.search(r"\{.*\}", text, re.S)
    verdict: Optional[str] = None
    score = 0.0
    rationale = text[:500]
    if json_match is not None:
        try:
            parsed = json.loads(json_match.group(0))
            if isinstance(parsed, dict):
                if parsed.get("verdict") in ("pass", "fail"):
                    verdict = parsed["verdict"]
                if isinstance(parsed.get("score"), (int, float)) and not isinstance(
                    parsed.get("score"), bool
                ):
                    score = clamp01(float(parsed["score"]))
                if isinstance(parsed.get("rationale"), str):
                    rationale = parsed["rationale"]
        except ValueError:
            pass  # fall through to score-threshold verdict
    if verdict is None:
        verdict = "pass" if score >= pass_at else "fail"
    return {"verdict": verdict, "score": score, "rationale": rationale}


def _sample_live(spec: JudgeGrader, bundle: List[Any], api_key: str) -> JudgeCacheEntry:
    import httpx  # dev/extra dependency; only needed in live mode

    with open(spec.promptFile, "r", encoding="utf-8") as fh:
        prompt = fh.read()
    prompt_hash = sha256_hex(prompt)
    if prompt_hash != spec.promptHash:
        raise ValueError(
            "judge prompt hash mismatch: file %s hashes to %s, spec pins %s"
            % (spec.promptFile, prompt_hash, spec.promptHash)
        )
    rubric_text = "\n".join(
        "- [%s] (weight %s) %s" % (r.id, r.weight, r.criterion) for r in spec.rubric
    )
    user_content = "\n".join(
        [
            "Grade the evidence bundle against the rubric.",
            'Respond with ONLY a JSON object: {"verdict": "pass"|"fail", "score": 0..1, "rationale": "..."}.',
            "\nRubric:\n%s" % rubric_text,
            "\nEvidence bundle:\n%s" % json.dumps(bundle, indent=2, default=str),
        ]
    )

    verdicts: List[str] = []
    rationales: List[str] = []
    score_sum = 0.0
    with httpx.Client(timeout=120.0) as client:
        for _ in range(spec.samples):
            res = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "content-type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": spec.model,
                    "max_tokens": 1024,
                    "temperature": 0,
                    "system": prompt,
                    "messages": [{"role": "user", "content": user_content}],
                },
            )
            if res.status_code != 200:
                raise RuntimeError(
                    "judge model call failed: HTTP %s %s" % (res.status_code, res.text)
                )
            body = res.json()
            text = "\n".join(
                b.get("text", "")
                for b in body.get("content", [])
                if b.get("type") == "text" and isinstance(b.get("text"), str)
            )
            sample = _parse_sample(text, spec.passAt)
            verdicts.append(sample["verdict"])
            rationales.append(sample["rationale"])
            score_sum += sample["score"]
    return {
        "verdicts": verdicts,
        "score": 0.0 if spec.samples == 0 else score_sum / spec.samples,
        "rationales": rationales,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_judge(
    spec: JudgeGrader,
    record: GradeRecord,
    item_ctx: Optional[ItemCtx],
    mode: str = "cache-only",
    cache_dir: Optional[str] = None,
    calibration: Optional[CalibrationStore] = None,
) -> GraderOutcome:
    advisory = not judge_is_calibrated(
        calibration if calibration is not None else empty_calibration_store(),
        spec.judgeId,
        spec.promptHash,
    )
    cache_key = judge_cache_key(spec, record, item_ctx)
    cache_path = os.path.join(str(cache_dir), cache_key + ".json") if cache_dir else None

    if cache_path is not None and os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            entry = json.load(fh)
        return _outcome_from_entry(entry, advisory)

    if mode == "cache-only":
        outcome = GraderOutcome(
            status="error", score=0.0, evidence=[ev_note("judge cache miss (live disabled)")]
        )
        if advisory:
            outcome.advisory = True
        return outcome

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        outcome = GraderOutcome(
            status="error",
            score=0.0,
            evidence=[ev_note("live judge requested but ANTHROPIC_API_KEY is not set")],
        )
        if advisory:
            outcome.advisory = True
        return outcome

    bundle = build_evidence_bundle(spec, record, item_ctx)
    entry = _sample_live(spec, bundle, api_key)
    if cache_path is not None:
        os.makedirs(str(cache_dir), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, indent=2)
    return _outcome_from_entry(entry, advisory)
