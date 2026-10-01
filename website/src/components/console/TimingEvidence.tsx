import evidence from "./jetson-timing-evidence.json";

const labels: Record<string, string> = {
  "scripted-camera-v2": "Scripted controller · wall-clock playback",
  "learned-reference-bf16-osmesa-v2": "SmolVLA · offline reference",
  "delay-0": "SmolVLA · wall-clock playback",
  "learned-fast-reference-v2": "SmolVLA · lighter offline configuration",
};

export function TimingEvidence() {
  return <details className="console-card">
    <summary>Jetson timing experiment · recorded results</summary>
    <h2>Does the policy deliver actions in time?</h2>
    <p>Measured on the Orin Nano on {evidence.recorded}. These are saved experimental results, separate from the Mac simulation runner and current device telemetry.</p>
    <p><strong>The learned policy did not meet the real-time contract.</strong> The wall-clock experiment used 80 Hz physics, a 625 ms action-validity horizon, and a maximum 5% steady-state fallback target. Physics, rendering and inference ran on the same Jetson.</p>
    {evidence.runs.map(run => <section key={run.run} className="console-card">
      <h3>{labels[run.run]}</h3>
      <p>Task successes: {Math.round(run.task_success_rate * run.completed_episodes)} / {run.completed_episodes} episodes. {run.overall_qualified ? "Met this experiment’s observed scripted-control contract." : run.status === "offline_reference" ? "Offline reference; physics waited for inference." : "Did not qualify for real-time control."}</p>
      <dl>
        <dt>Observation to result · worst episode p99</dt><dd>{(run.worst_episode_p99_s.observation_to_result_s * 1000).toFixed(1)} ms</dd>
        <dt>Policy results / stale results</dt><dd>{run.policy_results} / {run.stale_results}</dd>
        <dt>Overall fallback</dt><dd>{run.overall_fallback_fraction == null ? "Not applicable" : `${(run.overall_fallback_fraction * 100).toFixed(0)}%`}</dd>
      </dl>
      {run.episode_outcomes.map(episode => <p key={episode.seed} className="console-note">Seed {episode.seed}: {episode.steps} steps, {episode.wall_s.toFixed(2)} seconds elapsed / {episode.simulated_s.toFixed(2)} simulated seconds.</p>)}
    </section>)}
    <p className="console-note">The scripted controller reads simulator state; its success does not qualify the learned policy. The wall-clock learned trial returned only three policy chunks per episode, so these percentiles are small-sample observations. No cloud planner or wireless path was measured, and this report is not a deployment gate.</p>
    <details><summary>Evidence identity</summary><pre className="console-code">{JSON.stringify({ source_sha256: evidence.source_sha256, image: evidence.runs[0].image }, null, 2)}</pre></details>
  </details>;
}
