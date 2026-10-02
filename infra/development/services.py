"""Reproducible local service deployment; requires Docker Compose and OpenSSL."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_STATE = HERE / ".services"
DEFAULT_PROJECT = "convoy-v1-services"
STATE, PROJECT = DEFAULT_STATE, DEFAULT_PROJECT
AUTH_MODE = "public-key-v1"
CONFIG_KEYS = {"WEB_PORT", "API_PORT", "POSTGRES_PASSWORD", "CONVOY_ADMIN_EMAIL", "CONVOY_ADMIN_PASSWORD",
               "CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET", "CONVOY_WORKER_PROBE_TOKEN",
               "API_IMAGE", "WEB_IMAGE", "REFERENCE_IMAGE", "STACK_AUTH_MODE", "STACK_PROJECT", "IMAGE_PREFIX"}


class InstallationError(ValueError):
    pass


def environment_values():
    """Read only this helper's generated, single-quoted environment format."""
    values = {}
    for line in (STATE / ".env").read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z_]+", key) or len(value) < 2 or value[0] != "'" or value[-1] != "'":
            raise InstallationError("Installation environment is malformed; preserve it and use a new empty state directory.")
        if key in values:
            raise InstallationError("Installation environment has duplicate settings.")
        values[key] = value[1:-1]
    return values


def validate_installation(*, require_public, image_prefix=None):
    values = environment_values()
    recorded = values.get("STACK_PROJECT", DEFAULT_PROJECT)
    if recorded != PROJECT:
        raise InstallationError("This state directory belongs to a different Compose project; use its original --project.")
    if require_public:
        if (values.get("STACK_AUTH_MODE") != AUTH_MODE or "CONVOY_EXECUTION_SECRET" in values
                or "CONVOY_PLANNER_EXECUTION_SECRET" in values):
            raise InstallationError(
                "This retained installation uses legacy shared signing credentials. It has not been changed. "
                "Use a new --project, empty --state-dir, and unused --web-port/--api-port for public-key hosting. "
                "Existing data has no automatic credential migration; status/logs/down remain available.")
        if image_prefix is not None and values.get("IMAGE_PREFIX") != image_prefix:
            raise InstallationError("This installation already has different image tags; omit --image-prefix to reuse them.")
        if not all(values.get(name) for name in ("API_IMAGE", "WEB_IMAGE", "REFERENCE_IMAGE")):
            raise InstallationError("Installation image settings are incomplete.")
    return values


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def private_json(path, content):
    with path.open("w") as stream:
        os.chmod(path, 0o600)
        json.dump(content, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def require_unused_project():
    # Compose names are global to the Docker host, even across different checkouts.
    # Without the original private state, existing containers/volumes are not ours
    # to reconfigure or adopt with freshly generated credentials.
    for resource in ("container", "volume"):
        command = ["docker", resource, "ls", *(["--all"] if resource == "container" else []),
                   "--filter", f"label=com.docker.compose.project={PROJECT}", "--quiet"]
        if run(command, capture_output=True).stdout.strip():
            raise InstallationError(
                "This Docker project already has containers or retained volumes, but its original state is missing. "
                "Use the original --state-dir or choose a new --project; existing resources were not changed.")


def initialize(web_port, api_port, image_prefix=None):
    if (STATE / ".env").exists():
        validate_installation(require_public=True, image_prefix=image_prefix)
        return
    if STATE.exists() and any(STATE.iterdir()):
        raise InstallationError("State directory contains files but no complete installation; preserve it and choose an empty directory.")
    STATE.mkdir(mode=0o700, exist_ok=True, parents=True)
    STATE.chmod(0o700)
    tls = STATE / "tls"
    tls.mkdir(mode=0o700, exist_ok=True)

    def openssl(*args):
        run(["openssl", *map(str, args)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj",
            "/CN=Convoy local development CA", "-keyout", tls / "ca.key", "-out", tls / "ca.crt",
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    (tls / "ca.key").chmod(0o600)
    (tls / "ca.crt").chmod(0o444)
    for name in ("api", "inference"):
        directory = tls / name
        directory.mkdir(mode=0o755, exist_ok=True)
        extensions = directory / "extensions.cnf"
        extensions.write_text(f"subjectAltName=DNS:{name},DNS:localhost,IP:127.0.0.1\n"
                              "basicConstraints=critical,CA:FALSE\n"
                              "keyUsage=critical,digitalSignature,keyEncipherment\n"
                              "extendedKeyUsage=serverAuth\n")
        openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
                "-keyout", directory / "service.key", "-out", directory / "service.csr")
        openssl("x509", "-req", "-in", directory / "service.csr", "-CA", tls / "ca.crt",
                "-CAkey", tls / "ca.key", "-CAserial", tls / "ca.srl", "-CAcreateserial", "-days", "30", "-sha256",
                "-extfile", extensions, "-out", directory / "service.crt")
        (directory / "service.key").chmod(0o444)
        (directory / "service.crt").chmod(0o444)
        (directory / "service.csr").unlink()
        extensions.unlink()
    credentials = {"email": "developer@convoy.local", "password": secrets.token_urlsafe(32),
                   "console_url": f"http://localhost:{web_port}/app", "api_url": f"https://localhost:{api_port}",
                   "ca_file": str(tls / "ca.crt")}
    private_json(STATE / "connection.json", credentials)
    prefix = image_prefix or ("convoy-v1" if PROJECT == DEFAULT_PROJECT else PROJECT)
    values = {"STACK_AUTH_MODE": AUTH_MODE, "STACK_PROJECT": PROJECT, "IMAGE_PREFIX": prefix,
              "API_IMAGE": f"{prefix}-api:local", "WEB_IMAGE": f"{prefix}-web:local",
              "REFERENCE_IMAGE": f"{prefix}-reference:local",
              "WEB_PORT": str(web_port), "API_PORT": str(api_port),
              "POSTGRES_PASSWORD": secrets.token_urlsafe(32), "CONVOY_ADMIN_EMAIL": credentials["email"],
              "CONVOY_ADMIN_PASSWORD": credentials["password"],
              "CONVOY_WORKER_PROBE_TOKEN": secrets.token_urlsafe(48)}
    # Single quotes prevent Compose variable interpolation in generated values.
    env_file = STATE / ".env"
    env_file.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()))
    env_file.chmod(0o600)


