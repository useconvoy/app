"use client";

import { Facts } from "@/components/configurations/Tiles";

const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const measured = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "Not reported";
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? (Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(2)) : "Not reported";
const labels: Record<string, string> = { observed_deadlines_met: "Observed deadlines met", insufficient_evidence: "Insufficient timing evidence", failed: "Timing requirements not met" };
const reasons: Record<string, string> = {
  physics_dispatch_lag: "Physics started a tick too late", physics_completion_lag: "Physics completed a tick too late",
  policy_deadline_missed: "A policy action missed its deadline", policy_actions_expired_between_results: "The simulator needed position hold between fresh actions",
  policy_response_unavailable: "A policy response did not arrive successfully",
};

/** A task's timing evidence, separate from its success: verdict, counts, limits and latencies (joint-state simulation on one host). */
export function TimingResult({ value }: { value: unknown }) {
  if (!value) return null;
  const timing = object(value);
  const contract = object(timing.contract);
  const why = Array.isArray(timing.reasons) ? timing.reasons.filter((reason): reason is string => typeof reason === "string") : [];
  return <section className="cv-subsection" aria-label="Task timing">
    <h3>{typeof timing.status === "string" ? labels[timing.status] ?? "Timing status unavailable" : "Timing status unavailable"}</h3>
    <p className="cv-muted">Measured in joint-state simulation on one host; separate from task success.{why.length ? ` ${why.map(reason => reasons[reason] ?? "Timing condition was not met").join(". ")}.` : ""}</p>
    <Facts items={[
      { label: "Physics ticks", value: count(timing.physics_control_steps) },
      { label: "Applied actions", value: count(timing.applied_actions) },
      { label: "Position-hold ticks", value: count(timing.fallback_ticks) },
      { label: "Physics time", value: `${measured(timing.physics_wall_s)} s` },
      { label: "Observation age limit", value: `${measured(contract.max_observation_age_ms)} ms` },
      { label: "Physics lag limit", value: `${measured(contract.max_physics_lag_ms)} ms` },
    ]} />
    {timing.status === "insufficient_evidence" && <p className="cv-muted">At least {count(contract.minimum_physics_steps)} physics ticks and {count(contract.minimum_applied_actions)} applied actions are needed to assess the timing contract.</p>}
    <div className="cv-table-wrap"><table className="cv-table"><caption className="cv-sr">Timing in milliseconds</caption><thead><tr><th scope="col">Measurement (ms)</th><th scope="col" className="cv-num">Median</th><th scope="col" className="cv-num">95th percentile</th><th scope="col" className="cv-num">Maximum</th></tr></thead><tbody>
      {[["policy_wait_ms", "Policy wait, including transport"], ["observation_to_result_ms", "Observation to result"], ["observation_to_action_ms", "Observation to applied action"], ["dispatch_lag_ms", "Physics dispatch lag"], ["completion_lag_ms", "Physics completion lag"]].map(([key, label]) => {
        const sample = object(timing[key]);
        return <tr key={key}><th scope="row">{label}</th><td className="cv-num">{measured(sample.p50)}</td><td className="cv-num">{measured(sample.p95)}</td><td className="cv-num">{measured(sample.max)}</td></tr>;
      })}
    </tbody></table></div>
  </section>;
}
