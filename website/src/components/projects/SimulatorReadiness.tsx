import type { Tone } from "@/lib/configurations/status";
import type { Robot } from "@/lib/platform/client";

const labels = {
  requested: "Waiting for runner",
  passed: "Simulator checks passed",
  failed: "Simulator checks failed",
  expired: "Runner did not report in time",
  stale: "Connection changed — verify again",
};

/** A simulated robot's readiness in one or two words, for a badge. */
export function readiness(robot: Robot): { label: string; tone: Tone } {
  if (!robot.profile_id) return { label: "Existing runtime", tone: "neutral" };
  const state = robot.qualification?.state;
  return state === "passed" ? { label: "Verified", tone: "success" }
    : state === "requested" ? { label: "Verifying", tone: "info" }
      : state ? { label: "Not verified", tone: "warning" } : { label: "Not verified", tone: "neutral" };
}

export function SimulatorReadiness({ robot, busy, canWrite, verify }: {
  robot: Robot; busy: boolean; canWrite: boolean; verify: () => void;
}) {
  const result = robot.qualification;
  const supported = robot.simulation_engine === "mujoco";
  return <div className="cv-readiness">
    <span className="cv-readiness__state" role="status">{result ? labels[result.state] : "Simulator not verified"}</span>
    {supported && canWrite && <button className="cv-link" type="button" disabled={busy || result?.state === "requested"} onClick={verify}>
      {result?.state === "requested" ? "Verification requested" : result ? "Verify again" : "Verify simulator"}
    </button>}
    <details className="cv-disclosure"><summary>{result?.report ? "Verification results" : "What is checked?"}</summary>
      <div className="cv-disclosure__body">
        {result?.report && <>
          <p>{result.report.detail}</p>
          {result.state === "passed" && <p>{result.report.evidence.steps} physics steps · {result.report.evidence.sim_seconds.toFixed(3)} simulated seconds · {result.report.evidence.wall_seconds.toFixed(3)} elapsed seconds</p>}
          <p>Requested {new Date(result.created_at).toLocaleString()}{result.completed_at ? ` · reported ${new Date(result.completed_at).toLocaleString()}` : ""}</p>
        </>}
        <p>Checks the imported model, joints, controller and cameras in the simulator. Timing and physical accuracy require separate experiments.</p>
        <p>{supported ? "The runner must be running with this profile’s model installed. A request expires after 10 minutes without a report." : "Isaac verification is not available yet."}</p>
      </div>
    </details>
  </div>;
}
