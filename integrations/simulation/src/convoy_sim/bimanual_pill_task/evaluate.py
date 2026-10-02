"""Seeds x slices x configurations, run in parallel, aggregated to JSON and Markdown.

Episodes are written to ``OUTPUT/<config>/<seed>-<slice>/``. With ``replay="offline"``
every episode is recorded in the offline replay format and each configuration
directory gets an ``evaluation.json``, so ``scripts/import_offline_eval.py
OUTPUT/<config>`` imports one offline evaluation per configuration. With
``seed_stride`` the i-th slice runs seeds ``i * seed_stride + seed``, which keeps
every episode's seed distinct within a configuration (the platform groups an
offline evaluation's episodes by seed).

A configuration whose planner is the model on a connected device (the edge
device planner) needs that connection (``planner``). Its episodes run one after
another in this process, so the device only ever has one request; the device is
checked before every episode, and the evaluation stops (and says so) when it goes
offline or its active model is no longer the one the run started on. The model
and transport the run used are in ``manifest.json`` and the offline evaluation's
labels. Every call is written to ``planner_calls.jsonl`` in its episode's
directory and to ``OUTPUT/<config>/calls.jsonl``.
"""

from __future__ import annotations

import json
import multiprocessing
import platform
import subprocess
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np

from . import physics as P
from .configs import CONFIGS, DEFAULT_HORIZON_S, SLICES, EpisodeSpec, release_manifest


