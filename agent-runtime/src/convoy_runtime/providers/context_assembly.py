"""Pinned-header assembly: the always-present top of every turn's context.

Rebuilt fresh from `RunState` before each turn so the model always sees the
current goal, success criteria, a compact plan render, live budget status, and
the steers being delivered this turn. Pure functions here; the activity layer
owns writing the result to the artifact store.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from convoy_core import RunState


def _fraction(spent: Decimal, reserved: Decimal, cap: Decimal) -> str | None:
    if cap <= 0:
        return None
    fraction = (spent + reserved) / cap
    return str(fraction.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def build_pinned_header(state: RunState) -> dict[str, Any]:
    """Deterministic header dict for a run's next turn. Golden-tested: the
    exact shape is part of the executor contract."""
    plan = state.plan
    budget = state.budget
    remaining = budget.cap_usd - budget.spent_usd - budget.reserved_usd
    return {
        "run_id": state.run_id,
        "turn": state.turn_count + 1,
        "goal": plan.goal if plan else "",
        "success_criteria": list(plan.success_criteria) if plan else [],
        "plan": [
            {
                "id": step.id,
                "description": step.description,
                "status": step.status,
                "attempt": step.attempt,
            }
            for step in (plan.steps if plan else [])
        ],
        "budget": {
            "cap_usd": str(budget.cap_usd),
            "spent_usd": str(budget.spent_usd),
            "reserved_usd": str(budget.reserved_usd),
            "remaining_usd": str(remaining),
            "spent_fraction": _fraction(budget.spent_usd, budget.reserved_usd, budget.cap_usd),
        },
        # Steers pending delivery into this turn. Populated once the steer
        # mailbox drains; always present so the header shape is stable.
        "steers": [
            {
                "id": steer.id,
                "mode": steer.mode,
                "author": steer.author,
                "author_id": steer.author_id,
                "body": steer.body,
            }
            for steer in state.pending_steers
        ],
    }


def render_pinned_header(header: dict[str, Any]) -> str:
    """Plain-text render of the header for the model's system context."""
    lines: list[str] = [
        f"Goal: {header['goal']}",
        "Success criteria:",
    ]
    criteria: list[str] = list(header["success_criteria"])
    if criteria:
        lines.extend(f"- {criterion}" for criterion in criteria)
    else:
        lines.append("- (none stated)")
    lines.append("Plan:")
    steps: list[dict[str, Any]] = list(header["plan"])
    for step in steps:
        lines.append(f"- [{step['status']}] {step['id']}: {step['description']}")
    budget: dict[str, Any] = header["budget"]
    lines.append(
        f"Budget: spent {budget['spent_usd']} of {budget['cap_usd']} USD "
        f"(remaining {budget['remaining_usd']})"
    )
    steers: list[dict[str, Any]] = list(header["steers"])
    if steers:
        lines.append("Steers delivered this turn:")
        for steer in steers:
            lines.append(f"- ({steer['mode']}) {steer['author_id']}: {steer['body']}")
    return "\n".join(lines)
