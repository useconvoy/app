"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { getConfiguration, useWorkspace } from "@/lib/configurations/client";
import {
  applyDraft, buildCatalog, checkCounts, checkSummary, compatibilityFor, COMPUTED_CHECKS, connectedRobots, createOutcome, DRAFT_STEPS, draftIssues, draftRevisionLabel, initialDraft,
  parseStep, stepNumber, stepValue,
} from "@/lib/configurations/draft";
import type { ConfigurationDraft, DraftIssue, DraftStep } from "@/lib/configurations/draft";
import type { LiveBinding } from "@/lib/configurations/live";
import { QUERY, routes } from "@/lib/configurations/routes";
import type { CompatibilityCheck, ConvoyWorkspace, Robot } from "@/lib/configurations/types";
import { AppShell, type Crumb } from "../AppShell";
import { Badge } from "../Badges";
import { CompatList } from "../Compat";
import { Panel } from "../Facts";
import { useNow, useQueryState } from "../hooks";
import { Icon } from "../Icons";
import { useLiveRobots } from "../LiveDeviceProvider";
import { ImportWorkspaceButton, Notice, WorkspaceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { EmptyState, LoadingState } from "../States";
import { Stepper, type StepItem } from "../Stepper";
import { CloudStep, EdgeStep, HardwareStep, ReviewPanels, RobotStep, RoutingStep } from "./NewConfigurationSteps";
import type { StepProps } from "./NewConfigurationSteps";

const ROOT: Crumb = { label: "Configurations", href: routes.index() };
const NEW_CRUMBS: Crumb[] = [ROOT, { label: "New configuration" }];
const STEP_IDS = DRAFT_STEPS.map(step => step.id);
const issueKey = (issue: DraftIssue) => `${issue.step}|${issue.field}|${issue.message}`;
const TOTAL = String(DRAFT_STEPS.length).padStart(2, "0");

/**
 * Screen 1b · New configuration (`/app/configurations/new`, `?from=<configId>`, `?step=`).
 * With `from` the draft is the configuration's next revision; without it, a new
 * configuration copied from the recommended one. Six steps with real controls,
 * a review with the compatibility check, and Create, which saves the workspace
 * document and opens the configuration's dashboard.
 */
export function NewConfigurationPage() {
  const ws = useWorkspace();
  const [from] = useQueryState(QUERY.from);
  if (ws.status === "loading") return <AppShell crumbs={NEW_CRUMBS}><LoadingState /></AppShell>;
  const start = initialDraft(ws.workspace, from);
  if (!start) {
    return <AppShell crumbs={NEW_CRUMBS}>
      <PageHeader eyebrow="New configuration" title="New configuration" actions={<Link className="cfg-btn-text" href={routes.index()}>Cancel</Link>} />
      <WorkspaceNotice workspace={ws.workspace} />
      <EmptyState icon="info" title="There is nothing to start a configuration from yet."
        text="New configurations start from the robots, edge hardware and models a workspace describes. Import a workspace file that includes at least one configuration."
        action={<ImportWorkspaceButton />} />
    </AppShell>;
  }
  return <NewConfigurationForm key={from ?? ""} workspace={ws.workspace} initial={start.draft} missing={start.missing} />;
}

function NewConfigurationForm({ workspace: current, initial, missing }: { workspace: ConvoyWorkspace; initial: ConfigurationDraft; missing: string | null }) {
  const ws = useWorkspace();
  const router = useRouter();
  const now = useNow();
  const [stepParam, setStepParam] = useQueryState(QUERY.step);
  const [step, setStep] = useState<DraftStep>(() => parseStep(stepParam));
  const [draft, setDraft] = useState(initial);
  // Problems the form opened with (a copy's empty name) show once their step has been left or Create was tried;
  // problems the user causes (a route that needs another model) show at once.
  const [checked, setChecked] = useState<ReadonlySet<DraftStep>>(() => new Set());
  const [quiet] = useState(() => new Set(draftIssues(current, initial).map(issueKey)));
  // While a save is in flight the store already shows it; the form keeps reading the workspace it saved against.
  const [savingFrom, setSavingFrom] = useState<ConvoyWorkspace | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusRequest) heading.current?.focus(); }, [focusRequest]);

  const workspace = savingFrom ?? current;
  const saving = savingFrom !== null;
  const catalog = useMemo(() => buildCatalog(workspace), [workspace]);
  const issues = useMemo(() => draftIssues(workspace, draft), [workspace, draft]);
  const checks = useMemo(() => compatibilityFor(workspace, draft), [workspace, draft]);
  const connected = useMemo(() => connectedRobots(workspace, draft.edgeHardware.name), [workspace, draft.edgeHardware.name]);
  const live = useLiveRobots(connected);

  const base = getConfiguration(workspace, draft.baseId);
  const revisionMode = draft.saveAs === "revision" && !!base;
  const rev = draftRevisionLabel(workspace, draft);
  const name = draft.name.trim();
  const index = STEP_IDS.indexOf(step);
  const cancelHref = initial.saveAs === "revision" ? routes.configuration(initial.baseId) : routes.index();
  const blockedReason = saving ? null : issues.length ? `Fix ${issues.length === 1 ? "the item" : `the ${issues.length} items`} listed under Review to create it.` : !ws.canSave ? "This workspace cannot be saved right now; see the notice at the top of the page." : null;

  function go(next: DraftStep) {
    if (next !== step) setChecked(previous => new Set(previous).add(step));
    setStep(next);
    setStepParam(next === "robot" ? null : next, { replace: true });
    setFocusRequest(count => count + 1);
  }
  const update: StepProps["update"] = change => { setSaveError(null); setDraft(previous => change(previous)); };
  const visible = (issue: DraftIssue) => checked.has(issue.step) || step === "review" || !quiet.has(issueKey(issue));
  const errors: StepProps["errors"] = field => issues.filter(issue => issue.field === field && visible(issue)).map(issue => issue.message);
  const fixes: StepProps["fixes"] = field => [...new Set(issues.filter(issue => issue.field === field && visible(issue) && issue.fix).map(issue => issue.fix as DraftStep))];

  async function create() {
    if (saving) return;
    if (issues.length || !ws.canSave) { setChecked(new Set(STEP_IDS)); return; }
    setSavingFrom(workspace);
    setSaveError(null);
    const created: { value: { configId: string; rev: string } | null } = { value: null };
    const at = Date.now();
    let result: Awaited<ReturnType<typeof ws.save>>;
    try {
      result = await ws.save(latest => {
        const out = applyDraft(latest, draft, at);
        created.value = { configId: out.configId, rev: out.rev };
        return out.workspace;
      });
    } catch {
      result = { ok: false, error: "The change could not be saved. Try again." };
    }
    if (!result.ok) { setSavingFrom(null); setSaveError(result.error); return; }
    const id = created.value?.configId;
    router.replace(id && result.workspace.configurations.some(config => config.id === id) ? routes.configuration(id) : routes.index());
  }

  const stepItems: StepItem[] = DRAFT_STEPS.map(item => {
    const own = issues.filter(issue => issue.step === item.id).length;
    return {
      id: item.id, label: item.label,
      value: own ? `${own} ${own === 1 ? "item" : "items"} to fix` : stepValue(draft, item.id, item.id === "review" && step === "review" ? checks : undefined),
      state: item.id === step ? "current" : item.id !== "review" && !own ? "done" : "upcoming",
    };
  });
  const props: StepProps = { workspace, catalog, draft, update, errors, fixes, now, go };
  const crumbs: Crumb[] = revisionMode && base ? [ROOT, { label: base.name, href: routes.configuration(base.id) }, { label: "New revision" }] : NEW_CRUMBS;
  const title = revisionMode && base ? `${name || base.name} ${rev}` : name || "New configuration";
  const next = DRAFT_STEPS[index + 1];

  return <AppShell crumbs={crumbs}>
    <PageHeader eyebrow={revisionMode ? "New revision" : "New configuration"} title={title} badges={<Badge>Draft</Badge>}
      actions={<Link className="cfg-btn-text" href={cancelHref}>Cancel</Link>}
      meta={`Started from ${revisionMode ? draft.baseRev : `${base?.name ?? "a template"} ${draft.baseRev}`} · Not saved yet · Creating stores ${revisionMode ? rev : "it"} in the workspace; nothing is deployed`} />
    <WorkspaceNotice workspace={workspace} />
    {missing && <Notice tone="warning">No configuration has the id “{missing}”, so this starts from <strong>{base?.name ?? "a template"}</strong> as a new configuration.</Notice>}

    <div className="cfg-split ci-split">
      <div className="ci-steps"><Stepper label="New configuration steps" steps={stepItems} onSelect={id => go(parseStep(id))} /></div>
      <div className="ci-main">
        {step === "review"
          ? <Review heading={heading} {...props} issues={issues} checks={checks} connected={connected} live={live} rev={rev} />
          : <StepPanel step={step} heading={heading} incomplete={issues.some(issue => issue.step === step)}>
            {step === "robot" && <RobotStep {...props} />}
            {step === "hardware" && <HardwareStep {...props} connected={connected} live={live} />}
            {step === "edge" && <EdgeStep {...props} />}
            {step === "cloud" && <CloudStep {...props} />}
            {step === "routing" && <RoutingStep {...props} />}
          </StepPanel>}

        {step === "review" && <SaveNotes canSave={ws.canSave} sampleMissing={ws.source === "sample" && ws.reason === "missing"} reason={ws.reason} saveError={saveError} />}

        <div className="cfg-actions ci-foot">
          {step === "review" && blockedReason && <p className="ci-foot__hint" id="ci-create-hint">{blockedReason}</p>}
          <button className="btn btn-secondary cfg-btn" type="button" disabled={index === 0 || saving} onClick={() => go(STEP_IDS[Math.max(index - 1, 0)])}>Back</button>
          {next
            ? <button className="btn btn-primary cfg-btn" type="button" onClick={() => go(next.id)}>Continue to {next.label.toLowerCase()}</button>
            : <button className="btn btn-primary cfg-btn" type="button" aria-disabled={blockedReason || saving ? true : undefined}
              aria-describedby={[blockedReason ? "ci-create-hint" : null, saveError ? "ci-save-error" : null].filter(Boolean).join(" ") || undefined}
              onClick={() => void create()}>{saving ? "Creating…" : revisionMode ? `Create revision ${rev}` : "Create configuration"}</button>}
        </div>
        {saving && <p className="cfg-sr" role="status">Saving the workspace…</p>}
      </div>
      <div className="ci-after">
        <p className="portal-eyebrow">When you create {revisionMode ? rev : "it"}</p>
        <ul>{createOutcome(workspace, draft).map(line => <li key={line}>{line}</li>)}</ul>
      </div>
    </div>
  </AppShell>;
}

