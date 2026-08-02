"""suite_report.py — ONE self-contained HTML file for a SuiteResult. Port of
src/reports/suite-report.ts: header with the green verdict, scenario matrix,
per-gauntlet inline-SVG Q(n) charts, failed-verdict drill-down with rendered
evidence, and a cost summary ($ per verified item).

SuiteResult does not carry the eval-set thresholds, so the chart's item floor
comes in as an argument (the CLI passes config.thresholds.itemFloor); without
it the threshold line defaults to 0.5.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, List, Tuple

from convoy_evals.reports import (
    clip,
    esc,
    fmt_num,
    fmt_short,
    fmt_signed,
    fmt_usd,
    mark_svg,
    page,
    status_badge,
)
from convoy_evals.schema.verdict import ScenarioVerdict, SuiteResult, Verdict


def render_suite_report(result: SuiteResult, item_floor: float = 0.5) -> str:
    body = "\n".join(
        [
            _header(result),
            _matrix(result),
            _gauntlet_charts(result, item_floor),
            _drilldown(result),
            _cost_summary(result),
        ]
    )
    return page(
        "Suite report — {0}@{1}".format(result.evalSet.name, result.evalSet.version), body
    )


def write_suite_report(result: SuiteResult, path: str, item_floor: float = 0.5) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_suite_report(result, item_floor=item_floor))


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _header(result: SuiteResult) -> str:
    detail = ""
    if result.greenDetail:
        detail = '<ul class="detail">{0}</ul>'.format(
            "".join("<li>{0}</li>".format(esc(d)) for d in result.greenDetail)
        )
    model_part = ", {0}".format(esc(result.subject.modelId)) if result.subject.modelId else ""
    headline = (
        "GREEN — suite passes" if result.green else "NOT GREEN — suite fails"
    )
    return (
        "\n<h1>Eval suite report</h1>\n"
        '<p class="sub">{name}@{version} · subject: <code>{label}</code> ({kind}{model}) ·\n'
        "{started} → {finished}</p>\n"
        '<div class="panel">\n'
        '  <div class="headline">{mark} {headline}</div>\n'
        "  {detail}\n"
        "</div>"
    ).format(
        name=esc(result.evalSet.name),
        version=esc(result.evalSet.version),
        label=esc(result.subject.label),
        kind=esc(result.subject.kind),
        model=model_part,
        started=esc(result.startedAt),
        finished=esc(result.finishedAt),
        mark=mark_svg(result.green),
        headline=headline,
        detail=detail,
    )


def _matrix(result: SuiteResult) -> str:
    rows = []
    for s in result.scenarios:
        d = s.meanDecay
        invariants = (
            '<span class="badge bad">violated</span>'
            if s.invariantViolation
            else '<span class="badge ok">held</span>'
        )
        rows.append(
            "<tr>\n"
            "  <td><code>{sid}</code></td>\n"
            "  <td>{status}</td>\n"
            "  <td>{invariants}</td>\n"
            '  <td class="num">{trials}</td>\n'
            '  <td class="num">{cost}</td>\n'
            '  <td class="num">{slope}</td>\n'
            '  <td class="num">{auc}</td>\n'
            '  <td class="num">{minq}</td>\n'
            "  <td>{detail}</td>\n"
            "</tr>".format(
                sid=esc(s.scenarioId),
                status=status_badge(s.status),
                invariants=invariants,
                trials=len(s.trials),
                cost=fmt_usd(s.totalCostUsd),
                slope=fmt_signed(d.slope) if d else "—",
                auc=fmt_num(d.auc, 2) if d else "—",
                minq=fmt_num(d.minQ, 2) if d else "—",
                detail=esc(s.passDetail),
            )
        )
    return (
        "\n<h2>Scenario matrix</h2>\n"
        "<table>\n"
        '<thead><tr><th>Scenario</th><th>Status</th><th>Invariants</th><th class="num">Trials</th>'
        '<th class="num">Cost</th><th class="num">Slope</th><th class="num">AUC</th>'
        '<th class="num">Min Q</th><th>Detail</th></tr></thead>\n'
        "<tbody>{rows}</tbody>\n"
        "</table>"
    ).format(rows="\n".join(rows))


# ---------------------------------------------------------------------------
# Q(n) decay chart — one per scenario that produced item verdicts.
# ---------------------------------------------------------------------------


def _mean_q_by_ordinal(s: ScenarioVerdict) -> List[Tuple[int, float]]:
    by_ordinal = {}
    for trial in s.trials:
        for item in trial.items:
            by_ordinal.setdefault(item.ordinal, []).append(item.q)
    return [
        (ordinal, sum(qs) / len(qs)) for ordinal, qs in sorted(by_ordinal.items())
    ]


def _q_chart_svg(points: List[Tuple[int, float]], item_floor: float) -> str:
    w = 640
    h = 220
    ml = 44  # left margin (y labels)
    mr = 16
    mt = 14
    mb = 30  # bottom margin (x labels)
    plot_w = w - ml - mr
    plot_h = h - mt - mb
    min_ord = points[0][0] if points else 1
    max_ord = points[-1][0] if points else 1
    span = max(1, max_ord - min_ord)

    def x(ordinal: float) -> float:
        return ml + ((ordinal - min_ord) / span) * plot_w

    def y(q: float) -> float:
        return mt + (1 - q) * plot_h

    grid = "".join(
        '<line x1="{ml}" y1="{gy}" x2="{x2}" y2="{gy}" stroke="var(--border)" stroke-width="1"/>'
        '<text x="{lx}" y="{ly}" text-anchor="end" font-size="10" fill="var(--fg-dim)">{label}</text>'.format(
            ml=ml, gy=y(g), x2=w - mr, lx=ml - 8, ly=y(g) + 4, label=fmt_short(g)
        )
        for g in (0, 0.25, 0.5, 0.75, 1)
    )

    label_every = max(1, math.ceil(len(points) / 12)) if points else 1
    x_labels = "".join(
        '<text x="{px}" y="{py}" text-anchor="middle" font-size="10" fill="var(--fg-dim)">{ordinal}</text>'.format(
            px=x(p[0]), py=h - 10, ordinal=p[0]
        )
        for i, p in enumerate(points)
        if i % label_every == 0 or i == len(points) - 1
    )

    threshold = (
        '<line x1="{ml}" y1="{ty}" x2="{x2}" y2="{ty}" stroke="var(--warn)" stroke-width="1" '
        'stroke-dasharray="5 4"/>'
        '<text x="{x2}" y="{ly}" text-anchor="end" font-size="10" fill="var(--warn)">'
        "item floor {floor}</text>".format(
            ml=ml, ty=y(item_floor), x2=w - mr, ly=y(item_floor) - 5, floor=fmt_short(item_floor)
        )
    )

    polyline = ""
    if len(points) > 1:
        polyline = '<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2"/>'.format(
            pts=" ".join("{0:.1f},{1:.1f}".format(x(p[0]), y(p[1])) for p in points)
        )

    dots = "".join(
        '<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{fill}">'
        "<title>item ordinal {ordinal}: mean Q {q:.3f}</title></circle>".format(
            cx=x(p[0]),
            cy=y(p[1]),
            fill="var(--ok)" if p[1] >= item_floor else "var(--bad)",
            ordinal=p[0],
            q=p[1],
        )
        for p in points
    )

    return (
        '<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="Q by item ordinal">\n'
        '<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="none" stroke="var(--border)"/>\n'
        "{grid}\n{threshold}\n{polyline}\n{dots}\n{x_labels}\n"
        '<text x="{ml}" y="{lby}" font-size="10" fill="var(--fg-dim)">item ordinal n →</text>\n'
        "</svg>"
    ).format(
        w=w,
        h=h,
        ml=ml,
        mt=mt,
        pw=plot_w,
        ph=plot_h,
        grid=grid,
        threshold=threshold,
        polyline=polyline,
        dots=dots,
        x_labels=x_labels,
        lby=h - 10,
    )


def _gauntlet_charts(result: SuiteResult, item_floor: float) -> str:
    sections = []
    for s in result.scenarios:
        points = _mean_q_by_ordinal(s)
        if not points:
            continue
        d = s.meanDecay
        if d:
            stat_line = (
                "slope {slope} · auc {auc} · first-decile {fd} → last-decile {ld} · "
                "min Q {minq} over {count} items".format(
                    slope=fmt_signed(d.slope),
                    auc=fmt_num(d.auc, 3),
                    fd=fmt_num(d.firstDecileMean, 2),
                    ld=fmt_num(d.lastDecileMean, 2),
                    minq=fmt_num(d.minQ, 2),
                    count=d.itemCount,
                )
            )
        else:
            stat_line = "{0} items".format(len(points))
        sections.append(
            '<div class="panel">\n'
            "<h3><code>{sid}</code> — mean Q(n) across {trials} trial(s)</h3>\n"
            '<p class="sub">{stat}</p>\n'
            '<div class="chart-wrap">{chart}</div>\n'
            "</div>".format(
                sid=esc(s.scenarioId),
                trials=len(s.trials),
                stat=esc(stat_line),
                chart=_q_chart_svg(points, item_floor),
            )
        )
    if not sections:
        return ""
    return "\n<h2>Quality decay Q(n)</h2>\n" + "\n".join(sections)


# ---------------------------------------------------------------------------
# Failed-verdict drill-down
# ---------------------------------------------------------------------------


def _flagged_verdicts(s: ScenarioVerdict) -> List[Tuple[int, Any, Verdict]]:
    """[(trial_idx, item_id_or_None, verdict), ...] for fail/error/missing."""
    out: List[Tuple[int, Any, Verdict]] = []
    for trial in s.trials:
        for v in trial.verdicts:
            if v.status in ("fail", "error", "missing"):
                out.append((trial.trialIdx, None, v))
        for item in trial.items:
            for v in item.verdicts:
                if v.status in ("fail", "error", "missing"):
                    out.append((trial.trialIdx, item.itemId, v))
    return out


def render_evidence(e: Any) -> str:
    kind = e.kind
    if kind == "event":
        note = " — {0}".format(esc(e.note)) if e.note else ""
        return (
            '<div class="evidence"><span class="ev-kind">event</span>'
            "<code>{0}</code>{1}</div>".format(esc(e.eventId), note)
        )
    if kind == "artifact":
        excerpt = "<pre>{0}</pre>".format(esc(clip(e.excerpt))) if e.excerpt else ""
        return (
            '<div class="evidence"><span class="ev-kind">artifact</span>'
            "<code>{0}</code>{1}</div>".format(esc(e.hash[:16]), excerpt)
        )
    if kind == "world":
        return (
            '<div class="evidence"><span class="ev-kind">world query</span>'
            "<code>{0}</code><pre>{1}</pre></div>".format(
                esc(e.query), esc(clip(json.dumps(e.result, indent=2, default=str)))
            )
        )
    if kind == "judge_rationale":
        return (
            '<div class="evidence"><span class="ev-kind">judge sample {0}</span>'
            "<pre>{1}</pre></div>".format(e.sampleIdx, esc(clip(e.text)))
        )
    # note
    return '<div class="evidence"><span class="ev-kind">note</span>{0}</div>'.format(esc(e.text))


def _drilldown(result: SuiteResult) -> str:
    sections = []
    for s in result.scenarios:
        flagged = _flagged_verdicts(s)
        if not flagged:
            continue
        rows = []
        for trial_idx, item_id, v in flagged:
            flags = esc(v.class_)
            if v.advisory:
                flags += ' <span class="badge muted">advisory</span>'
            if v.lowConfidence:
                flags += ' <span class="badge warn">low-conf</span>'
            evidence = "".join(render_evidence(e) for e in v.evidence) or '<span class="sub">—</span>'
            rows.append(
                "<tr>\n"
                '  <td class="num">{trial_idx}</td>\n'
                "  <td>{item}</td>\n"
                '  <td><code>{grader}</code><br><span class="sub">{version}</span></td>\n'
                "  <td>{flags}</td>\n"
                "  <td>{status}</td>\n"
                '  <td class="num">{score}</td>\n'
                "  <td>{evidence}</td>\n"
                "</tr>".format(
                    trial_idx=trial_idx,
                    item="<code>{0}</code>".format(esc(item_id))
                    if item_id
                    else '<span class="sub">run</span>',
                    grader=esc(v.graderId),
                    version=esc(v.graderVersion[:16]),
                    flags=flags,
                    status=status_badge(v.status),
                    score=fmt_num(v.score, 2),
                    evidence=evidence,
                )
            )
        sections.append(
            "<h3><code>{sid}</code> — {count} failed/errored verdict(s)</h3>\n"
            "<table>\n"
            '<thead><tr><th class="num">Trial</th><th>Item</th><th>Grader</th><th>Class</th>'
            '<th>Status</th><th class="num">Score</th><th>Evidence</th></tr></thead>\n'
            "<tbody>{rows}</tbody>\n"
            "</table>".format(sid=esc(s.scenarioId), count=len(flagged), rows="\n".join(rows))
        )
    if not sections:
        return '\n<h2>Failed verdicts</h2>\n<p class="sub">None — every verdict passed.</p>'
    return "\n<h2>Failed verdicts</h2>\n" + "\n".join(sections)


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------


def _cost_summary(result: SuiteResult) -> str:
    passed_items = 0
    total_items = 0
    for s in result.scenarios:
        for trial in s.trials:
            for item in trial.items:
                total_items += 1
                if item.status == "pass":
                    passed_items += 1
    if passed_items > 0:
        per_verified = fmt_usd(result.totalCostUsd / passed_items)
    else:
        per_verified = '<span class="sub">n/a (no passed items)</span>'
    return (
        "\n<h2>Cost</h2>\n"
        '<div class="panel"><dl class="kv">\n'
        "  <dt>Total cost</dt><dd>{total}</dd>\n"
        "  <dt>Verified (passed) items</dt><dd>{passed} of {all}</dd>\n"
        "  <dt>$ per verified item</dt><dd>{per}</dd>\n"
        "</dl></div>"
    ).format(
        total=fmt_usd(result.totalCostUsd),
        passed=passed_items,
        all=total_items,
        per=per_verified,
    )
