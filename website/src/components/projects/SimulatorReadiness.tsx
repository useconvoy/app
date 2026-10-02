import type { Robot } from "@/lib/platform/client";

const labels = {
  requested: "Waiting for runner",
  passed: "Simulator checks passed",
  failed: "Simulator checks failed",
  expired: "Runner did not report in time",
  stale: "Connection changed — verify again",
};

export function SimulatorReadiness({ robot, busy, canWrite, verify }: {
  robot: Robot; busy: boolean; canWrite: boolean; verify: () => void;
}) {
  const result = robot.qualification;
  const supported = robot.simulation_engine === "mujoco";
  return <div>
    <p role="status">{result ? labels[result.state] : "Simulator not verified"}</p>
    {supported && canWrite && <button className="cv-link" disabled={busy || result?.state === "requested"} onClick={verify}>
      {result?.state === "requested" ? "Verification requested" : result ? "Verify again" : "Verify simulator"}
    </button>}
    <details><summary>{result?.report ? "Verification results" : "What is checked?"}</summary>
      <p>Checks the imported model, joints, controller, and cameras in the simulator. Timing and physical accuracy require separate experiments.</p>
      {result?.report && <>
        <p>{result.report.detail}</p>
        {result.state === "passed" && <p>{result.report.evidence.steps} physics steps · {result.report.evidence.sim_seconds.toFixed(3)} simulated seconds · {result.report.evidence.wall_seconds.toFixed(3)} elapsed seconds</p>}
        <p>Requested: {new Date(result.created_at).toLocaleString()}</p>
        {result.completed_at && <p>Reported: {new Date(result.completed_at).toLocaleString()}</p>}
      </>}
      <p>{supported
        ? "The enrolled simulator runner must be running with this profile’s model asset installed. A request expires after 10 minutes without a report."
        : "Isaac verification is not available yet. This profile’s asset remains unverified."}</p>
    </details>
  </div>;
}
