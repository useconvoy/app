"""ECS sandbox provider — request shapes, presigned-URL minting, and provider
selection, all without AWS: the ECS client is validated by botocore's Stubber
(so parameter shapes are checked against the real service model) while a moto
S3 backs the artifact store, and the tests play the sandbox-side runner by
answering the control mailbox the way the real task would."""

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from moto import mock_aws

from convoy_core import SandboxJob
from convoy_runtime.config import RuntimeConfig
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.sandbox import LocalSandboxProvider, SandboxLostError
from convoy_runtime.providers.sandbox_ecs import EcsSandboxProvider
from convoy_runtime.worker import build_sandbox_provider

pytestmark = pytest.mark.anyio

CLUSTER = "convoy-acme"
FAMILY = "convoy-acme-sandbox"
SUBNETS = ["subnet-a", "subnet-b"]
SECURITY_GROUP = "sg-123"
TASK_ARN = f"arn:aws:ecs:us-east-1:123456789012:task/{CLUSTER}/0123456789abcdef"


class RecordingEcs:
    """Passes every call through the stubbed client while keeping the raw
    kwargs, so tests can assert the fine-grained request contents Stubber's
    shape validation does not surface."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run_task(self, **kwargs: Any) -> Any:
        self.calls.append(("run_task", kwargs))
        return self._inner.run_task(**kwargs)

    def describe_tasks(self, **kwargs: Any) -> Any:
        self.calls.append(("describe_tasks", kwargs))
        return self._inner.describe_tasks(**kwargs)

    def list_tasks(self, **kwargs: Any) -> Any:
        self.calls.append(("list_tasks", kwargs))
        return self._inner.list_tasks(**kwargs)

    def stop_task(self, **kwargs: Any) -> Any:
        self.calls.append(("stop_task", kwargs))
        return self._inner.stop_task(**kwargs)

    def kwargs_for(self, operation: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == operation]


@pytest.fixture
def ecs_stub() -> Iterator[tuple[RecordingEcs, Stubber]]:
    client = cast(
        "Any",
        boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "ecs", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t"
        ),
    )
    stubber = Stubber(client)
    stubber.activate()
    try:
        yield RecordingEcs(client), stubber
    finally:
        stubber.assert_no_pending_responses()
        stubber.deactivate()


@pytest.fixture
async def store() -> AsyncIterator[ArtifactStore]:
    with mock_aws():
        s3 = ArtifactStore(bucket="convoy-test", region="us-east-1", access_key="t", secret_key="t")
        await s3.ensure_bucket()
        yield s3


def _provider(store: ArtifactStore, ecs: RecordingEcs) -> EcsSandboxProvider:
    return EcsSandboxProvider(
        store,
        cluster=CLUSTER,
        task_family=FAMILY,
        subnets=SUBNETS,
        security_group=SECURITY_GROUP,
        poll_interval_seconds=0.02,
        submit_timeout_seconds=5.0,
        create_timeout_seconds=5.0,
        ecs_client=ecs,
    )


def _stub_run_task(stubber: Stubber) -> None:
    stubber.add_response(
        "run_task",
        {"tasks": [{"taskArn": TASK_ARN, "lastStatus": "PROVISIONING"}], "failures": []},
        {
            "cluster": CLUSTER,
            "taskDefinition": FAMILY,
            "count": 1,
            "launchType": "FARGATE",
            "startedBy": ANY,
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": SUBNETS,
                    "securityGroups": [SECURITY_GROUP],
                    "assignPublicIp": "DISABLED",
                }
            },
            "overrides": ANY,
            "tags": ANY,
            "propagateTags": "TASK_DEFINITION",
        },
    )


def _stub_describe(stubber: Stubber, status: str, count: int = 1) -> None:
    for _ in range(count):
        stubber.add_response(
            "describe_tasks",
            {"tasks": [{"taskArn": TASK_ARN, "lastStatus": status}], "failures": []},
            {"cluster": CLUSTER, "tasks": [TASK_ARN]},
        )


def _override_env(run_task_kwargs: dict[str, Any]) -> dict[str, str]:
    container = run_task_kwargs["overrides"]["containerOverrides"][0]
    return {entry["name"]: entry["value"] for entry in container["environment"]}


async def _play_runner(
    store: ArtifactStore,
    sandbox_id: str,
    answer: "Callable[[dict[str, Any]], dict[str, Any]]",
) -> dict[str, Any]:
    """Await the next mailbox document like the in-task runner would, then
    publish the given answer at the document's result key."""
    mailbox_key = f"sandboxes/{sandbox_id}/control/mailbox.json"
    deadline = time.monotonic() + 5.0
    while True:
        try:
            doc: dict[str, Any] = await store.get_json_at(mailbox_key)
            break
        except ClientError:
            if time.monotonic() > deadline:
                raise TimeoutError("no control document arrived in the mailbox") from None
            await asyncio.sleep(0.02)
    payload = {"doc_id": doc["doc_id"], **answer(doc)}
    await store.put_json(f"sandboxes/{sandbox_id}/control/results/{doc['doc_id']}.json", payload)
    return doc


