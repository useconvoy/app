/**
 * suite-report.ts — ONE self-contained HTML file for a SuiteResult: header
 * with the green verdict, scenario matrix, per-gauntlet inline-SVG Q(n)
 * charts, failed-verdict drill-down with rendered evidence, and a cost
 * summary ($ per verified item).
 *
 * SuiteResult does not carry the eval-set thresholds, so the chart's item
 * floor comes in via opts (the CLI passes config.thresholds.itemFloor);
 * without it the threshold line defaults to 0.5 — flagged in RUNNER.NOTES.md.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type { Evidence, ScenarioVerdict, SuiteResult, Verdict } from '../schema/verdict.ts';
import { clip, esc, fmtNum, fmtSigned, fmtUsd, markSvg, page, statusBadge } from './html.ts';

export interface SuiteReportOpts {
  /** Q(n) chart threshold line; pass eval-set thresholds.itemFloor. */
  itemFloor?: number;
}

export function renderSuiteReport(result: SuiteResult, opts: SuiteReportOpts = {}): string {
  const itemFloor = opts.itemFloor ?? 0.5;
  const body = [
    header(result),
    matrix(result),
    gauntletCharts(result, itemFloor),
    drilldown(result),
    costSummary(result),
  ].join('\n');
  return page(`Suite report — ${result.evalSet.name}@${result.evalSet.version}`, body);
}

export function writeSuiteReport(result: SuiteResult, path: string, opts: SuiteReportOpts = {}): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, renderSuiteReport(result, opts));
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function header(result: SuiteResult): string {
  const detail = result.greenDetail.length
    ? `<ul class="detail">${result.greenDetail.map((d) => `<li>${esc(d)}</li>`).join('')}</ul>`
    : '';
  return `
<h1>Eval suite report</h1>
<p class="sub">${esc(result.evalSet.name)}@${esc(result.evalSet.version)} · subject: <code>${esc(
    result.subject.label
  )}</code> (${esc(result.subject.kind)}${result.subject.modelId ? `, ${esc(result.subject.modelId)}` : ''}) ·
${esc(result.startedAt)} → ${esc(result.finishedAt)}</p>
<div class="panel">
  <div class="headline">${markSvg(result.green)} ${result.green ? 'GREEN — suite passes' : 'NOT GREEN — suite fails'}</div>
  ${detail}
</div>`;
}

function matrix(result: SuiteResult): string {
  const rows = result.scenarios
    .map((s) => {
      const d = s.meanDecay;
      return `<tr>
  <td><code>${esc(s.scenarioId)}</code></td>
  <td>${statusBadge(s.status)}</td>
  <td>${s.invariantViolation ? '<span class="badge bad">violated</span>' : '<span class="badge ok">held</span>'}</td>
  <td class="num">${s.trials.length}</td>
  <td class="num">${fmtUsd(s.totalCostUsd)}</td>
  <td class="num">${d ? fmtSigned(d.slope) : '—'}</td>
  <td class="num">${d ? fmtNum(d.auc, 2) : '—'}</td>
  <td class="num">${d ? fmtNum(d.minQ, 2) : '—'}</td>
  <td>${esc(s.passDetail)}</td>
</tr>`;
    })
    .join('\n');
  return `
<h2>Scenario matrix</h2>
<table>
<thead><tr><th>Scenario</th><th>Status</th><th>Invariants</th><th class="num">Trials</th><th class="num">Cost</th><th class="num">Slope</th><th class="num">AUC</th><th class="num">Min Q</th><th>Detail</th></tr></thead>
<tbody>${rows}</tbody>
</table>`;
}

// ---------------------------------------------------------------------------
// Q(n) decay chart — one per scenario that produced item verdicts.
// ---------------------------------------------------------------------------

interface QPoint {
  ordinal: number;
  q: number;
}

function meanQByOrdinal(s: ScenarioVerdict): QPoint[] {
  const byOrdinal = new Map<number, number[]>();
  for (const trial of s.trials) {
    for (const item of trial.items) {
      const list = byOrdinal.get(item.ordinal) ?? [];
      list.push(item.q);
      byOrdinal.set(item.ordinal, list);
    }
  }
  return [...byOrdinal.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([ordinal, qs]) => ({ ordinal, q: qs.reduce((a, b) => a + b, 0) / qs.length }));
}