def compose(*args, capture=False, log=None, stdin=None):
    command = ["docker", "compose", "-p", PROJECT, "--env-file", str(STATE / ".env"),
               "-f", str(HERE / "services.compose.yml"), *args]
    # Shell variables take precedence over --env-file in Compose. Do not let a
    # different development installation silently override these credentials.
    environment = {key: value for key, value in os.environ.items() if key not in CONFIG_KEYS}
    if log:
        with log.open("w") as stream:
            return run(command, stdout=stream, stderr=subprocess.STDOUT, env=environment)
    return run(command, capture_output=capture, input=stdin, env=environment)


def initialize_execution_keys():
    receipt = STATE / "execution-keys.json"
    args = ["run", "--rm", "--no-deps", "-T", "execution-keys", "python", "/app/runtime/execution_keys.py",
            "initialize", "--signing-file", "/run/execution-signing/keys.json",
            "--action-verification-file", "/run/action-verification/keys.json", "--issuer", "convoy-local-services"]
    established = receipt.exists() or (STATE / "installation.json").exists()
    if receipt.exists():
        value = json.loads(receipt.read_text())
        if value != {"schema_version": 1, "project": PROJECT, "auth_mode": AUTH_MODE}:
            raise InstallationError("Execution-key provisioning receipt differs from this installation.")
    if established:
        args.append("--require-existing")
    compose(*args, capture=True)
    # Persist before any API/worker starts. A later loss of both key volumes must
    # fail, rather than silently mint replacement authority for retained missions.
    private_json(receipt, {"schema_version": 1, "project": PROJECT, "auth_mode": AUTH_MODE})
    descriptor = os.open(STATE, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def image_evidence():
    result = {}
    configuration = validate_installation(require_public=True)
    for image in (configuration[name] for name in ("API_IMAGE", "WEB_IMAGE", "REFERENCE_IMAGE")):
        details = json.loads(run(["docker", "image", "inspect", image], capture_output=True).stdout)[0]
        result[image] = {"id": details["Id"], "architecture": details["Architecture"]}
    result["source_commit"] = run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True).stdout.strip()
    result["source_dirty"] = bool(run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True).stdout)
    return result