# ------------------------------------------------------------------ create


async def test_create_runs_a_fargate_task_with_capability_urls(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    _stub_run_task(stubber)
    _stub_describe(stubber, "RUNNING")

    handle = await _provider(store, ecs).create("stub", None)
    assert handle.provider == "ecs"
    assert handle.template == "stub"
    assert handle.sandbox_id.startswith("sbx-")

    (run_kwargs,) = ecs.kwargs_for("run_task")
    assert run_kwargs["startedBy"] == handle.sandbox_id
    assert {"key": "convoy:sandbox-id", "value": handle.sandbox_id} in run_kwargs["tags"]
    assert {"key": "convoy:sandbox-template", "value": "stub"} in run_kwargs["tags"]

    container = run_kwargs["overrides"]["containerOverrides"][0]
    assert container["name"] == "sandbox"
    assert container["command"][:2] == ["python3", "-c"]
    # The override payload must stay within the RunTask overrides size cap.
    assert len(json.dumps(run_kwargs["overrides"])) < 8192

    env = _override_env(run_kwargs)
    assert env["CONVOY_SANDBOX_ID"] == handle.sandbox_id
    assert "CONVOY_SANDBOX_WORKSPACE_URL" not in env

    # The runner arrives as a claim-checked artifact behind an integrity-
    # pinned capability URL — the bootstrap refuses any other bytes.
    runner_url = urlparse(env["CONVOY_SANDBOX_RUNNER_URL"])
    assert runner_url.path.endswith(f"/convoy-test/sandboxes/{handle.sandbox_id}/control/runner.py")
    stored = _read_object(store.bucket, f"sandboxes/{handle.sandbox_id}/control/runner.py")
    assert env["CONVOY_SANDBOX_RUNNER_SHA256"] == hashlib.sha256(stored).hexdigest()
    assert b"def run_job" in stored

    mailbox_url = urlparse(env["CONVOY_SANDBOX_MAILBOX_URL"])
    assert mailbox_url.path.endswith(
        f"/convoy-test/sandboxes/{handle.sandbox_id}/control/mailbox.json"
    )
    assert parse_qs(mailbox_url.query)["X-Amz-Expires"] == ["28800"]


def _read_object(bucket: str, key: str) -> bytes:
    """Raw object bytes through a fresh client bound to the moto backend."""
    s3 = boto3.client(  # pyright: ignore[reportUnknownMemberType]
        "s3", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    body: Any = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return cast("bytes", body)


async def test_create_rehydrates_workspace_from_snapshot_capability(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    snapshot = await store.put_bytes("sandboxes/old/snapshot-abc.tar", b"tar-bytes")
    _stub_run_task(stubber)
    _stub_describe(stubber, "RUNNING")

    await _provider(store, ecs).create("stub", snapshot)
    env = _override_env(ecs.kwargs_for("run_task")[0])
    workspace_url = urlparse(env["CONVOY_SANDBOX_WORKSPACE_URL"])
    assert workspace_url.path.endswith("/convoy-test/sandboxes/old/snapshot-abc.tar")


async def test_create_fails_when_the_task_never_starts(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    _stub_run_task(stubber)
    _stub_describe(stubber, "STOPPED")
    with pytest.raises(RuntimeError, match="stopped before reaching RUNNING"):
        await _provider(store, ecs).create("stub", None)


# -------------------------------------------------------------------- exec


async def test_exec_submits_job_document_with_presigned_data_plane(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    _stub_run_task(stubber)
    _stub_describe(stubber, "RUNNING", count=2)
    provider = _provider(store, ecs)
    handle = await provider.create("stub", None)

    data_ref = await store.put_bytes("fixtures/ledger.csv", b"q3,1.2M\n")
    job = SandboxJob(
        idempotency_key="key-1",
        command=["sh", "-c", "true"],
        env={"JOB_VAR": "yes"},
        inputs=[data_ref],
    )

    result_payload: dict[str, Any] = {
        "result": {
            "exit_code": 0,
            "stdout_ref": None,
            "stderr_ref": None,
            "outputs": [],
        }
    }
    exec_task = asyncio.create_task(provider.exec(handle, job))
    doc = await _play_runner(store, handle.sandbox_id, lambda _doc: result_payload)
    result = await exec_task

    assert result.exit_code == 0
    assert doc["kind"] == "job"
    assert doc["idempotency_key"] == "key-1"
    assert doc["command"] == ["sh", "-c", "true"]
    assert doc["env"] == {"JOB_VAR": "yes"}
    assert doc["bucket"] == "convoy-test"

    job_prefix = f"sandboxes/{handle.sandbox_id}/jobs/key-1"
    assert doc["stdout"]["key"] == f"{job_prefix}/stdout.txt"
    assert doc["stderr"]["key"] == f"{job_prefix}/stderr.txt"
    for target in (doc["stdout"], doc["stderr"]):
        parsed = urlparse(str(target["url"]))
        assert parsed.path.endswith(f"/convoy-test/{target['key']}")
        assert "content-type" in parse_qs(parsed.query)["X-Amz-SignedHeaders"][0]

    (input_entry,) = doc["inputs"]
    assert input_entry["name"] == "ledger.csv"
    assert urlparse(str(input_entry["url"])).path.endswith("/convoy-test/fixtures/ledger.csv")

    # Outputs upload is a POST policy scoped to the job's outputs prefix, so
    # runtime-named files can land there and nowhere else.
    post = doc["outputs_post"]
    assert post["key_prefix"] == f"{job_prefix}/outputs/"
    policy = json.loads(base64.b64decode(post["fields"]["policy"]))
    assert ["starts-with", "$key", f"{job_prefix}/outputs/"] in policy["conditions"]


async def test_exec_on_a_stopped_task_raises_sandbox_lost(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    _stub_run_task(stubber)
    _stub_describe(stubber, "RUNNING")
    provider = _provider(store, ecs)
    handle = await provider.create("stub", None)

    _stub_describe(stubber, "STOPPED")
    with pytest.raises(SandboxLostError):
        await provider.exec(handle, SandboxJob(idempotency_key="k", command=["true"]))


async def test_exec_after_destroy_raises_sandbox_lost(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    _stub_run_task(stubber)
    _stub_describe(stubber, "RUNNING")
    provider = _provider(store, ecs)
    handle = await provider.create("stub", None)

    stubber.add_response(
        "stop_task",
        {"task": {"taskArn": TASK_ARN, "lastStatus": "STOPPED"}},
        {"cluster": CLUSTER, "task": TASK_ARN, "reason": ANY},
    )
    await provider.destroy(handle)

    # The stopped task no longer lists under the sandbox's startedBy stamp.
    stubber.add_response(
        "list_tasks",
        {"taskArns": []},
        {
            "cluster": CLUSTER,
            "family": FAMILY,
            "startedBy": handle.sandbox_id,
            "desiredStatus": "RUNNING",
        },
    )
    with pytest.raises(SandboxLostError):
        await provider.exec(handle, SandboxJob(idempotency_key="k", command=["true"]))

    # Destroy is idempotent: with no task to stop it is a clean no-op.
    stubber.add_response(
        "list_tasks",
        {"taskArns": []},
        {
            "cluster": CLUSTER,
            "family": FAMILY,
            "startedBy": handle.sandbox_id,
            "desiredStatus": "RUNNING",
        },
    )
    await provider.destroy(handle)
    assert len(ecs.kwargs_for("stop_task")) == 1


# ---------------------------------------------------------------- snapshot


async def test_snapshot_returns_ref_for_the_uploaded_workspace_archive(
    store: ArtifactStore, ecs_stub: tuple[RecordingEcs, Stubber]
) -> None:
    ecs, stubber = ecs_stub
    _stub_run_task(stubber)
    _stub_describe(stubber, "RUNNING", count=2)
    provider = _provider(store, ecs)
    handle = await provider.create("stub", None)

    snapshot_task = asyncio.create_task(provider.snapshot(handle))
    doc = await _play_runner(
        store, handle.sandbox_id, lambda _doc: {"sha256": "ab" * 32, "size_bytes": 512}
    )
    ref = await snapshot_task

    assert doc["kind"] == "snapshot"
    assert doc["key"].startswith(f"sandboxes/{handle.sandbox_id}/snapshot-")
    assert doc["key"].endswith(".tar")
    assert urlparse(str(doc["url"])).path.endswith(f"/convoy-test/{doc['key']}")
    assert ref.key == doc["key"]
    assert ref.sha256 == "ab" * 32
    assert ref.size_bytes == 512
    assert ref.content_type == "application/x-tar"


# --------------------------------------------------------------- selection


def _selection_config(monkeypatch: pytest.MonkeyPatch, provider: str) -> RuntimeConfig:
    monkeypatch.setenv("CONVOY_CODEC_KEY_B64", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("CONVOY_SANDBOX_PROVIDER", provider)
    return RuntimeConfig.from_env()


def test_local_provider_selected_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.delenv("CONVOY_SANDBOX_PROVIDER", raising=False)
    monkeypatch.setenv("CONVOY_SANDBOX_DIR", str(tmp_path))
    config = _selection_config(monkeypatch, "local")
    store = ArtifactStore(bucket="convoy-test", access_key="t", secret_key="t")
    assert isinstance(build_sandbox_provider(config, store), LocalSandboxProvider)


def test_ecs_provider_selected_with_stack_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVOY_SANDBOX_CLUSTER", CLUSTER)
    monkeypatch.setenv("CONVOY_SANDBOX_TASK_FAMILY", FAMILY)
    monkeypatch.setenv("CONVOY_SANDBOX_SUBNETS", ",".join(SUBNETS))
    monkeypatch.setenv("CONVOY_SANDBOX_SECURITY_GROUP", SECURITY_GROUP)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    config = _selection_config(monkeypatch, "ecs")
    store = ArtifactStore(bucket="convoy-test", access_key="t", secret_key="t")
    assert isinstance(build_sandbox_provider(config, store), EcsSandboxProvider)


def test_ecs_provider_without_wiring_fails_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CONVOY_SANDBOX_CLUSTER",
        "CONVOY_SANDBOX_TASK_FAMILY",
        "CONVOY_SANDBOX_SUBNETS",
        "CONVOY_SANDBOX_SECURITY_GROUP",
    ):
        monkeypatch.delenv(name, raising=False)
    config = _selection_config(monkeypatch, "ecs")
    store = ArtifactStore(bucket="convoy-test", access_key="t", secret_key="t")
    with pytest.raises(RuntimeError, match="CONVOY_SANDBOX_CLUSTER"):
        build_sandbox_provider(config, store)
