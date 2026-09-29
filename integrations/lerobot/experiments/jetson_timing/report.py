"""Build an inspectable timing report from completed or failed experiment runs.

Usage: python -m jetson_timing.report --output report.html RUN_DIRECTORY [...]
No interpolation across devices, precision profiles, or unmeasured delays.
"""

import argparse
import html
import json
from pathlib import Path


def summarize(root):
    manifest = json.loads((root / "manifest.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    config = manifest["config"]
    episodes = summary.get("episodes", [])
    timings = [e["timing"] for e in episodes if "timing" in e]
    status = "runtime_error" if summary["status"] == "error" else "not_qualified"
    if timings:
        statuses = [t["status"] for t in timings]
        status = ("failed" if "failed" in statuses else "passed_observed_contract"
                  if all(s == "passed_observed_contract" for s in statuses)
                  and len(episodes) == summary["requested_episodes"] else "insufficient_evidence")
    if summary["status"] != "completed":
        status = "runtime_error" if summary["status"] == "error" else "incomplete"
    elif config["mode"] != "realtime":
        status = "offline_reference" if config["mode"] == "lockstep" else "calibration"
    realtime = config["mode"] == "realtime"
    stage_names = ("camera_s", "inference_s", "observation_to_result_s", "observation_to_action_s",
                   "tick_dispatch_lag_s", "tick_completion_lag_s", "remaining_chunk_budget_s")
    stages = {}
    for name in stage_names:
        values = [t["stages"][name]["p99"] for t in timings if t["stages"][name]["p99"] is not None]
        stages[name] = max(values) if values else None
        if not realtime and name in {"tick_dispatch_lag_s", "tick_completion_lag_s", "remaining_chunk_budget_s"}:
            stages[name] = None
    return {"run": root.name, "status": status, "profile": manifest["profile"], "config": config,
            "overall_qualified": status == "passed_observed_contract" and summary.get("task_success_rate") == 1.0,
            "image": manifest["container_image"], "packages": manifest["packages"],
            "render_backend": manifest.get("render_backend"),
            "completed_episodes": len(episodes), "requested_episodes": summary["requested_episodes"],
            "episode_outcomes": [{k: e.get(k) for k in ("seed", "status", "steps", "wall_s", "simulated_s")}
                                 for e in episodes],
            "task_success_rate": summary.get("task_success_rate"),
            "policy_results": sum(e.get("policy_results", 0) for e in episodes),
            "stale_results": sum(e.get("stale_results", 0) for e in episodes),
            "dropped_results": sum(e.get("dropped_results", 0) for e in episodes),
            "worst_episode_p99_s": stages,
            "steady_fallback_fraction": max((t["steady_fallback_fraction"] for t in timings
                                               if realtime and t["steady_fallback_fraction"] is not None), default=None),
            "overall_fallback_fraction": max((t["fallback_fraction"] for t in timings
                if config["mode"] == "realtime" and t["fallback_fraction"] is not None), default=None),
            "estimated_actions_for_continuity": max((t["estimated_actions_for_continuity"] for t in timings
                if realtime and t["estimated_actions_for_continuity"] is not None), default=None),
            "reasons": sorted({r for t in timings for r in t["reasons"]}), "error": summary.get("error")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = [summarize(p) for p in args.runs]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps({"schema_version": 1, "runs": runs}, indent=2) + "\n")
    esc = lambda value: html.escape(str(value))  # noqa: E731
    ms = lambda value: "—" if value is None else f"{value * 1000:.1f} ms"  # noqa: E731
    cards = []
    for r in runs:
        c, s = r["config"], r["worst_episode_p99_s"]
        fallback = r["steady_fallback_fraction"]
        overall_fallback = r["overall_fallback_fraction"]
        success = r["task_success_rate"]
        cards.append(f'''<article><h2>{esc(r['run'])}</h2><strong class="{esc(r['status'])}">{esc(r['status'].replace('_', ' '))}</strong>
<p>{esc(c['policy'])} · {esc(c.get('precision', 'float32'))} · camera {esc(r['render_backend'])} · delay {esc(c['policy_delay_ms'])} ms · drop every {esc(c['drop_every']) or '0'} results</p>
<p>Camera {esc(c.get('camera_size', 480))} px · model image {esc(c.get('vision_size', 512))} px · {esc(c.get('denoise_steps', 10))} policy refinement steps</p>
<p>Task + timing: <b>{'met the observed contract' if r['overall_qualified'] else 'not qualified'}</b></p>
<table><tr><th>Completed episodes</th><td>{r['completed_episodes']} / {r['requested_episodes']}</td></tr>
<tr><th>Task success</th><td>{'—' if success is None else f'{success:.0%}'}</td></tr>
<tr><th>Camera p99</th><td>{ms(s['camera_s'])}</td></tr>
<tr><th>Inference p99</th><td>{ms(s['inference_s'])}</td></tr>
<tr><th>Observation → result p99</th><td>{ms(s['observation_to_result_s'])}</td></tr>
<tr><th>Observation → action p99</th><td>{ms(s['observation_to_action_s'])}</td></tr>
<tr><th>Physics dispatch lateness p99</th><td>{ms(s['tick_dispatch_lag_s'])}</td></tr>
<tr><th>Physics completion lateness p99</th><td>{ms(s['tick_completion_lag_s'])}</td></tr>
<tr><th>Overall fallback use (worst episode)</th><td>{'—' if overall_fallback is None else f'{overall_fallback:.1%}'}</td></tr>
<tr><th>Steady fallback use (worst episode)</th><td>{'—' if fallback is None else f'{fallback:.1%}'}</td></tr>
<tr><th>Policy results / stale / dropped</th><td>{r['policy_results']} / {r['stale_results']} / {r['dropped_results']}</td></tr>
<tr><th>Configured action horizon</th><td>{str(c['chunk_size']) + ' actions / ' + str(c['chunk_size'] * 12.5) + ' ms' if c['mode'] == 'realtime' else 'Offline: one fresh action per inference'}</td></tr>
<tr><th>Maximum observation age</th><td>{ms(c['max_age']) if c['mode'] == 'realtime' else 'Not a timing trial'}</td></tr>
<tr><th>Estimated actions for continuous supply</th><td>{r['estimated_actions_for_continuity'] or '—'}</td></tr></table>
<p>Episode outcomes: {esc('; '.join(str(e['seed']) + ': ' + str(e['status']) + ', ' + str(e['steps']) + ' steps' for e in r['episode_outcomes']))}</p>
<p>{esc(', '.join(r['reasons']) or r['error'] or 'See the saved contract and sample counts in the JSON report.')}</p></article>''')
    args.output.write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Convoy · Timing qualification</title><style>body{font:16px system-ui;background:#f5f6f8;color:#17202b;margin:0;padding:36px;max-width:1200px}h1{font-size:32px}p{line-height:1.6;color:#485568}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}article{background:white;border:1px solid #dde2e8;border-radius:12px;padding:24px}h2{font-size:19px;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px 0;border-bottom:1px solid #edf0f4}th{font-weight:500}td{text-align:right}.failed,.runtime_error{color:#b32828}.passed_observed_contract{color:#157347}strong{display:block;margin:12px 0}</style>
<h1>Convoy timing qualification</h1><p>Measured on the same host: physics → camera → policy → action playback. Task success and timing qualification are separate. Each p99 below is the worst episode p99, not a pooled percentile. No cloud-planner latency is represented. Scripted trials use privileged simulator state; SmolVLA trials execute the learned checkpoint.</p>
<p>The buffer estimate is p99 observation-to-result latency plus p99 result interval plus one 12.5 ms tick. It is a planning estimate, not a guarantee or permission to extend a model's trained action horizon. Short or incomplete runs cannot claim qualification.</p><div class="grid">''' + ''.join(cards) + '</div></html>')


if __name__ == "__main__":
    main()
