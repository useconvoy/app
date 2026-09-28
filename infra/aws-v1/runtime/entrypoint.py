"""Assemble a verified RDS connection inside the task; never in Terraform state."""
import os
import sys
from pathlib import Path

from sqlalchemy.engine import URL


def configure_database():
    password = os.environ.pop("CONVOY_DB_PASSWORD")
    os.environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg", username=os.environ["PGUSER"], password=password,
        host=os.environ["PGHOST"], port=5432, database=os.environ["PGDATABASE"],
        query={"sslmode": "verify-full", "sslrootcert": str(Path(__file__).with_name("rds-ca.pem"))},
    ).render_as_string(hide_password=False)


def main():
    role = sys.argv[1]
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
    main()
