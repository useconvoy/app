/**
 * rehearsal-report.ts — the CUSTOMER-facing artifact for one run: mission
 * summary, the interaction timeline on the sim-time axis (messages with
 * counterparty labels, gates raised → who approved and why, artifacts
 * produced), the verification table with evidence, cost + sim-duration, and a
 * sign-off footer block. Same self-contained-HTML rules as the suite report.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ConvoyEvent, EventOfType } from '../runtime/events.ts';
import type { WorldBundle, WorldMessage } from '../sandbox/api.ts';
import type { Scenario } from '../schema/scenario.ts';
import type { TrialResult, Verdict } from '../schema/verdict.ts';
import { clip, esc, fmtNum, fmtUsd, markSvg, page, statusBadge } from './html.ts';

export interface RehearsalReportOpts {
  scenario: Scenario;
  trial: TrialResult;
  events: ConvoyEvent[];
  world: WorldBundle;
}

export function renderRehearsalReport(opts: RehearsalReportOpts): string {
  const body = [
    missionSummary(opts),
    timeline(opts),
    verification(opts.trial),
    costBlock(opts.trial),
    signoff(),
  ].join('\n');
  return page(`Rehearsal report — ${opts.scenario.title}`, body);
}

export function writeRehearsalReport(opts: RehearsalReportOpts, path: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, renderRehearsalReport(opts));
}

// ---------------------------------------------------------------------------
// Mission summary
// ---------------------------------------------------------------------------

function goalOf(scenario: Scenario): string {
  return scenario.trigger.missionSpec.goal;
}

function missionSummary({ scenario, trial }: RehearsalReportOpts): string {
  const ok = trial.status === 'completed';
  return `
<h1>Rehearsal report</h1>
<p class="sub">${esc(scenario.title)} · <code>${esc(scenario.id)}</code> · mission type <code>${esc(
    scenario.missionType
  )}</code></p>
<div class="panel">
  <div class="headline">${markSvg(ok)} Run ${statusBadge(trial.status)}</div>
  <dl class="kv" style="margin-top:.6rem">
    <dt>Goal</dt><dd>${esc(goalOf(scenario))}</dd>
    <dt>Simulation start (t0)</dt><dd>${esc(scenario.t0)}</dd>
    <dt>Run id</dt><dd><code>${esc(trial.runId)}</code></dd>
    ${trial.deadlockDiagnosis ? `<dt>Deadlock diagnosis</dt><dd>${esc(trial.deadlockDiagnosis)}</dd>` : ''}
  </dl>
</div>`;
}

// ---------------------------------------------------------------------------
// Interaction timeline in sim time
// ---------------------------------------------------------------------------

interface TimelineEntry {
  ts: string;
  order: number; // stable tiebreak
  kind: string;
  html: string;
}

/** address / portal account → counterparty actor label. */
function actorLabels(scenario: Scenario): Map<string, string> {
  const map = new Map<string, string>();
  for (const cp of scenario.counterparties) {
    for (const owned of cp.owns) map.set(owned.toLowerCase(), cp.actorId);
  }
  return map;
}

function labelFor(addr: string, labels: Map<string, string>): string {
  const hit = labels.get(addr.toLowerCase());
  return hit ? `${hit} <${addr}>` : addr;
}

function simOffset(ts: string, t0: string): string {
  const days = (Date.parse(ts) - Date.parse(t0)) / 86_400_000;
  return Number.isFinite(days) ? `t0${days >= 0 ? '+' : ''}${days.toFixed(1)}d` : '';
}

function messageEntries(world: WorldBundle, labels: Map<string, string>): TimelineEntry[] {
  return world.messages.map((m: WorldMessage, i: number) => {
    const toLabels = m.to.map((t) => labelFor(t, labels)).join(', ');
    const html =
      m.direction === 'outbound'
        ? `Message sent — <strong>Agent → ${esc(toLabels)}</strong>: “${esc(m.subject)}”` +
          (m.attachments.length ? ` <span class="sub">(${m.attachments.map((a) => esc(a.name)).join(', ')})</span>` : '')
        : `Message received — <strong>${esc(labelFor(m.from, labels))} → Agent</strong>: “${esc(m.subject)}”` +
          (m.attachments.length ? ` <span class="sub">(${m.attachments.map((a) => esc(a.name)).join(', ')})</span>` : '');
    return { ts: m.ts, order: i, kind: m.direction === 'outbound' ? 'sent' : 'received', html };
  });
}

