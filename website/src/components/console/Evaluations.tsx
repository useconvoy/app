"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorText, MutationAttempts, terminal, timestamp } from "@/lib/platform/client";
import type { Application, EvaluationComparison, EvaluationRun, EvaluationSuite, Qualification, Release, Robot } from "@/lib/platform/client";

interface Props {
  application: Application;
  release: Release;
  robot?: Robot;
  runs: EvaluationRun[];
  qualification: Qualification | null;
  onQualification: (value: Qualification | null) => void;
  snapshot: string | null;
  dispatchAvailable: boolean;
  hasActiveMission: boolean;
  writable: boolean;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onEpisode: (id: string) => Promise<void>;
}

function parseSeeds(value: FormDataEntryValue | null): number[] {
  const tokens = String(value ?? "").trim().split(/[,\s]+/);
  if (!tokens.length || tokens.length > 20 || tokens.some(token => !/^\d+$/.test(token))) {
    throw new Error("Enter 1–20 whole-number seeds, separated by commas or spaces.");
  }
  const seeds = tokens.map(Number);
  if (seeds.some(seed => !Number.isSafeInteger(seed) || seed > 4294967295) || new Set(seeds).size !== seeds.length) {
    throw new Error("Seeds must be distinct integers from 0 through 4294967295.");
  }
  return seeds;
}