def _source_revision() -> dict:
    try:
        root = Path(__file__).parent
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL,
                                      timeout=5).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True,
                                        stderr=subprocess.DEVNULL, timeout=5).strip()
        return {"commit": sha, "dirty": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def episode_dir(output: Path, config: str, slice_id: str, seed: int) -> Path:
    return output / config / f"{seed:04d}-{slice_id}"


def make_recorder(job: dict, out: Path):
    """The recorder a job asks for: the hosted journal format, the offline replay format, or none."""
    if not job.get("record"):
        return None
    if job.get("replay", "journal") == "offline":
        from .offline_replay import OfflineReplayRecorder

        return OfflineReplayRecorder(out, seed=int(job["seed"]), preview_camera=job.get("preview_camera"))
    from .recording import JournalRecorder

    return JournalRecorder(out, release_manifest=release_manifest(CONFIGS[job["config"]]),
                           preview_camera=job.get("preview_camera"))


def run_job(job: dict, planner=None, planner_log=None) -> dict:
    """Run one episode (in a worker process, or in this one with a device `planner`); writes summary.json,
    the device planner's calls (planner_calls.jsonl) and, if asked, the recording."""
    if job.get("timestep"):
        P.TIMESTEP_S = float(job["timestep"])
    from .episode import Episode

    config = CONFIGS[job["config"]]
    spec = EpisodeSpec(config, SLICES[job["slice"]], int(job["seed"]), float(job["horizon"]))
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=False)
    recorder = make_recorder(job, out)
    episode = None
    try:
        episode = Episode(spec, recorder, planner, planner_log)
        summary = episode.run()
        summary["status"] = "completed"
    except Exception as error:  # keep the denominator honest: errors are results
        summary = {"config": job["config"], "slice": job["slice"], "seed": job["seed"], "status": "error",
                   "error": f"{type(error).__name__}: {error}"[:1000], "success": False, "fraction_placed": 0.0,
                   "placed": 0, "pills": SLICES[job["slice"]].pills}
    records = getattr(getattr(episode, "skill_planner", None), "records", None)
    if records is not None and getattr(episode, "device", False):
        with (out / "planner_calls.jsonl").open("w") as handle:
            for record in records:
                handle.write(json.dumps({"config": job["config"], "slice": job["slice"], "seed": int(job["seed"]),
                                         **record}, ensure_ascii=False) + "\n")
    if not (out / "summary.json").exists():  # the offline recorder writes it with its replay details
        (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    return summary


def _device_problem(planner, attempts: int = 3, wait_s: float = 10.0) -> str | None:
    """Why the device cannot take the next episode (None when it is online, eligible and still runs the
    release the run started on). A status that cannot be read is asked again a few times before it counts
    as a problem."""
    error = ""
    for attempt in range(attempts):
        try:
            state = planner.device()
        except Exception as failure:  # noqa: BLE001 - reported by class; a refused session stops at once
            error = type(failure).__name__
            if error in ("SessionEnded", "AccessRefused"):
                return "the session was refused" if error == "SessionEnded" else "the account was refused (403)"
            if attempt + 1 < attempts:
                time.sleep(wait_s)
            continue
        pinned = getattr(planner, "release_id", None)
        if state.online and state.eligible and pinned and state.release_id != pinned:
            return f"device model changed at {state.checked_at}: release {state.release_id}, the run started on {pinned}"
        if state.online and state.eligible:
            return None
        return f"device {'offline' if not state.online else 'not eligible'} at {state.checked_at}" + (
            f": {state.reason}" if state.reason else "")
    return f"device status unreadable ({error})"


def _run_on_device(work: list[dict], planner, planner_log=None) -> list[dict]:
    """The device planner's episodes, one after another (one request at a time on the device). The device
    is checked before each episode; once it is offline, or an episode stops on the device (offline, model
    changed, session ended), the remaining episodes are not run and are reported as such."""
    summaries: list[dict] = []
    stopped: str | None = None
    for job in work:
        if stopped is None:
            stopped = _device_problem(planner)
        if stopped is not None:
            summary = {"config": job["config"], "slice": job["slice"], "seed": job["seed"], "status": "not_run",
                       "reason": stopped, "success": False, "fraction_placed": 0.0, "placed": 0,
                       "pills": SLICES[job["slice"]].pills}
            summaries.append(summary)
            print(f"[not run] {job['config']} {job['slice']} seed {job['seed']}: {stopped}", flush=True)
            continue
        summary = run_job(job, planner, planner_log)
        summaries.append(summary)
        outcome = str(summary.get("outcome", ""))
        if outcome.startswith("planner_stopped:") and not outcome.endswith("planner_call_budget_exhausted"):
            stopped = outcome.split(":", 1)[1]
        print(f"[{len(summaries)}/{len(work)}] {summary['config']:26s} {summary['slice']:17s} seed {summary['seed']}: "
              f"{summary.get('placed')}/{summary.get('pills')} placed, {summary.get('outcome', summary.get('error'))}",
              flush=True)
    return summaries


def task_label(slices: list[str], seeds: dict[str, list[int]]) -> str:
    """The offline evaluation's task label: the instruction and the slices with their seeds (<= 120 chars)."""
    def span(values: list[int]) -> str:
        return str(values[0]) if len(values) == 1 else f"{values[0]}–{values[-1]}"

    parts = ", ".join(f"{SLICES[s].short or s} (seeds {span(seeds[s])})" for s in slices)
    label = f"Pills to bottle · {parts}"
    return label if len(label) <= 120 else "Put all the pills in the bottle"


def aggregate(summaries: list[dict]) -> dict:
    table: dict[str, dict[str, dict]] = {}
    for s in summaries:
        if s.get("status") == "not_run":  # never started (device offline): not in any denominator
            continue
        table.setdefault(s["config"], {}).setdefault(s["slice"], []).append(s)
    out = {}
    for config, slices in table.items():
        rows = {}
        everything = []
        for slice_id, items in slices.items():
            everything += items
            rows[slice_id] = _cell(items)
        out[config] = {"overall": _cell(everything), "slices": rows}
    return out


def _cell(items: list[dict]) -> dict:
    done = [s for s in items if s.get("status") == "completed"]
    times = [s["time_to_all_placed_s"] for s in done if s.get("time_to_all_placed_s") is not None]
    p50 = [s["planner_latency_p50_ms"] for s in done if s.get("planner_latency_p50_ms") is not None]
    return {
        "episodes": len(items),
        "errors": len(items) - len(done),
        "successes": sum(bool(s.get("success")) for s in items),
        "success_rate": round(sum(bool(s.get("success")) for s in items) / len(items), 4) if items else None,
        "mean_fraction_placed": round(float(np.mean([s.get("fraction_placed", 0.0) for s in items])), 4) if items else None,
        "median_time_to_all_placed_s": round(float(np.median(times)), 2) if times else None,
        "median_episode_s": round(float(np.median([s["simulated_duration_s"] for s in done])), 2) if done else None,
        "median_planner_calls": float(np.median([s["planner_calls"] for s in done])) if done else None,
        "median_planner_latency_p50_ms": round(float(np.median(p50)), 1) if p50 else None,
        "operator_pages": int(sum(s.get("operator_pages", 0) for s in done)),
        "max_robot_env_contact_force_n": max((s.get("max_robot_env_contact_force_n", 0.0) for s in done), default=None),
        "protective_stops": int(sum(s.get("protective_stops", 0) for s in done)),
        "arm_arm_contacts": int(sum(s.get("arm_arm_contacts", 0) for s in done)),
        "max_bottle_tilt_deg": max((s.get("max_bottle_tilt_deg", 0.0) for s in done), default=None),
        "lost_off_table": int(sum(s.get("lost_off_table", 0) for s in done)),
        "rejected_decisions": int(sum(s.get("rejected_decisions", 0) for s in done)),
        "unstable_episodes": sum(s.get("outcome") == "simulation_unstable" for s in done),
        "outcomes": {o: sum(s.get("outcome") == o for s in done) for o in sorted({s.get("outcome") for s in done})},
    }


def markdown(results: dict, slices: list[str]) -> str:
    lines = ["| Configuration | " + " | ".join(slices) + " | All slices |", "|---" * (len(slices) + 2) + "|"]
    for config, data in results.items():
        cells = []
        for slice_id in slices:
            c = data["slices"].get(slice_id)
            cells.append("-" if c is None else f"{c['successes']}/{c['episodes']} · {c['mean_fraction_placed'] * 100:.0f}%")
        o = data["overall"]
        cells.append(f"**{o['successes']}/{o['episodes']} · {o['mean_fraction_placed'] * 100:.0f}%**")
        lines.append(f"| {CONFIGS[config].label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Cells: episodes with every pill in the bottle / episodes · mean fraction of pills placed.")
    lines.append("")
    lines.append("| Configuration | Median time to all placed (s) | Median planner calls | Planner p50 (ms) | Operator pages |")
    lines.append("|---|---|---|---|---|")
    for config, data in results.items():
        o = data["overall"]
        lines.append(f"| {CONFIGS[config].label} | {o['median_time_to_all_placed_s'] or '-'} | {o['median_planner_calls']} | "
                     f"{o['median_planner_latency_p50_ms'] or '-'} | {o['operator_pages']} |")
    lines.append("")
    lines.append("| Configuration | Arm-arm contacts | Protective stops | Peak robot-environment force (N) | "
                 "Peak bottle tilt (deg) | Pills lost off table | Stale decisions rejected | Unstable episodes |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for config, data in results.items():
        o = data["overall"]
        lines.append(f"| {CONFIGS[config].label} | {o['arm_arm_contacts']} | {o['protective_stops']} | "
                     f"{o['max_robot_env_contact_force_n']} | {o['max_bottle_tilt_deg']} | {o['lost_off_table']} | "
                     f"{o['rejected_decisions']} | {o['unstable_episodes']} |")
    return "\n".join(lines) + "\n"


def evaluate(output: Path, configs: list[str], slices: list[str], seeds: list[int], *, jobs: int = 4,
             horizon: float = DEFAULT_HORIZON_S, record: set[tuple[str, str, int]] | str | None = None,
             timestep: float | None = None, preview_camera: str | None = None, replay: str = "journal",
             seed_stride: int = 0, previews: set[tuple[str, str, int]] | None = None, planner=None,
             planner_log=None, name: str | None = None) -> dict:
    """Run every (config, slice, seed). `record` is "all" or the (config, slice, seed) triples to
    record, and `previews` the recorded ones that also render `preview_camera` for a preview
    video (all recorded ones when None). Seeds in triples are the episodes' own (after the stride).
    `planner` is the device connection a device-planner configuration needs (run in this process,
    one episode at a time); `name` overrides the offline evaluation's name."""
    on_device = [c for c in configs if CONFIGS[c].skill_planner.source == "device"]
    if on_device and planner is None:
        raise ValueError(f"{', '.join(on_device)} call the model on a connected device: pass a device planner "
                         "connection (there is no stand-in for it)")
    output.mkdir(parents=True, exist_ok=False)
    by_slice = {s: [i * seed_stride + n for n in seeds] for i, s in enumerate(slices)}
    triples = [(c, s, n) for c in configs for s in slices for n in by_slice[s]]
    record = set(triples) if record == "all" else (record or set())
    manifest = {
        "schema_version": 2, "task": "pills_to_bottle", "configs": configs, "slices": slices, "seeds": seeds,
        "seed_stride": seed_stride, "seeds_by_slice": by_slice, "replay_format": replay if record else None,
        "horizon_s": horizon, "timestep_s": timestep or P.TIMESTEP_S,
        "slice_definitions": {s: SLICES[s].description for s in slices},
        "config_definitions": {c: CONFIGS[c].description for c in configs},
        "releases": {c: release_manifest(CONFIGS[c]) for c in configs},
        "packages": {name: version(name) for name in ("mujoco", "numpy")},
        "python": platform.python_version(), "platform": platform.platform(), "source": _source_revision(),
        "evidence_scope": ("simulated physics; skills read simulator state; planner decisions are a deterministic "
                           "stand-in, configurations differ only by modeled planner latency and network availability"),
    }
    if on_device:
        from .device_planner import settings

        state = planner.device()
        manifest["evidence_scope"] = (
            "simulated physics; skills read simulator state; " + ", ".join(on_device) + ": every skill decision is a "
            "real call to the model on the connected device (text scene in, JSON action out), measured end to end; "
            "other configurations: deterministic stand-in with modeled latency")
        # The model is what the platform reported when the device was first ready (pinned for the run).
        model = getattr(planner, "model", None) or getattr(state, "model", None)
        manifest["device_planner"] = {
            **settings(), "transport": getattr(planner, "transport", "unknown"), "model": model,
            "device_at_start": {k: getattr(state, k, None) for k in (
                "device_id", "device_name", "online", "eligible", "reason", "release_id", "status", "max_tokens",
                "context_window", "checked_at")}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    work = [{"config": c, "slice": s, "seed": n, "horizon": horizon, "timestep": timestep,
             "record": (c, s, n) in record, "replay": replay,
             "preview_camera": preview_camera if previews is None or (c, s, n) in previews else None,
             "output": str(episode_dir(output, c, s, n))}
            for c, s, n in triples]
    if replay == "offline" and record:
        from .offline_replay import write_evaluation

        for c in configs:
            measured = ({"model": manifest["device_planner"]["model"], "transport": manifest["device_planner"]["transport"]}
                        if c in on_device else None)
            write_evaluation(output / c, CONFIGS[c], task_label(slices, by_slice), name=name, planner=measured)
    started = time.time()
    summaries = []
    if on_device:
        summaries = _run_on_device(work, planner, planner_log)
        for c in on_device:  # every call of the configuration, in order (prompts, replies, timings; no credentials)
            with (output / c / "calls.jsonl").open("w") as handle:
                for job in work:
                    calls = Path(job["output"]) / "planner_calls.jsonl"
                    if job["config"] == c and calls.exists():
                        handle.write(calls.read_text())
        late = getattr(planner, "late_results", None)
        if late:
            (output / "late_results.json").write_text(json.dumps(late, indent=2) + "\n")
    else:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=max(1, jobs)) as pool:
            for summary in pool.imap_unordered(run_job, work):
                summaries.append(summary)
                print(f"[{len(summaries)}/{len(work)}] {summary['config']:26s} {summary['slice']:17s} seed "
                      f"{summary['seed']}: {summary.get('placed')}/{summary.get('pills')} placed, "
                      f"{summary.get('outcome', summary.get('error'))}", flush=True)
    results = aggregate(summaries)
    report = {"manifest": "manifest.json", "wall_s": round(time.time() - started, 1), "results": results,
              "not_run": [s for s in summaries if s.get("status") == "not_run"],
              "episodes": sorted(summaries, key=lambda s: (s["config"], s["slice"], s["seed"]))}
    (output / "results.json").write_text(json.dumps(report, indent=2, default=float) + "\n")
    (output / "results.md").write_text(markdown(results, slices))
    return report
