"""Sandbox-side runner for the ECS sandbox provider.

This module is the inside half of the ECS sandbox protocol. The trusted
worker claim-checks this file's source to the artifact store at sandbox
creation and boots the Fargate task with a tiny integrity-pinned bootstrap
that fetches and executes it — so the runtime owns both halves of the
protocol. The canonical image supplies Python and Chromium; environments
without browser policy need only the Python interpreter.

It runs credential-free by construction: every byte it moves travels over
short-lived presigned URLs minted by the trusted worker (a GET-poll mailbox
for control documents, per-job GET URLs for inputs, PUT/POST capabilities for
streams, outputs, results, and snapshots). It holds no IAM credentials and
strips its own environment before running job commands, so job processes see
only a minimal base environment plus the job's own variables.

The workspace directory persists for the life of the task and is cache; the
job journal inside it (identical layout to the local provider's) makes a
repeated idempotency key replay the recorded result instead of re-running,
and rides every snapshot so the guarantee survives sandbox loss.

Stdlib only — it must run on any image with a Python 3 interpreter, with no
package installs and no runtime dependencies.
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from convoy_runtime.providers.sandbox_browser import BrowserRuntime
else:
    try:
        from convoy_sandbox_browser import BrowserRuntime  # pyright: ignore[reportMissingImports]
    except ImportError:  # imported normally by local unit tests
        from convoy_runtime.providers.sandbox_browser import BrowserRuntime

JOURNAL_DIR = ".convoy/journal"
INPUTS_DIR = "inputs"
OUTPUTS_DIR = "outputs"

# Job processes run credential-free: this base plus the job's own variables.
BASE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}

DEFAULT_JOB_TIMEOUT_SECONDS = 60.0
DEFAULT_STREAM_CAP_BYTES = 1_000_000
TIMEOUT_EXIT_CODE = 124
_HTTP_TIMEOUT_SECONDS = 60.0


def http_request(
    method: str,
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """One HTTP exchange; errors come back as (status, body), never raises
    for HTTP-level failures so the poll loop can interpret 404 vs 403."""
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as err:
        return int(err.code), err.read()


def _capped(stream: bytes, cap: int) -> bytes:
    return stream[:cap]


def _put(url: str, data: bytes, content_type: str) -> None:
    status, body = http_request("PUT", url, data=data, headers={"Content-Type": content_type})
    if status not in (200, 201, 204):
        raise RuntimeError(f"upload failed with HTTP {status}: {body[:200]!r}")


def _post_file(post_url: str, fields: dict[str, str], key: str, data: bytes) -> None:
    """S3 POST-policy upload: the signed form fields plus the target key,
    with the file part last as the form contract requires."""
    boundary = f"convoy-{uuid.uuid4().hex}"
    body = io.BytesIO()
    for name, value in [*fields.items(), ("key", key)]:
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="file"; filename="blob"\r\n\r\n')
    body.write(data)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    status, response = http_request(
        "POST",
        post_url,
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if status not in (200, 201, 204):
        raise RuntimeError(f"output upload failed with HTTP {status}: {response[:200]!r}")


def _artifact_ref(bucket: str, key: str, data: bytes, content_type: str) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "key": key,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": content_type,
    }


def rehydrate(workspace: Path, url: str) -> None:
    """Rebuild the workspace (journal included) from a snapshot capability."""
    status, data = http_request("GET", url)
    if status != 200:
        raise RuntimeError(f"workspace rehydrate failed with HTTP {status}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        tar.extractall(workspace, filter="data")


def _archive(workspace: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(workspace.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(workspace)), recursive=False)
    return buffer.getvalue()


def _prepare_workspace(workspace: Path, inputs: list[tuple[str, bytes]]) -> None:
    """Materialize input data and reset the per-job outputs scratch, exactly
    as the local provider does: earlier jobs' outputs are already artifacts,
    so the outputs directory starts empty for every execution."""
    for name, data in inputs:
        target = workspace / INPUTS_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    outputs = workspace / OUTPUTS_DIR
    shutil.rmtree(outputs, ignore_errors=True)
    outputs.mkdir(parents=True, exist_ok=True)


def _collect_outputs(workspace: Path) -> list[tuple[str, bytes]]:
    outputs = workspace / OUTPUTS_DIR
    if not outputs.is_dir():
        return []
    return [
        (str(path.relative_to(outputs)), path.read_bytes())
        for path in sorted(outputs.rglob("*"))
        if path.is_file()
    ]


def run_job(
    workspace: Path, doc: dict[str, Any], browser_env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Execute one job document and return the result payload.

    A journaled idempotency key replays the recorded result without touching
    the process table — the single-fire guarantee for side effects.
    """
    journal_path = workspace / JOURNAL_DIR / f"{doc['idempotency_key']}.json"
    if journal_path.is_file():
        result: dict[str, Any] = json.loads(journal_path.read_text())
        return result

    inputs: list[tuple[str, bytes]] = []
    for entry in doc.get("inputs", []):
        status, data = http_request("GET", str(entry["url"]))
        if status != 200:
            raise RuntimeError(f"input {entry['name']!r} fetch failed with HTTP {status}")
        inputs.append((str(entry["name"]), data))
    _prepare_workspace(workspace, inputs)

    timeout = float(doc.get("timeout_seconds") or DEFAULT_JOB_TIMEOUT_SECONDS)
    cap = int(doc.get("stream_cap_bytes") or DEFAULT_STREAM_CAP_BYTES)
    env = {
        **BASE_ENV,
        "HOME": str(workspace),
        **{str(k): str(v) for k, v in doc["env"].items()},
        **(browser_env or {}),
    }
    try:
        completed = subprocess.run(
            [str(part) for part in doc["command"]],
            cwd=workspace,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout, stderr = completed.stdout, completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr = b"", f"job timed out after {timeout}s".encode()
        exit_code = TIMEOUT_EXIT_CODE
    except OSError as err:
        stdout, stderr = b"", str(err).encode()
        exit_code = 127

    bucket = str(doc["bucket"])
    _put(str(doc["stdout"]["url"]), _capped(stdout, cap), "text/plain")
    _put(str(doc["stderr"]["url"]), _capped(stderr, cap), "text/plain")
    stdout_ref = _artifact_ref(
        bucket, str(doc["stdout"]["key"]), _capped(stdout, cap), "text/plain"
    )
    stderr_ref = _artifact_ref(
        bucket, str(doc["stderr"]["key"]), _capped(stderr, cap), "text/plain"
    )

    outputs: list[dict[str, Any]] = []
    post = doc["outputs_post"]
    for rel, data in _collect_outputs(workspace):
        key = f"{post['key_prefix']}{rel}"
        _post_file(str(post["url"]), dict(post["fields"]), key, data)
        outputs.append(_artifact_ref(bucket, key, data, "application/octet-stream"))

    result = {
        "exit_code": exit_code,
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
        "outputs": outputs,
    }
    # Journal before reporting: from here on this key never re-executes.
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(json.dumps(result, sort_keys=True))
    return result


def run_snapshot(
    workspace: Path, doc: dict[str, Any], browser: BrowserRuntime | None = None
) -> dict[str, Any]:
    """Archive the whole workspace (journal included) to the snapshot
    capability and report its integrity facts."""
    if browser is not None:
        browser.stop_browser()
    try:
        data = _archive(workspace)
    finally:
        if browser is not None:
            browser.start()
    _put(str(doc["url"]), data, "application/x-tar")
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def process_document(
    workspace: Path, doc: dict[str, Any], browser: BrowserRuntime | None = None
) -> dict[str, Any]:
    kind = doc.get("kind")
    if kind == "job":
        return {
            "doc_id": doc["doc_id"],
            "result": run_job(workspace, doc, browser.job_env if browser is not None else None),
        }
    if kind == "snapshot":
        return {"doc_id": doc["doc_id"], **run_snapshot(workspace, doc, browser)}
    raise RuntimeError(f"unknown control document kind {kind!r}")


def main() -> None:
    workspace = Path(os.environ.get("CONVOY_SANDBOX_WORKSPACE_DIR", "/tmp/convoy-workspace"))
    workspace.mkdir(parents=True, exist_ok=True)
    mailbox_url = os.environ["CONVOY_SANDBOX_MAILBOX_URL"]
    poll_seconds = float(os.environ.get("CONVOY_SANDBOX_POLL_SECONDS", "1.0"))
    max_idle = float(os.environ.get("CONVOY_SANDBOX_MAX_IDLE_SECONDS", "3600"))

    workspace_url = os.environ.get("CONVOY_SANDBOX_WORKSPACE_URL")
    if workspace_url:
        rehydrate(workspace, workspace_url)

    browser_policy = json.loads(os.environ.get("CONVOY_SANDBOX_BROWSER_POLICY", "{}"))
    browser = BrowserRuntime(workspace, browser_policy)
    browser.start()

    last_doc_id = ""
    last_activity = time.monotonic()
    try:
        while time.monotonic() - last_activity < max_idle:
            status, body = http_request("GET", mailbox_url)
            if status == 403:
                # The mailbox capability expired: the sandbox has outlived its
                # lease, so it retires itself rather than idling forever.
                return
            if status == 200:
                doc: dict[str, Any] = json.loads(body)
                if doc.get("doc_id") != last_doc_id:
                    last_doc_id = str(doc["doc_id"])
                    response = process_document(workspace, doc, browser)
                    _put(
                        str(doc["result"]["url"]),
                        json.dumps(response).encode(),
                        "application/json",
                    )
                    last_activity = time.monotonic()
                    continue
            time.sleep(poll_seconds)
    finally:
        browser.close()


if __name__ == "__main__":
    main()
