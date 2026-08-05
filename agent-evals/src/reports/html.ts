/**
 * html.ts — shared helpers for the self-contained HTML reports. No external
 * assets, inline CSS only, dark theme by default (dark-friendly requirement)
 * with a light fallback via prefers-color-scheme.
 */

export function esc(v: unknown): string {
  return String(v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function fmtUsd(n: number): string {
  return `$${n.toFixed(4)}`;
}

export function fmtNum(n: number, digits = 3): string {
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

export function fmtSigned(n: number, digits = 3): string {
  if (!Number.isFinite(n)) return '—';
  const s = n.toFixed(digits);
  return n >= 0 ? `+${s}` : s;
}

/** Clip long excerpts so evidence blocks stay readable. */
export function clip(text: string, max = 1200): string {
  return text.length > max ? `${text.slice(0, max)}… [${text.length - max} more chars]` : text;
}

const STATUS_CLASS: Record<string, string> = {
  pass: 'ok',
  passed: 'ok',
  completed: 'ok',
  landed: 'ok',
  fail: 'bad',
  failed: 'bad',
  error: 'warn',
  harness_error: 'warn',
  missing: 'bad',
  skipped: 'muted',
  skipped_budget: 'muted',
  quarantined: 'muted',
  deadlock: 'warn',
  guard_tripped: 'warn',
  budget_exceeded: 'warn',
  cancelled: 'muted',
};

export function statusBadge(status: string): string {
  const cls = STATUS_CLASS[status] ?? 'muted';
  return `<span class="badge ${cls}">${esc(status)}</span>`;
}

/** Inline SVG check / cross — guarantees every report ships at least one <svg>. */
export function markSvg(ok: boolean): string {
  return ok
    ? '<svg class="mark" width="20" height="20" viewBox="0 0 20 20" role="img" aria-label="green">' +
        '<circle cx="10" cy="10" r="9" fill="none" stroke="var(--ok)" stroke-width="2"/>' +
        '<path d="M5.5 10.5l3 3 6-7" fill="none" stroke="var(--ok)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    : '<svg class="mark" width="20" height="20" viewBox="0 0 20 20" role="img" aria-label="not green">' +
        '<circle cx="10" cy="10" r="9" fill="none" stroke="var(--bad)" stroke-width="2"/>' +
        '<path d="M6.5 6.5l7 7M13.5 6.5l-7 7" fill="none" stroke="var(--bad)" stroke-width="2" stroke-linecap="round"/></svg>';
}

export const BASE_CSS = `
:root {
  --bg: #0b1120; --panel: #111a2e; --panel2: #16213a; --border: #24314f;
  --fg: #dbe4f4; --fg-dim: #8fa1c0; --accent: #38bdf8;
  --ok: #4ade80; --bad: #f87171; --warn: #fbbf24; --muted: #64748b;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f8fb; --panel: #ffffff; --panel2: #eef2f8; --border: #d4dce8;
    --fg: #1d2839; --fg-dim: #5a6a84; --accent: #0369a1;
    --ok: #15803d; --bad: #b91c1c; --warn: #a16207; --muted: #7d8aa0;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem clamp(1rem, 4vw, 3rem); background: var(--bg); color: var(--fg);
  font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 .6rem; color: var(--accent); text-transform: uppercase; letter-spacing: .06em; }
h3 { font-size: .95rem; margin: 1.2rem 0 .4rem; }
.sub { color: var(--fg-dim); margin: 0 0 1rem; }
table { border-collapse: collapse; width: 100%; background: var(--panel); border: 1px solid var(--border); }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--fg-dim); font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: .05rem .5rem; border-radius: 999px; font-size: .75rem; font-weight: 600; border: 1px solid; }
.badge.ok { color: var(--ok); border-color: var(--ok); }
.badge.bad { color: var(--bad); border-color: var(--bad); }
.badge.warn { color: var(--warn); border-color: var(--warn); }
.badge.muted { color: var(--muted); border-color: var(--muted); }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.2rem; margin: .8rem 0; }
.headline { display: flex; align-items: center; gap: .6rem; font-size: 1.1rem; font-weight: 700; }
.mark { flex: none; }
pre { background: var(--panel2); border: 1px solid var(--border); border-radius: 6px; padding: .6rem .8rem; overflow-x: auto; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; word-break: break-word; margin: .3rem 0; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .85em; color: var(--accent); }
ul.detail { margin: .4rem 0 0; padding-left: 1.2rem; color: var(--fg-dim); }
.evidence { margin: .35rem 0 .1rem; }
.evidence .ev-kind { color: var(--fg-dim); font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; margin-right: .4rem; }
.chart-wrap { overflow-x: auto; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1.2rem; }
.kv dt { color: var(--fg-dim); }
.kv dd { margin: 0; }
.timeline td.ts { white-space: nowrap; color: var(--fg-dim); font-variant-numeric: tabular-nums; }
.signoff { border: 1px dashed var(--border); border-radius: 8px; padding: 1rem 1.2rem; margin-top: 2rem; }
.signoff .line { display: inline-block; min-width: 14rem; border-bottom: 1px solid var(--fg-dim); margin: 0 1.5rem .2rem .4rem; }
footer { margin-top: 2.5rem; color: var(--muted); font-size: .78rem; }
`;

export function page(title: string, body: string): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<style>${BASE_CSS}</style>
</head>
<body>
${body}
<footer>Generated ${esc(new Date().toISOString())} · convoy-evals · self-contained report (no external assets)</footer>
</body>
</html>
`;
}
