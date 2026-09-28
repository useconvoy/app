"""Consume the planner's public JSON injection before entering its model owner."""
import os
import sys
import tempfile
from pathlib import Path

from execution_keys import EXECUTION_ENV, materialize_execution_keys


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"inspect", "serve"}:
        raise ValueError("choose inspect or serve")
    if sys.argv[1] == "serve":
        directory = Path(tempfile.mkdtemp(prefix="convoy-planner-verification-"))
        os.environ.update(materialize_execution_keys("planner", directory))
    elif EXECUTION_ENV.intersection(os.environ):
        raise ValueError("inspection does not require execution credentials")
    os.execv(sys.executable, [sys.executable, "-m", "convoy_planner.owned", *sys.argv[1:]])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        raise SystemExit("Planner container configuration is unavailable") from None
