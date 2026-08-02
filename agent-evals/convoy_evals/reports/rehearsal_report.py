"""rehearsal_report.py — the CUSTOMER-facing artifact for one run. Port of
src/reports/rehearsal-report.ts: mission summary, the interaction timeline on
the sim-time axis (messages with counterparty labels, gates raised -> who
approved and why, artifacts produced), the verification table with evidence,
cost + sim-duration, and a sign-off footer block. Same self-contained-HTML
rules as the suite report.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from convoy_evals.reports import (
    clip,
    esc,
    fmt_num,
    fmt_usd,
    mark_svg,
    page,
    status_badge,
)
from convoy_evals.sandbox.api import WorldBundle
from convoy_evals.schema.scenario import Scenario
from convoy_evals.schema.verdict import TrialResult, Verdict

_DAY_MS = 86_400_000.0


def render_rehearsal_report(
    scenario: Scenario,
    trial: TrialResult,
    events: List[Any],
    world_bundle: WorldBundle,
) -> str:
    body = "\n".join(
        [
            _mission_summary(scenario, trial),
            _timeline(scenario, events, world_bundle),
            _verification(trial),
            _cost_block(trial),
            _signoff(),
        ]
    )
    return page("Rehearsal report — {0}".format(scenario.title), body)


def write_rehearsal_report(
    scenario: Scenario,
    trial: TrialResult,
    events: List[Any],
    world_bundle: WorldBundle,
    path: str,
) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_rehearsal_report(scenario, trial, events, world_bundle))


# ---------------------------------------------------------------------------
# Time helpers (local: reports stay dependency-free of the runner)
# ---------------------------------------------------------------------------


def _parse_ts(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    s = ts
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_between(from_ts: str, to_ts: str) -> Optional[float]:
    a = _parse_ts(from_ts)
    b = _parse_ts(to_ts)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() * 1000.0 / _DAY_MS


def _sim_offset(ts: str, t0: str) -> str:
    days = _days_between(t0, ts)
    if days is None or not math.isfinite(days):
        return ""
    return "t0{0}{1:.1f}d".format("+" if days >= 0 else "", days)


def _sim_offset_pair(from_ts: str, to_ts: str) -> str:
    days = _days_between(from_ts, to_ts)
    if days is None or not math.isfinite(days):
        return ""
    if abs(days) >= 1:
        return "{0:.1f}d".format(days)
    hours = days * 24
    if abs(hours) >= 1:
        return "{0:.1f}h".format(hours)
    return "{0:.0f}m".format(hours * 60)


# ---------------------------------------------------------------------------
# Mission summary
# ---------------------------------------------------------------------------


def _goal_of(scenario: Scenario) -> str:
    return scenario.trigger.missionSpec.goal


def _mission_summary(scenario: Scenario, trial: TrialResult) -> str:
    ok = trial.status == "completed"
    diagnosis = (
        "<dt>Deadlock diagnosis</dt><dd>{0}</dd>".format(esc(trial.deadlockDiagnosis))
        if trial.deadlockDiagnosis
        else ""
    )
    return (
        "\n<h1>Rehearsal report</h1>\n"
        '<p class="sub">{title} · <code>{sid}</code> · mission type <code>{mtype}</code></p>\n'
        '<div class="panel">\n'
        '  <div class="headline">{mark} Run {badge}</div>\n'
        '  <dl class="kv" style="margin-top:.6rem">\n'
        "    <dt>Goal</dt><dd>{goal}</dd>\n"
        "    <dt>Simulation start (t0)</dt><dd>{t0}</dd>\n"
        "    <dt>Run id</dt><dd><code>{run_id}</code></dd>\n"
        "    {diagnosis}\n"
        "  </dl>\n"
        "</div>"
    ).format(
        title=esc(scenario.title),
        sid=esc(scenario.id),
        mtype=esc(scenario.missionType),
        mark=mark_svg(ok),
        badge=status_badge(trial.status),
        goal=esc(_goal_of(scenario)),
        t0=esc(scenario.t0),
        run_id=esc(trial.runId),
        diagnosis=diagnosis,
    )


# ---------------------------------------------------------------------------
# Interaction timeline in sim time
# ---------------------------------------------------------------------------


def _actor_labels(scenario: Scenario) -> Dict[str, str]:
    """address / portal account -> counterparty actor label."""
    labels: Dict[str, str] = {}
    for cp in scenario.counterparties:
        for owned in cp.owns:
            labels[owned.lower()] = cp.actorId
    return labels


def _label_for(addr: str, labels: Dict[str, str]) -> str:
    hit = labels.get(addr.lower())
    return "{0} <{1}>".format(hit, addr) if hit else addr


def _message_entries(world: WorldBundle, labels: Dict[str, str]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for i, m in enumerate(world.messages):
        attachments = ""
        if m.attachments:
            attachments = ' <span class="sub">({0})</span>'.format(
                ", ".join(esc(a.name) for a in m.attachments)
            )
        if m.direction == "outbound":
            to_labels = ", ".join(_label_for(t, labels) for t in m.to)
            html = "Message sent — <strong>Agent → {0}</strong>: “{1}”{2}".format(
                esc(to_labels), esc(m.subject), attachments
            )
        else:
            html = "Message received — <strong>{0} → Agent</strong>: “{1}”{2}".format(
                esc(_label_for(m.from_, labels)), esc(m.subject), attachments
            )
        entries.append(
            {
                "ts": m.ts,
                "order": i,
                "kind": "sent" if m.direction == "outbound" else "received",
                "html": html,
            }
        )
    return entries


def _event_entries(events: List[Any]) -> List[Dict[str, Any]]:
    resolutions: Dict[str, Any] = {}
    for e in events:
        if e.type == "gate_resolved":
            resolutions[e.gateId] = e

    out: List[Dict[str, Any]] = []
    for i, e in enumerate(events):
        etype = e.type
        if etype == "mission_started":
            out.append(
                {"ts": e.ts, "order": i, "kind": "start", "html": "Mission started — {0}".format(esc(e.goal))}
            )
        elif etype == "gate_raised":
            r = resolutions.get(e.gateId)
            if r is not None:
                resolved = "<strong>{0}</strong> by <strong>{1}</strong>".format(
                    esc(r.resolution), esc(r.resolvedBy)
                )
                if r.reason:
                    resolved += " — “{0}”".format(esc(r.reason))
            else:
                resolved = '<span class="badge warn">unresolved</span>'
            step = ", step {0}".format(esc(e.stepTag)) if e.stepTag else ""
            later = (
                " · resolved {0} later".format(esc(_sim_offset_pair(e.ts, r.ts)))
                if r is not None
                else ""
            )
            out.append(
                {
                    "ts": e.ts,
                    "order": i,
                    "kind": "gate",
                    "html": (
                        "Gate raised (<code>{kind}</code>{step}) → {resolved}"
                        '<div class="sub">gate <code>{gate_id}</code>{later}</div>'
                    ).format(
                        kind=esc(e.kind),
                        step=step,
                        resolved=resolved,
                        gate_id=esc(e.gateId),
                        later=later,
                    ),
                }
            )
        elif etype == "steer":
            out.append(
                {
                    "ts": e.ts,
                    "order": i,
                    "kind": "steer",
                    "html": "Steer ({0}) from {1} — “{2}”".format(
                        esc(e.priority), esc(e.author), esc(clip(e.message, 300))
                    ),
                }
            )
        elif etype == "artifact_created":
            mime = " ({0})".format(esc(e.mime)) if e.mime else ""
            out.append(
                {
                    "ts": e.ts,
                    "order": i,
                    "kind": "artifact",
                    "html": (
                        "Artifact produced — <code>{tag}</code>{mime} "
                        '<span class="sub">{hash}</span>'
                    ).format(tag=esc(e.tag), mime=mime, hash=esc(e.hash[:12])),
                }
            )
        elif etype == "terminal_outcome":
            summary = " — {0}".format(esc(e.summary)) if e.summary else ""
            out.append(
                {
                    "ts": e.ts,
                    "order": i,
                    "kind": "terminal",
                    "html": "Mission {0} (judged by {1}){2}".format(
                        status_badge(e.status), esc(e.judgedBy), summary
                    ),
                }
            )
    return out


def _timeline(scenario: Scenario, events: List[Any], world: WorldBundle) -> str:
    labels = _actor_labels(scenario)
    entries = _message_entries(world, labels) + _event_entries(events)
    entries.sort(key=lambda e: (e["ts"], e["order"]))
    if not entries:
        return '\n<h2>Interaction timeline</h2>\n<p class="sub">No recorded interactions.</p>'
    rows = "\n".join(
        "<tr>\n"
        '  <td class="ts">{offset}<br><span class="sub">{ts}</span></td>\n'
        "  <td>{kind}</td>\n"
        "  <td>{html}</td>\n"
        "</tr>".format(
            offset=esc(_sim_offset(e["ts"], scenario.t0)),
            ts=esc(e["ts"]),
            kind=esc(e["kind"]),
            html=e["html"],
        )
        for e in entries
    )
    return (
        "\n<h2>Interaction timeline (sim time)</h2>\n"
        '<table class="timeline">\n'
        "<thead><tr><th>Sim time</th><th>Kind</th><th>What happened</th></tr></thead>\n"
        "<tbody>{rows}</tbody>\n"
        "</table>"
    ).format(rows=rows)


# ---------------------------------------------------------------------------
# Verification table
# ---------------------------------------------------------------------------


def _evidence_summary(v: Verdict) -> str:
    if not v.evidence:
        return '<span class="sub">—</span>'
    parts = []
    for e in v.evidence[:3]:
        kind = e.kind
        if kind == "event":
            note = " {0}".format(esc(e.note)) if e.note else ""
            parts.append(
                '<div class="evidence"><span class="ev-kind">event</span>'
                "<code>{0}</code>{1}</div>".format(esc(e.eventId), note)
            )
        elif kind == "artifact":
            excerpt = "<pre>{0}</pre>".format(esc(clip(e.excerpt, 400))) if e.excerpt else ""
            parts.append(
                '<div class="evidence"><span class="ev-kind">artifact</span>'
                "<code>{0}</code>{1}</div>".format(esc(e.hash[:12]), excerpt)
            )
        elif kind == "world":
            parts.append(
                '<div class="evidence"><span class="ev-kind">world</span>'
                "<code>{0}</code><pre>{1}</pre></div>".format(
                    esc(e.query),
                    esc(clip(json.dumps(e.result, default=str), 400)),
                )
            )
        elif kind == "judge_rationale":
            parts.append(
                '<div class="evidence"><span class="ev-kind">judge</span>{0}</div>'.format(
                    esc(clip(e.text, 400))
                )
            )
        else:  # note
            parts.append(
                '<div class="evidence"><span class="ev-kind">note</span>{0}</div>'.format(
                    esc(clip(e.text, 400))
                )
            )
    return "".join(parts)


def _verdict_row(scope: str, v: Verdict) -> str:
    flags = esc(v.class_)
    if v.advisory:
        flags += ' <span class="badge muted">advisory</span>'
    return (
        "<tr>\n"
        "  <td>{scope}</td>\n"
        "  <td><code>{grader}</code></td>\n"
        "  <td>{flags}</td>\n"
        "  <td>{status}</td>\n"
        '  <td class="num">{score}</td>\n'
        "  <td>{evidence}</td>\n"
        "</tr>".format(
            scope=scope,
            grader=esc(v.graderId),
            flags=flags,
            status=status_badge(v.status),
            score=fmt_num(v.score, 2),
            evidence=_evidence_summary(v),
        )
    )


def _verification(trial: TrialResult) -> str:
    rows: List[str] = []
    for v in trial.verdicts:
        rows.append(_verdict_row('<span class="sub">run</span>', v))
    for item in trial.items:
        for v in item.verdicts:
            rows.append(_verdict_row("<code>{0}</code>".format(esc(item.itemId)), v))
        if not item.verdicts:
            rows.append(
                '<tr><td><code>{item_id}</code></td><td class="sub" colspan="2">item</td>'
                '<td>{status}</td><td class="num">{q}</td>'
                '<td><span class="sub">—</span></td></tr>'.format(
                    item_id=esc(item.itemId),
                    status=status_badge(item.status),
                    q=fmt_num(item.q, 2),
                )
            )
    if not rows:
        return '\n<h2>Verification</h2>\n<p class="sub">No verdicts recorded for this trial.</p>'
    return (
        "\n<h2>Verification</h2>\n"
        "<table>\n"
        "<thead><tr><th>Scope</th><th>Check</th><th>Class</th><th>Status</th>"
        '<th class="num">Score</th><th>Evidence</th></tr></thead>\n'
        "<tbody>{rows}</tbody>\n"
        "</table>"
    ).format(rows="\n".join(rows))


# ---------------------------------------------------------------------------
# Cost + sim duration, sign-off
# ---------------------------------------------------------------------------


def _cost_block(trial: TrialResult) -> str:
    return (
        "\n<h2>Cost and duration</h2>\n"
        '<div class="panel"><dl class="kv">\n'
        "  <dt>Cost</dt><dd>{cost}</dd>\n"
        "  <dt>Simulated duration</dt><dd>{days} days</dd>\n"
        "  <dt>Wall-clock runtime</dt><dd>{wall:.1f}s</dd>\n"
        "</dl></div>"
    ).format(cost=fmt_usd(trial.costUsd), days=fmt_num(trial.simDays, 1), wall=trial.wallMs / 1000)


def _signoff() -> str:
    return (
        '\n<section class="signoff">\n'
        '  <h2 style="margin-top:0">Sign-off</h2>\n'
        '  <p class="sub">I have reviewed this rehearsal — including every gate resolution and the '
        "verification evidence above — and approve this routine for the certified configuration.</p>\n"
        '  <p>Name: <span class="line"></span> Role: <span class="line"></span></p>\n'
        '  <p>Date: <span class="line"></span> Signature: <span class="line"></span></p>\n'
        "</section>"
    )