function StepPanel({ step, heading, incomplete, children }: { step: DraftStep; heading: RefObject<HTMLHeadingElement | null>; incomplete: boolean; children: ReactNode }) {
  const title = DRAFT_STEPS.find(item => item.id === step)?.title ?? "";
  return <section className="portal-panel ci-step" aria-labelledby="ci-step-title">
    <div className="portal-panel-heading">
      <div><p className="portal-eyebrow">Step {stepNumber(step)} of {TOTAL}</p><h2 id="ci-step-title" ref={heading} tabIndex={-1}>{title}</h2></div>
      {incomplete ? <Badge tone="warning" icon="warning">Incomplete</Badge> : <Badge tone="success" icon="check">Complete</Badge>}
    </div>
    <div className="ci-body">{children}</div>
  </section>;
}

function Review({ heading, issues, checks, connected, live, rev, ...props }: StepProps & {
  heading: RefObject<HTMLHeadingElement | null>; issues: readonly DraftIssue[]; checks: readonly CompatibilityCheck[]; connected: readonly Robot[]; live: Readonly<Record<string, LiveBinding | undefined>>; rev: string;
}) {
  const counts = checkCounts(checks);
  const carriedBlocks = checks.filter(check => check.verdict === "block" && !(COMPUTED_CHECKS as readonly string[]).includes(check.id));
  const label = (step: DraftStep) => `Step ${stepNumber(step)} · ${DRAFT_STEPS.find(item => item.id === step)?.label ?? ""}`;
  return <>
    <div className="portal-section-label ci-review-head">
      <h2 id="ci-step-title" ref={heading} tabIndex={-1}>Review</h2>
      <span>Step {stepNumber("review")} of {TOTAL} · check each part, then create</span>
    </div>
    {issues.length
      ? <div className="portal-notice cfg-notice" role="status">
        <Icon name="warning" />
        <div className="ci-notice__body">
          <p><strong>Not ready to create.</strong> {issues.length === 1 ? "One item needs" : `${issues.length} items need`} a change:</p>
          <ul className="ci-issues">{issues.map(issue => <li key={`${issue.step}-${issue.field}-${issue.message}`}>
            <button className="cfg-btn-text" type="button" onClick={() => props.go(issue.fix ?? issue.step)}>{label(issue.fix ?? issue.step)}</button> {issue.message}
          </li>)}</ul>
        </div>
      </div>
      : <Notice tone="info"><strong>Ready to create.</strong> Compatibility check: {checkSummary(checks)}.{carriedBlocks.length > 0 && <> {carriedBlocks.map(check => check.title).join("; ")} {carriedBlocks.length === 1 ? "is" : "are"} device evidence from the starting revision, not part of this configuration.</>}</Notice>}
    <ReviewPanels {...props} connected={connected} live={live} rev={rev} />
    <Panel eyebrow={`Against ${props.draft.edgeHardware.name} evidence`} title="Compatibility check" titleId="ci-compat-title" className="ci-compat"
      action={<span className="cfg-badges">
        {counts.pass > 0 && <Badge tone="success">{counts.pass} pass</Badge>}
        {counts.warn > 0 && <Badge tone="warning">{counts.warn} {counts.warn === 1 ? "warning" : "warnings"}</Badge>}
        {counts.block > 0 && <Badge tone="warning">{counts.block} blocked</Badge>}
        {counts.pending > 0 && <Badge>{counts.pending} not measured</Badge>}
      </span>}>
      {checks.length ? <CompatList checks={checks} now={props.now} /> : <p className="portal-empty">No checks apply to this draft.</p>}
      <p className="portal-context-note">Each check reuses the evidence stored for the same combination in this workspace, or is worked out from declared values; anything not recorded yet reads Not measured. Live device readings are never stored with a configuration.</p>
    </Panel>
  </>;
}

function SaveNotes({ canSave, sampleMissing, reason, saveError }: { canSave: boolean; sampleMissing: boolean; reason: string | null; saveError: string | null }) {
  return <div className="ci-save">
    {!canSave && <Notice tone="warning" icon="blocked">
      {reason === "invalid" ? "The stored workspace is not valid, so creating would overwrite it. Import a valid workspace first." : "The stored workspace could not be read, so changes are not saved. Reload to try again."}
    </Notice>}
    {canSave && sampleMissing && <Notice tone="info">No workspace document is stored for this account yet. Creating saves this sample workspace, with the new configuration, as <strong>this account’s workspace document</strong>; later changes edit that copy.</Notice>}
    {saveError && <div className="portal-notice portal-notice-error cfg-notice" id="ci-save-error"><Icon name="warning" /><p><strong>Not saved.</strong> {saveError} Your choices are kept.</p></div>}
  </div>;
}
