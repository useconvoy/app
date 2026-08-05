"""Self-contained HTML reports (suite + rehearsal).

This package `__init__` carries the shared helpers from src/reports/html.ts:
no external assets, inline CSS only, dark theme by default (dark-friendly
requirement) with a light fallback via prefers-color-scheme. The renderers
live in `convoy_evals.reports.suite_report` and
`convoy_evals.reports.rehearsal_report` (imported directly — this module does
not import them, so the helpers stay dependency-free).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def esc(v: Any) -> str:
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def fmt_usd(n: float) -> str:
    return "${0:.4f}".format(n)


def fmt_num(n: float, digits: int = 3) -> str:
    if not math.isfinite(n):
        return "—"
    return "{0:.{1}f}".format(n, digits)


def fmt_signed(n: float, digits: int = 3) -> str:
    if not math.isfinite(n):
        return "—"
    s = "{0:.{1}f}".format(n, digits)
    return "+" + s if n >= 0 else s


def fmt_short(n: float) -> str:
    """JS-style short number rendering (0.7 not 0.700000)."""
    return "%g" % n


def clip(text: str, max_len: int = 1200) -> str:
    """Clip long excerpts so evidence blocks stay readable."""
    if len(text) > max_len:
        return "{0}… [{1} more chars]".format(text[:max_len], len(text) - max_len)
    return text


_STATUS_CLASS = {
    "pass": "ok",
    "passed": "ok",
    "completed": "ok",
    "landed": "ok",
    "fail": "bad",
    "failed": "bad",
    "error": "warn",
    "harness_error": "warn",
    "missing": "bad",
    "skipped": "muted",
    "skipped_budget": "muted",
    "quarantined": "muted",
    "deadlock": "warn",
    "guard_tripped": "warn",
    "budget_exceeded": "warn",
    "cancelled": "muted",
}


def status_badge(status: str) -> str:
    cls = _STATUS_CLASS.get(status, "muted")
    return '<span class="badge {0}">{1}</span>'.format(cls, esc(status))


def mark_svg(ok: bool) -> str:
    """Inline SVG check / cross — guarantees every report ships at least one <svg>."""
    if ok:
        return (
            '<svg class="mark" width="20" height="20" viewBox="0 0 20 20" role="img" aria-label="green">'
            '<circle cx="10" cy="10" r="9" fill="none" stroke="var(--ok)" stroke-width="2"/>'
            '<path d="M5.5 10.5l3 3 6-7" fill="none" stroke="var(--ok)" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    return (
        '<svg class="mark" width="20" height="20" viewBox="0 0 20 20" role="img" aria-label="not green">'
        '<circle cx="10" cy="10" r="9" fill="none" stroke="var(--bad)" stroke-width="2"/>'
        '<path d="M6.5 6.5l7 7M13.5 6.5l-7 7" fill="none" stroke="var(--bad)" stroke-width="2" '
        'stroke-linecap="round"/></svg>'
    )


BASE_CSS = """
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
"""


def page(title: str, body: str) -> str:
    generated = (
        datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>{title}</title>\n"
        "<style>{css}</style>\n"
        "</head>\n"
        "<body>\n"
        "{body}\n"
        "<footer>Generated {generated} · convoy-evals · self-contained report (no external assets)</footer>\n"
        "</body>\n"
        "</html>\n"
    ).format(title=esc(title), css=BASE_CSS, body=body, generated=esc(generated))
