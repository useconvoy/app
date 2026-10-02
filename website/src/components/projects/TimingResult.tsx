"use client";

const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const measured = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "Not reported";
const labels: Record<string, string> = { observed_deadlines_met: "Observed deadlines met", insufficient_evidence: "Insufficient timing evidence", failed: "Timing requirements not met" };
const reasons: Record<string, string> = {
  physics_dispatch_lag: "Physics started a tick too late", physics_completion_lag: "Physics completed a tick too late",
  policy_deadline_missed: "A policy action missed its deadline", policy_actions_expired_between_results: "The simulator needed position hold between fresh actions",
  policy_response_unavailable: "A policy response did not arrive successfully",
};

export function TimingResult({ value }: { value: unknown }) {
  if (!value) return null;
  const timing = object(value);
  const contract = object(timing.contract);
  return <section aria-label="Task timing">
    <h3>{typeof timing.status === "string" ? labels[timing.status] ?? "Timing status unavailable" : "Timing status unavailable"}</h3>
    <p>Task success and timing evidence are separate. These measurements cover joint-state simulation on one host.</p>
    {Array.isArray(timing.reasons) && timing.reasons.filter((r): r is string => typeof r === "string").map(reason => <p key={reason}>{reasons[reason] ?? "Timing condition was not met"}</p>)}
    <p>Physics ticks: {measured(timing.physics_control_steps)} · Applied policy actions: {measured(timing.applied_actions)} · Position-hold ticks: {measured(timing.fallback_ticks)}</p>
    <p>Observation age limit: {measured(contract.max_observation_age_ms)} ms · Physics lag limit: {measured(contract.max_physics_lag_ms)} ms</p>
    {timing.status === "insufficient_evidence" && <p>At least {measured(contract.minimum_physics_steps)} physics ticks and {measured(contract.minimum_applied_actions)} applied actions are required to assess the observed timing contract.</p>}
    <div className="cv-table-wrap"><table className="cv-table"><caption>Timing in milliseconds</caption><thead><tr><th scope="col">Measurement</th><th scope="col">Median</th><th scope="col">95th percentile</th><th scope="col">Maximum</th></tr></thead><tbody>
      {[["policy_wait_ms", "Policy wait, including transport"], ["observation_to_result_ms", "Observation to result"], ["observation_to_action_ms", "Observation to applied action"], ["dispatch_lag_ms", "Physics dispatch lag"], ["completion_lag_ms", "Physics completion lag"]].map(([key, label]) => {
        const sample = object(timing[key]);
        return <tr key={key}><th scope="row">{label}</th><td>{measured(sample.p50)}</td><td>{measured(sample.p95)}</td><td>{measured(sample.max)}</td></tr>;
      })}
    </tbody></table></div>
    <p>Physics elapsed time: {measured(timing.physics_wall_s)} seconds. A short successful task does not establish a timing guarantee.</p>
  </section>;
}