def main():
    global STATE, PROJECT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "verify", "status", "logs", "down"])
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Compose project owning this installation")
    parser.add_argument("--state-dir", type=Path, help="private installation directory; required with a nondefault project")
    parser.add_argument("--image-prefix", help="local image tag prefix, used only on first up")
    parser.add_argument("--web-port", type=int, default=3300, help="used only on first up")
    parser.add_argument("--api-port", type=int, default=8443, help="used only on first up")
    parser.add_argument("--skip-build", action="store_true", help="reuse existing local images")
    parser.add_argument("--delete-data", action="store_true", help="with down: remove this setup's volumes and secrets")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project):
        parser.error("project must contain 1 through 63 lowercase letters, digits, underscores or hyphens")
    if args.project != DEFAULT_PROJECT and args.state_dir is None:
        parser.error("a separate --project requires its own explicit --state-dir")
    if args.image_prefix is not None and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", args.image_prefix):
        parser.error("image prefix must be a simple lowercase local image name")
    STATE = args.state_dir.resolve() if args.state_dir is not None else DEFAULT_STATE
    PROJECT = args.project
    if not 1024 <= args.web_port <= 65535 or not 1024 <= args.api_port <= 65535:
        parser.error("choose unprivileged ports from 1024 through 65535")
    if args.delete_data and args.action != "down":
        parser.error("--delete-data requires down")
    if args.action == "up":
        if not (STATE / ".env").exists():
            require_unused_project()
        initialize(args.web_port, args.api_port, args.image_prefix)
        if not args.skip_build:
            # Sequential builds keep the local Docker memory requirement bounded.
            for service in ("api", "web", "inference"):
                log = STATE / f"build-{service}.log"
                print(f"Building {service}; log: {log}", flush=True)
                compose("build", service, log=log)
        initialize_execution_keys()
        certificates = {f"{name}/{file}": (STATE / "tls" / name / file).read_text()
                        for name in ("api", "inference") for file in ("service.crt", "service.key")}
        certificates["ca/ca.crt"] = (STATE / "tls" / "ca.crt").read_text()
        compose("run", "--rm", "--no-deps", "-T", "certificates", capture=True, stdin=json.dumps(certificates))
        compose("up", "-d", "--wait", "postgres")
        # Explicit offline migrations precede API/jobs startup, including on upgrades.
        compose("stop", "simulator", "evaluations", "jobs", "api")
        compose("run", "--rm", "--no-deps", "migrate")
        compose("up", "-d", "--wait", "api", "jobs", "evaluations", "web", "inference")
        bootstrap = compose("run", "--rm", "--no-deps", "acceptance", "python",
                            "/app/packaging/acceptance.py", "bootstrap", capture=True)
        private_json(STATE / "installation.json", json.loads(bootstrap.stdout))
        compose("up", "-d", "simulator")
        connection = json.loads((STATE / "connection.json").read_text())
        print(f"Ready to connect: {connection['console_url']}\nPrivate login details: {STATE / 'connection.json'}")
        print("Deployment readiness is observed in the console. Run services.py verify for a real mission.")
    elif not (STATE / ".env").exists():
        parser.error("run services.py up first")
    elif args.action == "verify":
        validate_installation(require_public=True)
        result = compose("run", "--rm", "--no-deps", "acceptance", "python",
                         "/app/packaging/acceptance.py", "verify", capture=True)
        evidence = json.loads(result.stdout)
        evidence["images"] = image_evidence()
        private_json(STATE / "evidence.json", evidence)
        print(f"Passed: {evidence['journal_applied_actions']} actions, successful simulated task.\nEvidence: {STATE / 'evidence.json'}")
    elif args.action == "status":
        validate_installation(require_public=False)
        compose("ps")
    elif args.action == "logs":
        validate_installation(require_public=False)
        compose("logs", "--tail", "100")
    elif args.action == "down":
        validate_installation(require_public=False)
        compose("down", *(('--volumes',) if args.delete_data else ()))
        if args.delete_data:
            shutil.rmtree(STATE)
        print("Stopped this local deployment." + (" Its volumes and credentials were deleted." if args.delete_data else " Data and credentials retained."))


if __name__ == "__main__":
    try:
        main()
    except InstallationError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
    except subprocess.CalledProcessError as error:
        print(f"Service command failed ({error.returncode}); inspect {STATE} build logs or services.py logs.", file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode) from None
