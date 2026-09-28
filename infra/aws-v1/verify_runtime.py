"""Local Docker qualification only; creates no AWS resources or host DB listener."""
import argparse
import secrets
import subprocess
import time
import uuid
from pathlib import Path

POSTGRES = "postgres:17-bookworm@sha256:639ab7ceb90e13123085b741fb31ef493fba25463002f6da665352e7b534b652"


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-image", required=True, help="locally built AWS API wrapper image")
    args = parser.parse_args()
    name = "convoy-aws-check-" + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    network = container = False
    try:
        run(["docker", "network", "create", "--internal", name], capture_output=True)
        network = True
        run(["docker", "run", "--rm", "-d", "--name", name, "--network", name,
             "--network-alias", "postgres", "--tmpfs", "/var/lib/postgresql/data",
             "-e", "POSTGRES_USER=convoy_admin", "-e", "POSTGRES_DB=convoy",
             "-e", "POSTGRES_PASSWORD=" + password, POSTGRES], capture_output=True)
        container = True
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "convoy_admin", "-d", "convoy"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("disposable PostgreSQL did not become ready")
        script = Path(__file__).with_name("tests").joinpath("runtime_check.py").read_text()
        run(["docker", "run", "--rm", "-i", "--network", name,
             "-e", "TEST_ADMIN_PASSWORD=" + password, "--entrypoint", "python", args.api_image, "-"], input=script)
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if network:
            run(["docker", "network", "rm", name], capture_output=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Local container check failed (exit {error.returncode}); see the preceding diagnostic.") from None
