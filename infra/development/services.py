"""Reproducible local service deployment; requires Docker Compose and OpenSSL."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = HERE / ".services"
PROJECT = "convoy-v1-services"
CONFIG_KEYS = {"WEB_PORT", "API_PORT", "POSTGRES_PASSWORD", "CONVOY_ADMIN_EMAIL", "CONVOY_ADMIN_PASSWORD",
               "CONVOY_EXECUTION_SECRET", "CONVOY_WORKER_PROBE_TOKEN"}


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def private_json(path, content):
    with path.open("w") as stream:
        os.chmod(path, 0o600)
        json.dump(content, stream, indent=2)
        stream.write("\n")


def initialize(web_port, api_port):
    if (STATE / ".env").exists():
        return
    STATE.mkdir(mode=0o700, exist_ok=True)
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
                   "console_url": f"http://localhost:{web_port}/console", "api_url": f"https://localhost:{api_port}",
                   "ca_file": str(tls / "ca.crt")}
    private_json(STATE / "connection.json", credentials)
    values = {"WEB_PORT": str(web_port), "API_PORT": str(api_port),
              "POSTGRES_PASSWORD": secrets.token_urlsafe(32), "CONVOY_ADMIN_EMAIL": credentials["email"],
              "CONVOY_ADMIN_PASSWORD": credentials["password"], "CONVOY_EXECUTION_SECRET": secrets.token_urlsafe(48),
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


def image_evidence():
    result = {}
    for image in ("convoy-v1-api:local", "convoy-v1-web:local", "convoy-v1-reference:local"):
        details = json.loads(run(["docker", "image", "inspect", image], capture_output=True).stdout)[0]
        result[image] = {"id": details["Id"], "architecture": details["Architecture"]}
    result["source_commit"] = run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True).stdout.strip()
    result["source_dirty"] = bool(run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True).stdout)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "verify", "status", "logs", "down"])
    parser.add_argument("--web-port", type=int, default=3300, help="used only on first up")
    parser.add_argument("--api-port", type=int, default=8443, help="used only on first up")
    parser.add_argument("--skip-build", action="store_true", help="reuse existing local images")
    parser.add_argument("--delete-data", action="store_true", help="with down: remove this setup's volumes and secrets")
    args = parser.parse_args()
    if not 1024 <= args.web_port <= 65535 or not 1024 <= args.api_port <= 65535:
        parser.error("choose unprivileged ports from 1024 through 65535")
    if args.delete_data and args.action != "down":
        parser.error("--delete-data requires down")
    if args.action == "up":
        initialize(args.web_port, args.api_port)
        if not args.skip_build:
            # Sequential builds keep the local Docker memory requirement bounded.
            for service in ("api", "web", "inference"):
                log = STATE / f"build-{service}.log"
                print(f"Building {service}; log: {log}", flush=True)
                compose("build", service, log=log)
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
        result = compose("run", "--rm", "--no-deps", "acceptance", "python",
                         "/app/packaging/acceptance.py", "verify", capture=True)
        evidence = json.loads(result.stdout)
        evidence["images"] = image_evidence()
        private_json(STATE / "evidence.json", evidence)
        print(f"Passed: {evidence['journal_applied_actions']} actions, successful simulated task.\nEvidence: {STATE / 'evidence.json'}")
    elif args.action == "status":
        compose("ps")
    elif args.action == "logs":
        compose("logs", "--tail", "100")
    elif args.action == "down":
        compose("down", *(('--volumes',) if args.delete_data else ()))
        if args.delete_data:
            shutil.rmtree(STATE)
        print("Stopped this local deployment." + (" Its volumes and credentials were deleted." if args.delete_data else " Data and credentials retained."))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(f"Service command failed ({error.returncode}); inspect {STATE} build logs or services.py logs.", file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode) from None
