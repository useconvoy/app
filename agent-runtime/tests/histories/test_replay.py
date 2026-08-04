"""Replay every checked-in history against current workflow code — merge
blocking: a change that breaks replay would also break live runs mid-flight."""

from pathlib import Path

import anyio
import pytest
from _support.common import build_data_converter
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from convoy_runtime.workflows.agent_run import AgentRunWorkflow
from convoy_runtime.workflows.subagent import SubagentWorkflow

pytestmark = pytest.mark.anyio

HISTORIES_DIR = Path(__file__).resolve().parent
HISTORY_FILES = sorted(HISTORIES_DIR.glob("*.json"))


def test_at_least_one_history_is_checked_in() -> None:
    assert HISTORY_FILES, "no checked-in replay histories under tests/histories/"


def test_a_fanout_history_is_checked_in() -> None:
    assert any("fanout" in path.stem for path in HISTORY_FILES), (
        "no replay history exercises a fan-out group"
    )


@pytest.mark.parametrize("history_path", HISTORY_FILES, ids=lambda p: p.stem)
async def test_replay_checked_in_history(history_path: Path) -> None:
    text = await anyio.Path(history_path).read_text()
    history = WorkflowHistory.from_json(history_path.stem, text)
    replayer = Replayer(
        workflows=[AgentRunWorkflow, SubagentWorkflow],
        data_converter=build_data_converter(),
    )
    await replayer.replay_workflow(history)
