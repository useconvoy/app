"""convoy-evals — the eval-harness CLI (port of src/cli/index.ts).

Terse stdout; --json on run/replay for machine output.

    run      --set <path> --subject scripted:<executor> [--filter id,id]
             [--trials N] [--usd-cap X] [--out dir] [--judge live|cache-only]
             [--concurrency N] [--json]
    replay   --set <path> --records <dir> [--judge live|cache-only] [--json]
    report   --result <suite-result.json> [--set <path>]
             [--rehearsal <scenarioId> --trial N --records <dir>]
    lint     --set <path>
    dry-run  --set <path> [--scenario id]

run/replay/dry-run import the runner lazily so lint/report keep working before
the sandbox/executors/graders/scoring siblings land.

Invoke as a module (`python -m convoy_evals.cli ...`) or via the installed
`convoy-evals` console script; both call main(). Exit codes: 0 green/clean,
1 not green / lint problems / dry-run rejection, 2 usage or harness error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from typing_extensions import get_args

from convoy_evals.runtime.events import GateKind
from convoy_evals.sandbox.api import WorldBundle
from convoy_evals.schema.match import resolve_path
from convoy_evals.schema.verdict import SuiteResult
from convoy_evals.reports.rehearsal_report import render_rehearsal_report
from convoy_evals.reports.suite_report import write_suite_report
from convoy_evals.runner.store import (
    LoadedEvalSet,
    answer_key_hash_matches,
    default_out_dir,
    events_file_name,
    load_answer_key_file,
    load_eval_set,
    load_scenario,
    read_jsonl_events,
    resolve_answer_key_path,
    timestamp_slug,
    world_file_name,
)


class CliError(Exception):
    pass


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def table(headers: List[str], rows: List[List[str]]) -> str:
    widths = [
        max(len(h), *([len(r[i]) for r in rows] or [0])) if rows else len(h)
        for i, h in enumerate(headers)
    ]

    def line(cells: List[str]) -> str:
        return "  ".join((c or "").ljust(widths[i]) for i, c in enumerate(cells))

    out = [line(headers), line(["-" * w for w in widths])]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def print_suite(result: SuiteResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.model_dump(by_alias=True)))
        return
    rows = []
    for s in result.scenarios:
        d = s.meanDecay
        rows.append(
            [
                s.scenarioId,
                s.status,
                "VIOLATED" if s.invariantViolation else "held",
                str(len(s.trials)),
                "${0:.4f}".format(s.totalCostUsd),
                "{0:.3f}".format(d.slope) if d else "-",
                "{0:.2f}".format(d.auc) if d else "-",
                "{0:.2f}".format(d.minQ) if d else "-",
            ]
        )
    print(table(["scenario", "status", "invariants", "trials", "cost", "slope", "auc", "minQ"], rows))
    print("")
    print(
        "{verdict} — {name}@{version} subject={label} total=${total:.4f}".format(
            verdict="verdict: GREEN" if result.green else "verdict: NOT GREEN",
            name=result.evalSet.name,
            version=result.evalSet.version,
            label=result.subject.label,
            total=result.totalCostUsd,
        )
    )
    for d in result.greenDetail:
        print("  - {0}".format(d))


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _parse_subject(spec: str) -> Dict[str, Any]:
    if spec.startswith("scripted:"):
        return {"kind": "scripted", "executor": spec[len("scripted:"):]}
    if spec == "baseline":
        try:
            from convoy_evals.executors import create_baseline_runtime_factory
        except ImportError:
            raise CliError("--subject baseline: convoy_evals.executors has no baseline factory yet")
        return {"kind": "runtime", "factory": create_baseline_runtime_factory({}), "label": "baseline"}
    raise CliError('--subject expects scripted:<executor> or baseline, got "{0}"'.format(spec))


async def cmd_run(args: argparse.Namespace) -> int:
    loaded = load_eval_set(args.set)
    out_dir = args.out if args.out is not None else default_out_dir()
    subject = _parse_subject(args.subject)

    from convoy_evals.runner.suite_runner import run_suite

    scenario_filter = None
    if args.filter:
        scenario_filter = [s.strip() for s in args.filter.split(",") if s.strip()]

    kwargs: Dict[str, Any] = {}
    if args.concurrency is not None:
        kwargs["max_concurrent"] = args.concurrency
    if args.judge is not None:
        kwargs["judge_mode"] = args.judge
    result = await run_suite(
        args.set,
        subject,
        scenario_filter=scenario_filter,
        trials_override=args.trials,
        usd_cap=args.usd_cap,
        out_dir=out_dir,
        **kwargs
    )

    report_path = os.path.join(out_dir, "suite-report.html")
    write_suite_report(result, report_path, item_floor=loaded.config.thresholds.itemFloor)
    print_suite(result, args.json)
    if not args.json:
        print("\nartifacts: {0}\nreport: {1}".format(out_dir, report_path))
    return 0 if result.green else 1


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


async def cmd_replay(args: argparse.Namespace) -> int:
    from convoy_evals.runner.replay import replay_suite

    kwargs: Dict[str, Any] = {}
    if args.judge is not None:
        kwargs["judge_mode"] = args.judge
    result = await replay_suite(args.set, args.records, **kwargs)
    print_suite(result, args.json)
    return 0 if result.green else 1


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _scenario_file_for(scenario_id: str, set_path: Optional[str]):
    if set_path:
        loaded = load_eval_set(set_path)
        p = loaded.scenario_paths.get(scenario_id)
        if p:
            return load_scenario(p)
    # conventional fallback: ./scenarios/<id>.scenario.json
    return load_scenario(os.path.abspath(os.path.join("scenarios", scenario_id + ".scenario.json")))


def cmd_report(args: argparse.Namespace) -> int:
    result_path = os.path.abspath(args.result)
    with open(result_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    try:
        result = SuiteResult.model_validate(raw)
    except Exception as err:
        raise CliError("{0} is not a valid suite-result.json: {1}".format(result_path, err))
    out_dir = os.path.dirname(result_path)
    item_floor = 0.5
    if args.set:
        item_floor = load_eval_set(args.set).config.thresholds.itemFloor

    suite_path = os.path.join(out_dir, "suite-report.html")
    write_suite_report(result, suite_path, item_floor=item_floor)
    print("wrote {0}".format(suite_path))

    if args.rehearsal:
        rehearsal_id = args.rehearsal
        trial_idx = args.trial if args.trial is not None else 0
        records_dir = args.records if args.records else out_dir
        sv = next((s for s in result.scenarios if s.scenarioId == rehearsal_id), None)
        if sv is None:
            raise CliError('scenario "{0}" not present in {1}'.format(rehearsal_id, result_path))
        trial = next((t for t in sv.trials if t.trialIdx == trial_idx), None)
        if trial is None:
            raise CliError(
                'trial {0} of "{1}" not present (have {2} trial(s))'.format(
                    trial_idx, rehearsal_id, len(sv.trials)
                )
            )
        scenario = _scenario_file_for(rehearsal_id, args.set)
        events = read_jsonl_events(os.path.join(records_dir, events_file_name(rehearsal_id, trial_idx)))
        with open(
            os.path.join(records_dir, world_file_name(rehearsal_id, trial_idx)), "r", encoding="utf-8"
        ) as fh:
            world = WorldBundle.from_json(json.load(fh))
        html = render_rehearsal_report(scenario, trial, events, world)
        out_path = os.path.join(out_dir, "rehearsal-{0}-t{1}.html".format(rehearsal_id, trial_idx))
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        print("wrote {0}".format(out_path))
    return 0


# ---------------------------------------------------------------------------
# lint — static completeness per scenario.
# ---------------------------------------------------------------------------


def _collect_key_refs(value: Any, out: List[str]) -> None:
    """Deep-walk arbitrary JSON collecting {$key: "..."} refs."""
    if isinstance(value, list):
        for v in value:
            _collect_key_refs(v, out)
        return
    if isinstance(value, dict):
        if isinstance(value.get("$key"), str) and len(value) == 1:
            out.append(value["$key"])
            return
        for v in value.values():
            _collect_key_refs(v, out)


def _key_ref_resolves(key_dump: Dict[str, Any], key_path: str) -> bool:
    """A KeyRef resolves if its path hits the key root, facts, or a perItem
    subtree. Corpus convention: "item.<field>" refs are item-relative — they
    resolve inside the current item's perItem subtree with the prefix stripped
    (checked here against every perItem entry; a typo'd field matches none)."""
    per_item = list((key_dump.get("perItem") or {}).values())
    if key_path.startswith("item."):
        rest = key_path[len("item."):]
        return any(len(resolve_path(subtree, rest)) > 0 for subtree in per_item)
    if len(resolve_path(key_dump, key_path)) > 0:
        return True
    if len(resolve_path(key_dump.get("facts"), key_path)) > 0:
        return True
    return any(len(resolve_path(subtree, key_path)) > 0 for subtree in per_item)


def _scan_checklist_refs(graders: Any, out: List[str]) -> None:
    if not isinstance(graders, list):
        return
    for g in graders:
        asserts = g.get("asserts") if isinstance(g, dict) else None
        if not isinstance(asserts, list):
            continue
        for a in asserts:
            if (
                isinstance(a, dict)
                and a.get("kind") == "checklist"
                and isinstance(a.get("checklistRef"), str)
            ):
                out.append(a["checklistRef"])


def _lint_scenario(scenario_id: str, loaded: LoadedEvalSet, rows: List[Dict[str, Any]]) -> None:
    def row(check: str, ok: bool, detail: str = "") -> None:
        rows.append({"scenario": scenario_id, "check": check, "ok": ok, "detail": detail})

    path = loaded.scenario_paths.get(scenario_id)
    if path is None:
        row("file", False, "no {0}.scenario.json under {1}".format(scenario_id, loaded.scenarios_dir))
        return
    try:
        scenario = load_scenario(path)
        row("schema", True)
    except Exception as err:
        row("schema", False, str(err).split("\n")[0])
        return

    row(
        "budgets",
        bool(scenario.budgets and scenario.budgets.usd > 0 and scenario.budgets.simTime),
        "",
    )

    key_file = None
    key_path = resolve_answer_key_path(scenario.answerKeyRef.path, path, loaded.scenarios_dir)
    try:
        key_file = load_answer_key_file(key_path)
        row("answer-key", True)
    except Exception as err:
        row("answer-key", False, str(err).split("\n")[0])
    if key_file is None:
        return
    key_dump = key_file.key.model_dump(by_alias=True)

    hash_ok = answer_key_hash_matches(key_file, scenario.answerKeyRef.hash)
    row(
        "key-hash",
        hash_ok,
        ""
        if hash_ok
        else "answerKeyRef.hash {0} != sha256:{1}".format(
            scenario.answerKeyRef.hash, key_file.content_hash
        ),
    )

    # Every KeyRef in graders (+ item graders + template) and key checklists resolves.
    scenario_dump = scenario.model_dump(by_alias=True)
    refs: List[str] = []
    _collect_key_refs(scenario_dump.get("graders"), refs)
    _collect_key_refs(scenario_dump.get("itemGraderTemplate") or [], refs)
    for item in scenario_dump.get("items") or []:
        if item.get("graders") != "inherit":
            _collect_key_refs(item.get("graders"), refs)
    _collect_key_refs(key_dump.get("checklists") or {}, refs)
    unresolved = sorted(
        {r for r in refs if not _key_ref_resolves(key_dump, r)}
    )
    row(
        "key-refs",
        len(unresolved) == 0,
        "unresolved: {0}".format(", ".join(unresolved))
        if unresolved
        else "{0} ref(s)".format(len(refs)),
    )

    # Checklist refs used by graders exist in the key.
    checklist_refs: List[str] = []
    _scan_checklist_refs(scenario_dump.get("graders"), checklist_refs)
    _scan_checklist_refs(scenario_dump.get("itemGraderTemplate") or [], checklist_refs)
    for item in scenario_dump.get("items") or []:
        if item.get("graders") != "inherit":
            _scan_checklist_refs(item.get("graders"), checklist_refs)
    key_checklists = key_dump.get("checklists") or {}
    missing_checklists = sorted({r for r in checklist_refs if r not in key_checklists})
    row(
        "checklists",
        len(missing_checklists) == 0,
        "missing: {0}".format(", ".join(missing_checklists))
        if missing_checklists
        else "{0} ref(s)".format(len(checklist_refs)),
    )

    # Scripted approval steps' gate kinds are valid GateKinds.
    if scenario.approvals.mode == "scripted":
        valid_kinds = set(get_args(GateKind))
        bad_kinds = [
            step.expect.kind
            for step in scenario.approvals.steps
            if step.expect.kind is not None and step.expect.kind not in valid_kinds
        ]
        row(
            "gate-kinds",
            len(bad_kinds) == 0,
            "invalid: {0}".format(", ".join(bad_kinds))
            if bad_kinds
            else "{0} step(s)".format(len(scenario.approvals.steps)),
        )
    else:
        row("gate-kinds", True, "mode={0}".format(scenario.approvals.mode))

    # Gauntlet items: keyRef exists in perItem; 'inherit' requires a template.
    if scenario.items:
        per_item = key_dump.get("perItem") or {}
        bad_items = [i.itemId for i in scenario.items if i.keyRef not in per_item]
        row(
            "item-key-refs",
            len(bad_items) == 0,
            "no perItem entry: {0}".format(", ".join(bad_items))
            if bad_items
            else "{0} item(s)".format(len(scenario.items)),
        )
        inheriting = any(i.graders == "inherit" for i in scenario.items)
        if inheriting:
            has_template = bool(scenario.itemGraderTemplate)
            row(
                "item-grader-template",
                has_template,
                "" if has_template else "items use graders:'inherit' but itemGraderTemplate is missing/empty",
            )


def cmd_lint(args: argparse.Namespace) -> int:
    loaded = load_eval_set(args.set)
    rows: List[Dict[str, Any]] = []
    for sid in loaded.config.scenarios:
        _lint_scenario(sid, loaded, rows)

    print(
        table(
            ["scenario", "check", "result", "detail"],
            [[r["scenario"], r["check"], "ok" if r["ok"] else "PROBLEM", r["detail"]] for r in rows],
        )
    )
    problems = sum(1 for r in rows if not r["ok"])
    print(
        "\n{status} — {name}@{version}, {count} scenario(s)".format(
            status="lint: clean" if problems == 0 else "lint: {0} problem(s)".format(problems),
            name=loaded.config.name,
            version=loaded.config.version,
            count=len(loaded.config.scenarios),
        )
    )
    return 0 if problems == 0 else 1


# ---------------------------------------------------------------------------
# dry-run — a scenario must pass under scripted:golden AND fail under
# scripted:violator-no-gate, or it cannot distinguish the two and is rejected.
# ---------------------------------------------------------------------------


async def cmd_dry_run(args: argparse.Namespace) -> int:
    scenario_filter = [args.scenario] if args.scenario else None
    from convoy_evals.runner.suite_runner import run_suite

    base = os.path.join("results", "dry-run-{0}".format(timestamp_slug()))
    golden = await run_suite(
        args.set,
        {"kind": "scripted", "executor": "golden"},
        scenario_filter=scenario_filter,
        trials_override=1,
        judge_mode="cache-only",
        out_dir=os.path.join(base, "golden"),
    )
    violator = await run_suite(
        args.set,
        {"kind": "scripted", "executor": "violator-no-gate"},
        scenario_filter=scenario_filter,
        trials_override=1,
        judge_mode="cache-only",
        out_dir=os.path.join(base, "violator-no-gate"),
    )

    violator_by_id = {s.scenarioId: s for s in violator.scenarios}
    rejected = 0
    rows = []
    for g in golden.scenarios:
        v = violator_by_id.get(g.scenarioId)
        golden_ok = g.status == "passed" or (g.status == "quarantined" and not g.invariantViolation)
        violator_ok = v is not None and (
            v.status == "failed" or (v.status == "quarantined" and v.invariantViolation)
        )
        ok = golden_ok and violator_ok
        if not ok:
            rejected += 1
        if ok:
            why = "distinguishes golden from violator"
        elif not golden_ok:
            why = "golden did not pass ({0})".format(g.status)
        else:
            why = "violator did not fail ({0})".format(v.status if v else "missing")
        rows.append([g.scenarioId, g.status, v.status if v else "missing", "OK" if ok else "REJECTED", why])
    print(table(["scenario", "golden", "violator-no-gate", "verdict", "detail"], rows))
    print(
        "\ndry-run: {0} (artifacts: {1})".format(
            "all scenarios accepted" if rejected == 0 else "{0} scenario(s) REJECTED".format(rejected),
            base,
        )
    )
    return 0 if rejected == 0 else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convoy-evals", description="Convoy eval-harness CLI"
    )
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="run an eval set against a subject")
    run_p.add_argument("--set", required=True, help="eval-set config path")
    run_p.add_argument("--subject", required=True, help="scripted:<executor> or baseline")
    run_p.add_argument("--filter", help="comma-separated scenario ids")
    run_p.add_argument("--trials", type=int, help="override trials per scenario")
    run_p.add_argument("--usd-cap", type=float, dest="usd_cap", help="cumulative USD cap")
    run_p.add_argument("--out", help="artifact directory (default results/<ts>)")
    run_p.add_argument("--judge", choices=["cache-only", "live"], help="judge mode")
    run_p.add_argument("--concurrency", type=int, help="scenario pool width (default 2)")
    run_p.add_argument("--json", action="store_true", help="machine-readable output")

    replay_p = sub.add_parser("replay", help="regrade recorded artifacts (free CI tier)")
    replay_p.add_argument("--set", required=True)
    replay_p.add_argument("--records", required=True, help="directory of recorded artifacts")
    replay_p.add_argument("--judge", choices=["cache-only", "live"])
    replay_p.add_argument("--json", action="store_true")

    report_p = sub.add_parser("report", help="render HTML reports from a suite-result.json")
    report_p.add_argument("--result", required=True, help="path to suite-result.json")
    report_p.add_argument("--set", help="eval-set config (for the Q(n) item-floor line)")
    report_p.add_argument("--rehearsal", help="scenario id to render a rehearsal report for")
    report_p.add_argument("--trial", type=int, default=0)
    report_p.add_argument("--records", help="records dir for events/world (default: result dir)")

    lint_p = sub.add_parser("lint", help="static completeness checks per scenario")
    lint_p.add_argument("--set", required=True)

    dry_p = sub.add_parser("dry-run", help="authoring gate: golden must pass, violator must fail")
    dry_p.add_argument("--set", required=True)
    dry_p.add_argument("--scenario", help="restrict to one scenario id")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            return asyncio.run(cmd_run(args))
        if args.cmd == "replay":
            return asyncio.run(cmd_replay(args))
        if args.cmd == "report":
            return cmd_report(args)
        if args.cmd == "lint":
            return cmd_lint(args)
        if args.cmd == "dry-run":
            return asyncio.run(cmd_dry_run(args))
        parser.print_help()
        return 0
    except CliError as err:
        print("error: {0}".format(err), file=sys.stderr)
        return 2
    except Exception:
        print("error: {0}".format(traceback.format_exc().strip()), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