function qChartSvg(points: QPoint[], itemFloor: number): string {
  const W = 640;
  const H = 220;
  const ML = 44; // left margin (y labels)
  const MR = 16;
  const MT = 14;
  const MB = 30; // bottom margin (x labels)
  const plotW = W - ML - MR;
  const plotH = H - MT - MB;
  const minOrd = points[0]?.ordinal ?? 1;
  const maxOrd = points[points.length - 1]?.ordinal ?? 1;
  const span = Math.max(1, maxOrd - minOrd);
  const x = (ordinal: number): number => ML + ((ordinal - minOrd) / span) * plotW;
  const y = (q: number): number => MT + (1 - q) * plotH;

  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map(
      (g) =>
        `<line x1="${ML}" y1="${y(g)}" x2="${W - MR}" y2="${y(g)}" stroke="var(--border)" stroke-width="1"/>` +
        `<text x="${ML - 8}" y="${y(g) + 4}" text-anchor="end" font-size="10" fill="var(--fg-dim)">${g}</text>`
    )
    .join('');

  const labelEvery = Math.max(1, Math.ceil(points.length / 12));
  const xLabels = points
    .filter((_, i) => i % labelEvery === 0 || i === points.length - 1)
    .map(
      (p) =>
        `<text x="${x(p.ordinal)}" y="${H - 10}" text-anchor="middle" font-size="10" fill="var(--fg-dim)">${p.ordinal}</text>`
    )
    .join('');

  const threshold =
    `<line x1="${ML}" y1="${y(itemFloor)}" x2="${W - MR}" y2="${y(itemFloor)}" stroke="var(--warn)" stroke-width="1" stroke-dasharray="5 4"/>` +
    `<text x="${W - MR}" y="${y(itemFloor) - 5}" text-anchor="end" font-size="10" fill="var(--warn)">item floor ${itemFloor}</text>`;

  const polyline =
    points.length > 1
      ? `<polyline points="${points.map((p) => `${x(p.ordinal).toFixed(1)},${y(p.q).toFixed(1)}`).join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2"/>`
      : '';

  const dots = points
    .map(
      (p) =>
        `<circle cx="${x(p.ordinal).toFixed(1)}" cy="${y(p.q).toFixed(1)}" r="4" fill="${
          p.q >= itemFloor ? 'var(--ok)' : 'var(--bad)'
        }"><title>item ordinal ${p.ordinal}: mean Q ${p.q.toFixed(3)}</title></circle>`
    )
    .join('');

  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Q by item ordinal">
<rect x="${ML}" y="${MT}" width="${plotW}" height="${plotH}" fill="none" stroke="var(--border)"/>
${grid}
${threshold}
${polyline}
${dots}
${xLabels}
<text x="${ML}" y="${H - 10}" font-size="10" fill="var(--fg-dim)">item ordinal n →</text>
</svg>`;
}

function gauntletCharts(result: SuiteResult, itemFloor: number): string {
  const sections = result.scenarios
    .map((s) => {
      const points = meanQByOrdinal(s);
      if (points.length === 0) return '';
      const d = s.meanDecay;
      const statLine = d
        ? `slope ${fmtSigned(d.slope)} · auc ${fmtNum(d.auc, 3)} · first-decile ${fmtNum(d.firstDecileMean, 2)} → last-decile ${fmtNum(
            d.lastDecileMean,
            2
          )} · min Q ${fmtNum(d.minQ, 2)} over ${d.itemCount} items`
        : `${points.length} items`;
      return `<div class="panel">
<h3><code>${esc(s.scenarioId)}</code> — mean Q(n) across ${s.trials.length} trial(s)</h3>
<p class="sub">${esc(statLine)}</p>
<div class="chart-wrap">${qChartSvg(points, itemFloor)}</div>
</div>`;
    })
    .filter((s) => s !== '')
    .join('\n');
  if (!sections) return '';
  return `\n<h2>Quality decay Q(n)</h2>\n${sections}`;
}

// ---------------------------------------------------------------------------
// Failed-verdict drill-down
// ---------------------------------------------------------------------------

interface FlaggedVerdict {
  trialIdx: number;
  itemId?: string;
  verdict: Verdict;
}

function flaggedVerdicts(s: ScenarioVerdict): FlaggedVerdict[] {
  const out: FlaggedVerdict[] = [];
  for (const trial of s.trials) {
    for (const v of trial.verdicts) {
      if (v.status === 'fail' || v.status === 'error' || v.status === 'missing') {
        out.push({ trialIdx: trial.trialIdx, verdict: v });
      }
    }
    for (const item of trial.items) {
      for (const v of item.verdicts) {
        if (v.status === 'fail' || v.status === 'error' || v.status === 'missing') {
          out.push({ trialIdx: trial.trialIdx, itemId: item.itemId, verdict: v });
        }
      }
    }
  }
  return out;
}

function renderEvidence(e: Evidence): string {
  switch (e.kind) {
    case 'event':
      return `<div class="evidence"><span class="ev-kind">event</span><code>${esc(e.eventId)}</code>${
        e.note ? ` — ${esc(e.note)}` : ''
      }</div>`;
    case 'artifact':
      return `<div class="evidence"><span class="ev-kind">artifact</span><code>${esc(e.hash.slice(0, 16))}</code>${
        e.excerpt ? `<pre>${esc(clip(e.excerpt))}</pre>` : ''
      }</div>`;
    case 'world':
      return `<div class="evidence"><span class="ev-kind">world query</span><code>${esc(e.query)}</code><pre>${esc(
        clip(JSON.stringify(e.result, null, 2) ?? 'undefined')
      )}</pre></div>`;
    case 'judge_rationale':
      return `<div class="evidence"><span class="ev-kind">judge sample ${e.sampleIdx}</span><pre>${esc(
        clip(e.text)
      )}</pre></div>`;
    case 'note':
      return `<div class="evidence"><span class="ev-kind">note</span>${esc(e.text)}</div>`;
  }
}

function drilldown(result: SuiteResult): string {
  const sections = result.scenarios
    .map((s) => {
      const flagged = flaggedVerdicts(s);
      if (flagged.length === 0) return '';
      const rows = flagged
        .map(
          ({ trialIdx, itemId, verdict: v }) => `<tr>
  <td class="num">${trialIdx}</td>
  <td>${itemId ? `<code>${esc(itemId)}</code>` : '<span class="sub">run</span>'}</td>
  <td><code>${esc(v.graderId)}</code><br><span class="sub">${esc(v.graderVersion.slice(0, 16))}</span></td>
  <td>${esc(v.class)}${v.advisory ? ' <span class="badge muted">advisory</span>' : ''}${
            v.lowConfidence ? ' <span class="badge warn">low-conf</span>' : ''
          }</td>
  <td>${statusBadge(v.status)}</td>
  <td class="num">${fmtNum(v.score, 2)}</td>
  <td>${v.evidence.map(renderEvidence).join('') || '<span class="sub">—</span>'}</td>
</tr>`
        )
        .join('\n');
      return `<h3><code>${esc(s.scenarioId)}</code> — ${flagged.length} failed/errored verdict(s)</h3>
<table>
<thead><tr><th class="num">Trial</th><th>Item</th><th>Grader</th><th>Class</th><th>Status</th><th class="num">Score</th><th>Evidence</th></tr></thead>
<tbody>${rows}</tbody>
</table>`;
    })
    .filter((s) => s !== '')
    .join('\n');
  if (!sections) return '\n<h2>Failed verdicts</h2>\n<p class="sub">None — every verdict passed.</p>';
  return `\n<h2>Failed verdicts</h2>\n${sections}`;
}

// ---------------------------------------------------------------------------
// Cost summary
// ---------------------------------------------------------------------------

function costSummary(result: SuiteResult): string {
  let passedItems = 0;
  let totalItems = 0;
  for (const s of result.scenarios) {
    for (const trial of s.trials) {
      for (const item of trial.items) {
        totalItems += 1;
        if (item.status === 'pass') passedItems += 1;
      }
    }
  }
  const perVerified =
    passedItems > 0 ? fmtUsd(result.totalCostUsd / passedItems) : '<span class="sub">n/a (no passed items)</span>';
  return `
<h2>Cost</h2>
<div class="panel"><dl class="kv">
  <dt>Total cost</dt><dd>${fmtUsd(result.totalCostUsd)}</dd>
  <dt>Verified (passed) items</dt><dd>${passedItems} of ${totalItems}</dd>
  <dt>$ per verified item</dt><dd>${perVerified}</dd>
</dl></div>`;
}
