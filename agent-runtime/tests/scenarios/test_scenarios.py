"""Executes every scenario file in the time-skipping environment (fast lane)."""

from pathlib import Path

import pytest

from .runner import CASES_DIR, assert_scenario, load_scenario, run_scenario

pytestmark = pytest.mark.anyio

CASE_FILES = sorted(CASES_DIR.glob("*.yaml"))


def test_scenarios_exist() -> None:
    assert CASE_FILES, f"no scenario files under {CASES_DIR}"


@pytest.mark.parametrize("case_path", CASE_FILES, ids=lambda p: p.stem)
async def test_scenario(case_path: Path) -> None:
    scenario = load_scenario(case_path)
    result, fake = await run_scenario(scenario)
    assert_scenario(scenario, result, fake)