function eventEntries(events: ConvoyEvent[]): TimelineEntry[] {
  const resolutions = new Map<string, EventOfType<'gate_resolved'>>();
  for (const e of events) if (e.type === 'gate_resolved') resolutions.set(e.gateId, e);

  const out: TimelineEntry[] = [];
  events.forEach((e, i) => {
    switch (e.type) {
      case 'mission_started':
        out.push({ ts: e.ts, order: i, kind: 'start', html: `Mission started — ${esc(e.goal)}` });
        break;
      case 'gate_raised': {
        const r = resolutions.get(e.gateId);
        const resolved = r
          ? `<strong>${esc(r.resolution)}</strong> by <strong>${esc(r.resolvedBy)}</strong>` +
            (r.reason ? ` — “${esc(r.reason)}”` : '')
          : '<span class="badge warn">unresolved</span>';
        out.push({
          ts: e.ts,
          order: i,
          kind: 'gate',
          html:
            `Gate raised (<code>${esc(e.kind)}</code>${e.stepTag ? `, step ${esc(e.stepTag)}` : ''}) → ${resolved}` +
            `<div class="sub">gate <code>${esc(e.gateId)}</code>${
              r ? ` · resolved ${esc(simOffsetPair(e.ts, r.ts))} later` : ''
            }</div>`,
        });
        break;
      }
      case 'steer':
        out.push({ ts: e.ts, order: i, kind: 'steer', html: `Steer (${esc(e.priority)}) from ${esc(e.author)} — “${esc(clip(e.message, 300))}”` });
        break;
      case 'artifact_created':
        out.push({
          ts: e.ts,
          order: i,
          kind: 'artifact',
          html: `Artifact produced — <code>${esc(e.tag)}</code>${e.mime ? ` (${esc(e.mime)})` : ''} <span class="sub">${esc(
            e.hash.slice(0, 12)
          )}</span>`,
        });
        break;
      case 'terminal_outcome':
        out.push({
          ts: e.ts,
          order: i,
          kind: 'terminal',
          html: `Mission ${statusBadge(e.status)} (judged by ${esc(e.judgedBy)})${e.summary ? ` — ${esc(e.summary)}` : ''}`,
        });
        break;
      default:
        break;
    }
  });
  return out;
}

function simOffsetPair(fromTs: string, toTs: string): string {
  const days = (Date.parse(toTs) - Date.parse(fromTs)) / 86_400_000;
  if (!Number.isFinite(days)) return '';
  if (Math.abs(days) >= 1) return `${days.toFixed(1)}d`;
  const hours = days * 24;
  if (Math.abs(hours) >= 1) return `${hours.toFixed(1)}h`;
  return `${(hours * 60).toFixed(0)}m`;
}

function timeline({ scenario, events, world }: RehearsalReportOpts): string {
  const labels = actorLabels(scenario);
  const entries = [...messageEntries(world, labels), ...eventEntries(events)].sort((a, b) =>
    a.ts === b.ts ? a.order - b.order : a.ts < b.ts ? -1 : 1
  );
  if (entries.length === 0) return '\n<h2>Interaction timeline</h2>\n<p class="sub">No recorded interactions.</p>';
  const rows = entries
    .map(
      (e) => `<tr>
  <td class="ts">${esc(simOffset(e.ts, scenario.t0))}<br><span class="sub">${esc(e.ts)}</span></td>
  <td>${esc(e.kind)}</td>
  <td>${e.html}</td>
</tr>`
    )
    .join('\n');
  return `
<h2>Interaction timeline (sim time)</h2>
<table class="timeline">
<thead><tr><th>Sim time</th><th>Kind</th><th>What happened</th></tr></thead>
<tbody>${rows}</tbody>
</table>`;
}

