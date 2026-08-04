"""AST lint test enforcing CLAUDE.md rule 13 / TESTING.md section 4.4.

Inside `src/convoy_runtime/workflows/`: no `workflow.now()`, no
`datetime.now()`, no raw timers (`workflow.sleep` / `asyncio.sleep`), no
`random`, no `os.environ`, and no I/O imports. Time goes through `RunClock`;
everything real goes through activities.
"""

import ast
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "src" / "convoy_runtime" / "workflows"

# Modules whose import into workflow code means I/O, nondeterminism, or env access.
BANNED_IMPORTS = {
    "os",
    "sys",
    "io",
    "time",
    "random",
    "secrets",
    "uuid",
    "socket",
    "ssl",
    "subprocess",
    "shutil",
    "pathlib",
    "tempfile",
    "urllib",
    "http",
    "httpx",
    "requests",
    "aiohttp",
    "boto3",
    "botocore",
    "psycopg",
    "psycopg_pool",
    "sqlalchemy",
}

# Attribute calls that reach for time, randomness, or the environment directly.
BANNED_ATTR_CALLS = {
    ("workflow", "now"),
    ("workflow", "time"),
    ("workflow", "time_ns"),
    ("workflow", "sleep"),
    ("workflow", "random"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "sleep"),
    ("time", "monotonic"),
    ("asyncio", "sleep"),
    ("asyncio", "wait_for"),
    ("random", "random"),
    ("random", "randint"),
    ("random", "choice"),
    ("os", "getenv"),
    ("os", "environ"),
}

BANNED_NAME_CALLS = {"open", "input", "exec", "eval"}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []

    def report(node: ast.AST, message: str) -> None:
        found.append(f"{path.name}:{getattr(node, 'lineno', '?')}: {message}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_IMPORTS:
                    report(node, f"banned import '{alias.name}' in workflow code")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_IMPORTS:
                report(node, f"banned import 'from {node.module}' in workflow code")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_NAME_CALLS:
                report(node, f"banned call '{func.id}()' in workflow code")
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                if pair in BANNED_ATTR_CALLS:
                    report(node, f"banned call '{pair[0]}.{pair[1]}()' - use RunClock/activities")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (node.value.id, node.attr) == ("os", "environ")
        ):
            report(node, "banned access 'os.environ' in workflow code")

    return found


def test_workflows_dir_exists() -> None:
    assert WORKFLOWS_DIR.is_dir(), f"missing workflows dir: {WORKFLOWS_DIR}"
    assert list(WORKFLOWS_DIR.glob("*.py")), "workflows dir has no modules to lint"


def test_no_forbidden_time_io_or_randomness_in_workflows() -> None:
    violations = [v for path in WORKFLOWS_DIR.rglob("*.py") for v in _violations(path)]
    assert not violations, "workflow determinism violations:\n" + "\n".join(violations)


def test_lint_catches_known_violations(tmp_path: Path) -> None:
    """Self-check: the linter actually flags each banned pattern."""
    bad = tmp_path / "bad_workflow.py"
    bad.write_text(
        "import os\n"
        "import random\n"
        "from datetime import datetime\n"
        "import asyncio\n"
        "from temporalio import workflow\n"
        "async def f():\n"
        "    workflow.now()\n"
        "    datetime.now()\n"
        "    await workflow.sleep(1)\n"
        "    await asyncio.sleep(1)\n"
        "    random.random()\n"
        "    os.environ['X']\n"
        "    open('f')\n"
    )
    found = _violations(bad)
    assert len(found) >= 9