export function Evaluations({ application, release, robot, runs, qualification, onQualification, snapshot,
  dispatchAvailable, hasActiveMission, writable, busy, run, onEpisode }: Props) {
  const [suites, setSuites] = useState<EvaluationSuite[]>([]);
  const [suiteId, setSuiteId] = useState("");
  const [runId, setRunId] = useState("");
  const [baselineId, setBaselineId] = useState("");
  const [comparison, setComparison] = useState<EvaluationComparison | null>(null);
  const [extraReservation, setExtraReservation] = useState<EvaluationRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const attempts = useRef(new MutationAttempts());
  const qualificationPath = `applications/${application.id}/qualification?release_id=${release.id}`;
  const suite = suites.find(item => item.id === suiteId) ?? suites.find(item => item.id === qualification?.gate?.suite_id) ?? suites[0];
  const candidateRuns = runs.filter(item => item.release_id === release.id && item.suite_id === suite?.id);
  const selectedRun = candidateRuns.find(item => item.id === runId) ?? candidateRuns[0];
  const baselines = runs.filter(item => item.suite_id === selectedRun?.suite_id && item.report && item.id !== selectedRun.id);
  const reservation = runs.find(item => item.id === robot?.evaluation_id)
    ?? (extraReservation?.id === robot?.evaluation_id ? extraReservation : null);
  const report = selectedRun?.report;
  const profileMatches = robot?.profile === release.manifest.profile && suite?.spec.contract.profile === release.manifest.profile;
  const disabled = busy || !writable || !loaded;

  useEffect(() => {
    let current = true;
    void Promise.all([
      api<EvaluationSuite[]>(`applications/${application.id}/evaluation-suites`),
      api<Qualification>(qualificationPath),
    ]).then(([available, status]) => {
      if (current) { setSuites(available); onQualification(status); setLoaded(true); setError(null); }
    }).catch(cause => {
      if (current) { setError(errorText(cause)); setLoaded(false); onQualification(null); }
    });
    return () => { current = false; };
  }, [application.id, qualificationPath, snapshot, onQualification]);

  useEffect(() => {
    let current = true;
    // A long-unresolved reservation can fall outside the bounded run history.
    if (robot?.evaluation_id && !runs.some(item => item.id === robot.evaluation_id)) {
      void api<EvaluationRun>(`evaluations/${robot.evaluation_id}`).then(value => {
        if (current) setExtraReservation(value);
      }).catch(cause => { if (current) setError(errorText(cause)); });
    }
    return () => { current = false; };
  }, [robot?.evaluation_id, runs]);

  async function act(action: () => Promise<void>) {
    await run(async () => { setNotice(null); await action(); });
  }

  return <section className="console-card console-history console-evaluations" aria-labelledby="evaluations-heading">
    <div className="console-section-heading"><div><p className="console-eyebrow">03 / Evaluate</p><h2 id="evaluations-heading">Release qualification</h2></div><span>{application.name}</span></div>
    <p>Run the same fixed scenarios for each candidate, inspect its evidence, then choose whether to promote it.</p>
    {error && <p className="console-alert" role="alert">{error} Evaluation admission is disabled until this data refreshes.</p>}
    {notice && <p className="console-notice" role="status">{notice}</p>}
    {robot?.evaluation_id && <div className="console-reservation" role="status">
      <p><strong>{robot.name} is reserved by evaluation <code>{robot.evaluation_id}</code>.</strong> Ordinary deployment and Start remain unavailable until its reservation is released.</p>
      {reservation ? <>
        <p>State: {reservation.state.replaceAll("_", " ")} · {reservation.cases.filter(item => item.episode_id).length}/{reservation.cases.length} case reports received. {reservation.detail}</p>
        {reservation.state === "unknown" && <p>Execution is unresolved. Cancellation can be requested, but only reconciliation can release the robot.</p>}
        <button className="btn btn-secondary" disabled={disabled || terminal(reservation.state) || reservation.state === "cancel_requested"} onClick={() => void act(async () => {
          await attempts.current.submit(`evaluations/${reservation.id}/cancel`, { reason: "Requested from release qualification console" });
          setNotice("Evaluation cancellation requested. Waiting for the active mission and job to acknowledge it.");
        })}>{reservation.state === "cancel_requested" ? "Evaluation cancellation awaiting acknowledgement" : "Request evaluation cancellation"}</button>
      </> : <p>Reading the reserved evaluation…</p>}
    </div>}
    <div className="console-evaluation-columns">
      <div>
        <h3>Fixed scenario suite</h3>
        <label>Evaluation suite<select value={suite?.id ?? ""} disabled={busy} onChange={event => { setSuiteId(event.target.value); setRunId(""); setBaselineId(""); setComparison(null); }}><option value="" disabled>{loaded ? "No suites yet" : "Loading suites…"}</option>{suites.map(item => <option key={item.id} value={item.id}>{item.spec.name}</option>)}</select></label>
        {suite && <><p className="console-note">Seeds: {suite.spec.seeds.join(", ")} · Requires {suite.spec.min_successes}/{suite.spec.seeds.length} successful cases.<br />Immutable suite <code>{suite.digest}</code></p>
          <button className="btn btn-primary" disabled={disabled || !dispatchAvailable || !profileMatches || !robot || !!robot.evaluation_id || hasActiveMission} onClick={() => void act(async () => {
            if (!robot) return;
            const created = await attempts.current.submit<EvaluationRun>("evaluations", { suite_id: suite.id, release_id: release.id, robot_id: robot.id });
            setRunId(created.id); setNotice("Evaluation requested. Its reserved robot will execute the persisted cases.");
          })}>Evaluate selected release</button>
          <p className="console-note">Uses {robot?.name ?? "the selected robot"} and release <code>{release.id}</code>. Its inference worker must already serve that release. Stay signed in: signing out revokes this evaluation’s authority to admit new cases.</p>
          {!profileMatches && <p className="console-note">The robot, release and suite must use the same observation profile. Select a matching robot and suite, or create a suite from this release.</p>}
        </>}
        <details><summary>Create an immutable suite</summary><form onSubmit={event => {
          event.preventDefault(); const data = new FormData(event.currentTarget);
          void act(async () => {
            const seeds = parseSeeds(data.get("seeds"));
            const minimum = Number(data.get("minimum"));
            if (!Number.isInteger(minimum) || minimum < 1 || minimum > seeds.length) throw new Error("Required successes must be between one and the number of cases.");
            const created = await attempts.current.submit<EvaluationSuite>(`applications/${application.id}/evaluation-suites`, {
              name: data.get("name"), reference_release_id: release.id, seeds, min_successes: minimum,
            });
            setSuites(previous => previous.some(item => item.id === created.id) ? previous : [created, ...previous]);
            setSuiteId(created.id); setRunId(""); setBaselineId(""); setComparison(null);
            setNotice("Immutable suite created. It does not start an evaluation or enable a deployment gate.");
          });
        }}><label>Suite name<input name="name" maxLength={120} defaultValue="Pick-and-place regression" required /></label><label>Scenario seeds<input name="seeds" defaultValue="0, 1" maxLength={230} required /></label><label>Required successes<input name="minimum" type="number" min={1} max={20} step={1} defaultValue={2} required /></label><p className="console-note">This freezes the selected release’s interface, environment and execution limits. Candidate policies may vary; other changes need a new suite.</p><button className="btn btn-secondary" disabled={disabled}>Create suite</button></form></details>
      </div>
      <div>
        <h3>Deployment gate</h3>
        {qualification ? <>
          <p data-testid="qualification-state">{qualification.gate ? <>Required suite: <strong>{suites.find(item => item.id === qualification.gate?.suite_id)?.spec.name ?? qualification.gate.suite_id}</strong> · Gate generation {qualification.gate.generation}.</> : "No evaluation gate is configured for this application."}</p>
          <p>{!qualification.gate ? "Evaluation is optional until a required suite is configured." : qualification.deployment_allowed ? "The selected release has a promotion for the application’s required suite." : "The selected release needs a passing promotion for the required suite before ordinary deployment or Start."}</p>
          {qualification.promotion && <p className="console-note">Promotion recorded from <code>{qualification.promotion.evaluation_id}</code> on {timestamp(qualification.promotion.created_at)}.</p>}
          <button className="btn btn-secondary" disabled={disabled || !suite || qualification.gate?.suite_id === suite.id} onClick={() => void act(async () => {
            if (!suite) return;
            await attempts.current.submit(`applications/${application.id}/evaluation-gate`, { suite_id: suite.id, expected_generation: qualification.gate?.generation ?? 0 });
            onQualification(await api<Qualification>(qualificationPath));
            setNotice("Application gate updated. New deployment and Start requests require promotion for this suite.");
          })}>Require selected suite</button>
          <p className="console-note">This is an explicit application setting. It applies to new work and does not cancel an already authorized mission. Robot readiness and availability are checked separately.</p>
        </> : <p>Reading the selected release’s qualification…</p>}
      </div>
    </div>
    <div className="console-evaluation-report">
      <h3>Results for the selected release and suite</h3>
      <label>Evaluation run<select value={selectedRun?.id ?? ""} disabled={busy} onChange={event => { setRunId(event.target.value); setBaselineId(""); setComparison(null); }}><option value="" disabled>No evaluations yet</option>{candidateRuns.map(item => <option key={item.id} value={item.id}>{item.id} · {item.state.replaceAll("_", " ")}</option>)}</select></label>
      {selectedRun ? <>
        <p className="console-note">{selectedRun.state.replaceAll("_", " ")} · Last report {timestamp(selectedRun.updated_at)} · {selectedRun.detail}</p>
        {report ? <>
          <p className="console-evaluation-outcome" role="status"><strong>{report.passed ? "Passed" : "Did not pass"}</strong> · {report.successes}/{report.case_count} successful cases; {report.min_successes} required. Median case wall time: {report.median_wall_s === null ? "unavailable" : `${report.median_wall_s.toFixed(2)} seconds`}.</p>
          <div className="console-table-wrap"><table><thead><tr><th>Seed</th><th>Mission outcome</th><th>Task result</th><th>Steps</th><th>Evidence</th></tr></thead><tbody>{report.cases.map(item => <tr key={item.seed}><td>{item.seed}</td><td>{item.state.replaceAll("_", " ")}</td><td>{item.passed ? "Succeeded" : item.evidence_valid ? "Did not succeed" : "Incomplete or invalid evidence"}</td><td>{item.steps ?? "—"}</td><td>{item.episode_id ? <button disabled={busy} onClick={() => void run(() => onEpisode(item.episode_id!))}>View case episode</button> : "No episode"}</td></tr>)}</tbody></table></div>
          <p className="console-note">Release <code>{report.release_digest}</code><br />Suite <code>{report.suite_digest}</code>. Fixed simulated cases do not establish general reliability or physical-robot timing.</p>
          {qualification?.gate && qualification.gate.suite_id !== selectedRun.suite_id && <p className="console-note">This report uses a different suite from the application gate. Promoting it will not satisfy the current gate.</p>}
          <button className="btn btn-primary" disabled={disabled || !report.passed || selectedRun.state !== "completed" || qualification?.promotion?.evaluation_id === selectedRun.id} onClick={() => void act(async () => {
            await attempts.current.submit(`evaluations/${selectedRun.id}/promote`, {});
            onQualification(await api<Qualification>(qualificationPath));
            setNotice("Promotion recorded for this report’s exact release and suite. Deployment remains a separate action.");
          })}>{qualification?.promotion?.evaluation_id === selectedRun.id ? "Report promoted" : "Promote passing report"}</button>
          <div className="console-comparison"><label>Compare with baseline<select value={baselineId} disabled={busy} onChange={event => { setBaselineId(event.target.value); setComparison(null); }}><option value="">Choose a report from this same suite</option>{baselines.map(item => <option key={item.id} value={item.id}>{item.id} · {item.release_id}</option>)}</select></label><button className="btn btn-secondary" disabled={busy || !baselines.some(item => item.id === baselineId)} onClick={() => void act(async () => setComparison(await api<EvaluationComparison>(`evaluations/${selectedRun.id}?baseline_id=${baselineId}`)))}>Compare reports</button></div>
          {comparison?.candidate.id === selectedRun.id && comparison.baseline.id === baselineId && <p className="console-notice" role="status">Candidate: {comparison.candidate.report?.successes}/{comparison.candidate.report?.case_count}. Baseline: {comparison.baseline.report?.successes}/{comparison.baseline.report?.case_count}. Success-count change: {comparison.success_count_delta > 0 ? "+" : ""}{comparison.success_count_delta}. Paired fixed seeds; this is not statistical proof of reliability.</p>}
        </> : <p>{selectedRun.cases.filter(item => item.episode_id).length}/{selectedRun.cases.length} case reports received. {terminal(selectedRun.state) ? "No terminal report is available." : "A final score is shown only when the job records its outcome."}</p>}
      </> : <p>No run has been recorded for this selected release and suite.</p>}
    </div>
  </section>;
}