// ---------------------------------------------------------------------------
// Verification table
// ---------------------------------------------------------------------------

function evidenceSummary(v: Verdict): string {
  if (v.evidence.length === 0) return '<span class="sub">—</span>';
  return v.evidence
    .slice(0, 3)
    .map((e) => {
      switch (e.kind) {
        case 'event':
          return `<div class="evidence"><span class="ev-kind">event</span><code>${esc(e.eventId)}</code>${
            e.note ? ` ${esc(e.note)}` : ''
          }</div>`;
        case 'artifact':
          return `<div class="evidence"><span class="ev-kind">artifact</span><code>${esc(e.hash.slice(0, 12))}</code>${
            e.excerpt ? `<pre>${esc(clip(e.excerpt, 400))}</pre>` : ''
          }</div>`;
        case 'world':
          return `<div class="evidence"><span class="ev-kind">world</span><code>${esc(e.query)}</code><pre>${esc(
            clip(JSON.stringify(e.result) ?? 'undefined', 400)
          )}</pre></div>`;
        case 'judge_rationale':
          return `<div class="evidence"><span class="ev-kind">judge</span>${esc(clip(e.text, 400))}</div>`;
        case 'note':
          return `<div class="evidence"><span class="ev-kind">note</span>${esc(clip(e.text, 400))}</div>`;
      }
    })
    .join('');
}

function verification(trial: TrialResult): string {
  const rows: string[] = [];
  const row = (scope: string, v: Verdict): string => `<tr>
  <td>${scope}</td>
  <td><code>${esc(v.graderId)}</code></td>
  <td>${esc(v.class)}${v.advisory ? ' <span class="badge muted">advisory</span>' : ''}</td>
  <td>${statusBadge(v.status)}</td>
  <td class="num">${fmtNum(v.score, 2)}</td>
  <td>${evidenceSummary(v)}</td>
</tr>`;
  for (const v of trial.verdicts) rows.push(row('<span class="sub">run</span>', v));
  for (const item of trial.items) {
    for (const v of item.verdicts) rows.push(row(`<code>${esc(item.itemId)}</code>`, v));
    if (item.verdicts.length === 0) {
      rows.push(`<tr><td><code>${esc(item.itemId)}</code></td><td class="sub" colspan="2">item</td><td>${statusBadge(
        item.status
      )}</td><td class="num">${fmtNum(item.q, 2)}</td><td><span class="sub">—</span></td></tr>`);
    }
  }
  if (rows.length === 0) return '\n<h2>Verification</h2>\n<p class="sub">No verdicts recorded for this trial.</p>';
  return `
<h2>Verification</h2>
<table>
<thead><tr><th>Scope</th><th>Check</th><th>Class</th><th>Status</th><th class="num">Score</th><th>Evidence</th></tr></thead>
<tbody>${rows.join('\n')}</tbody>
</table>`;
}

// ---------------------------------------------------------------------------
// Cost + sim duration, sign-off
// ---------------------------------------------------------------------------

function costBlock(trial: TrialResult): string {
  return `
<h2>Cost and duration</h2>
<div class="panel"><dl class="kv">
  <dt>Cost</dt><dd>${fmtUsd(trial.costUsd)}</dd>
  <dt>Simulated duration</dt><dd>${fmtNum(trial.simDays, 1)} days</dd>
  <dt>Wall-clock runtime</dt><dd>${(trial.wallMs / 1000).toFixed(1)}s</dd>
</dl></div>`;
}

function signoff(): string {
  return `
<section class="signoff">
  <h2 style="margin-top:0">Sign-off</h2>
  <p class="sub">I have reviewed this rehearsal — including every gate resolution and the verification evidence above — and approve this routine for the certified configuration.</p>
  <p>Name: <span class="line"></span> Role: <span class="line"></span></p>
  <p>Date: <span class="line"></span> Signature: <span class="line"></span></p>
</section>`;
}
