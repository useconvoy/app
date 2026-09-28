"""Assemble a verified RDS connection inside the task; never in Terraform state."""
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import URL

sys.path.insert(0, "/app/runtime")
from execution_keys import materialize_execution_keys


def configure_database():
    password = os.environ.pop("CONVOY_DB_PASSWORD")
    os.environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg", username=os.environ["PGUSER"], password=password,
        host=os.environ["PGHOST"], port=5432, database=os.environ["PGDATABASE"],
        query={"sslmode": "verify-full", "sslrootcert": str(Path(__file__).with_name("rds-ca.pem"))},
    ).render_as_string(hide_password=False)


def configure_execution(role):
    if role == "api":
        # Keep the private directory for the exec'd API's lifetime. ECS discards
        # its ephemeral filesystem when the task ends; no shared mount is used.
        directory = Path(tempfile.mkdtemp(prefix="convoy-signing-"))
        os.environ.update(materialize_execution_keys("api", directory))
    elif any(name in os.environ for name in (
        "CONVOY_EXECUTION_SIGNING_JSON", "CONVOY_ACTION_VERIFICATION_JSON", "CONVOY_PLANNER_VERIFICATION_JSON",
        "CONVOY_EXECUTION_SIGNING_KEYS_FILE", "CONVOY_ACTION_VERIFICATION_KEYS_FILE",
        "CONVOY_PLANNER_VERIFICATION_KEYS_FILE", "CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET",
    )):
        raise ValueError("execution keys are unavailable to this task role")


def main():
    role = sys.argv[1]
    configure_execution(role)
    configure_database()
    commands = {
        "api": ["uvicorn", "hosted:app", "--factory", "--app-dir", str(Path(__file__).parent),
                "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"],
        "scheduler": ["convoy-server", "worker"],
        "scheduler-health": ["convoy-server", "worker-health"],
        "evaluations": ["python", "-m", "convoy_server.evaluation_worker"],
        "migrate": ["convoy-server", "migrate"],
    }
    command = commands[role]
    os.execvp(command[0], command)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError):
        raise SystemExit("AWS task configuration is unavailable") from None
