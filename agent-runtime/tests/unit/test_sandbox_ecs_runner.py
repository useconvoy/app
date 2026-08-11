"""Sandbox-side ECS runner logic, exercised in-process with a fake HTTP seam:
job execution and stream/output uploads, the workspace journal that keeps
idempotency keys single-fire, snapshot archiving, the poll loop's lifecycle,
and the invariants that keep the runner shippable into a bare sandbox image
(stdlib-only) and contract-identical to the local provider."""

import ast
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from convoy_core import SandboxJobResult
from convoy_runtime.providers import sandbox as sandbox_local
from convoy_runtime.providers import sandbox_ecs_runner as runner


class FakeHttp:
    """Stands in for the runner's single HTTP seam: canned GET bodies keyed
    by URL, recorded uploads, scripted mailbox responses for the main loop."""

    def __init__(self) -> None:
        self.gets: dict[str, bytes] = {}
        self.mailbox: list[tuple[int, bytes]] = []
        self.uploads: list[tuple[str, str, bytes]] = []

    def __call__(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        if method == "GET":
            if url == "https://mailbox":
                return self.mailbox.pop(0) if self.mailbox else (404, b"")
            if url in self.gets:
                return 200, self.gets[url]
            return 404, b""
        self.uploads.append((method, url, data or b""))
        return 204, b""

    def uploaded(self, url: str) -> bytes:
        return next(data for _method, up_url, data in self.uploads if up_url == url)


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(runner, "http_request", fake)
    return fake


def _job_doc(key: str, command: list[str], **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "doc_id": f"doc-{key}",
        "kind": "job",
        "idempotency_key": key,
        "command": command,
        "env": {},
        "timeout_seconds": 10.0,
        "stream_cap_bytes": 1_000_000,
        "bucket": "convoy-test",
        "inputs": [],
        "stdout": {"key": f"jobs/{key}/stdout.txt", "url": f"https://put/{key}/stdout"},
        "stderr": {"key": f"jobs/{key}/stderr.txt", "url": f"https://put/{key}/stderr"},
        "outputs_post": {
            "url": "https://post",
            "fields": {"policy": "p", "x-amz-signature": "s"},
            "key_prefix": f"jobs/{key}/outputs/",
        },
        "result": {"url": f"https://put/{key}/result"},
    }
    doc.update(overrides)
    return doc


def test_job_runs_credential_free_and_uploads_streams(
    tmp_path: Path, fake_http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_HOST_SECRET", "never-visible")
    doc = _job_doc("k1", ["sh", "-c", "echo out; env >&2; exit 3"])
    result = runner.run_job(tmp_path, doc)

    assert result["exit_code"] == 3
    assert fake_http.uploaded(str(doc["stdout"]["url"])) == b"out\n"
    env_dump = fake_http.uploaded(str(doc["stderr"]["url"])).decode()
    # The runner's own environment (capability URLs, host vars) never leaks
    # into job processes: a minimal base plus the job's variables only.
    assert "RUNNER_HOST_SECRET" not in env_dump
    assert "CONVOY_SANDBOX" not in env_dump
    assert f"HOME={tmp_path}" in env_dump

    # The recorded result is a valid claim-check payload for the worker side.
    parsed = SandboxJobResult.model_validate(result)
    assert parsed.stdout_ref is not None and parsed.stdout_ref.key == "jobs/k1/stdout.txt"
    assert parsed.stdout_ref.bucket == "convoy-test"


def test_inputs_materialize_and_outputs_post_under_the_job_prefix(
    tmp_path: Path, fake_http: FakeHttp
) -> None:
    fake_http.gets["https://get/ledger"] = b"q3,1.2M\n"
    doc = _job_doc(
        "k2",
        ["sh", "-c", "mkdir -p outputs/sub && cp inputs/ledger.csv outputs/sub/copy.csv"],
        inputs=[{"name": "ledger.csv", "url": "https://get/ledger"}],
    )
    result = runner.run_job(tmp_path, doc)

    assert result["exit_code"] == 0
    (output,) = result["outputs"]
    assert output["key"] == "jobs/k2/outputs/sub/copy.csv"
    posted = fake_http.uploaded("https://post")
    assert b"q3,1.2M" in posted
    assert b'name="key"' in posted and b"jobs/k2/outputs/sub/copy.csv" in posted
    # Signed policy fields ride along, and the file part closes the form.
    assert b'name="policy"' in posted
    assert posted.index(b'name="file"') > posted.index(b'name="policy"')


def test_repeated_idempotency_key_replays_the_journal(tmp_path: Path, fake_http: FakeHttp) -> None:
    script = "echo once >> effect.log && mkdir -p outputs && wc -l < effect.log > outputs/n"
    first = runner.run_job(tmp_path, _job_doc("k3", ["sh", "-c", script]))
    uploads_after_first = len(fake_http.uploads)

    replayed = runner.run_job(tmp_path, _job_doc("k3", ["sh", "-c", script]))
    assert replayed == first
    assert len(fake_http.uploads) == uploads_after_first  # nothing re-ran, nothing re-uploaded
    assert (tmp_path / "effect.log").read_text() == "once\n"

    # The journal convention is byte-for-byte the local provider's, so
    # snapshots move cleanly between substrates.
    journal = tmp_path / runner.JOURNAL_DIR / "k3.json"
    assert journal.is_file()
    assert SandboxJobResult.model_validate_json(journal.read_text()) == (
        SandboxJobResult.model_validate(first)
    )


def test_timeout_surfaces_as_a_result_not_a_crash(tmp_path: Path, fake_http: FakeHttp) -> None:
    doc = _job_doc("k4", ["sh", "-c", "sleep 30"], timeout_seconds=0.2)
    result = runner.run_job(tmp_path, doc)
    assert result["exit_code"] == runner.TIMEOUT_EXIT_CODE
    assert b"timed out" in fake_http.uploaded(str(doc["stderr"]["url"]))


def test_missing_binary_surfaces_as_a_result_not_a_crash(
    tmp_path: Path, fake_http: FakeHttp
) -> None:
    result = runner.run_job(tmp_path, _job_doc("k5", ["/no/such/binary"]))
    assert result["exit_code"] == 127


def test_snapshot_archives_the_whole_workspace_with_the_journal(
    tmp_path: Path, fake_http: FakeHttp
) -> None:
    runner.run_job(tmp_path, _job_doc("k6", ["sh", "-c", "printf truth > state.txt"]))
    payload = runner.run_snapshot(tmp_path, {"url": "https://put/snapshot"})
    data = fake_http.uploaded("https://put/snapshot")
    assert payload["size_bytes"] == len(data)

    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        names = tar.getnames()
    assert "state.txt" in names
    assert f"{runner.JOURNAL_DIR}/k6.json" in names


def test_unknown_control_document_kind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unknown control document kind"):
        runner.process_document(tmp_path, {"doc_id": "d", "kind": "surprise"})


def test_main_loop_processes_documents_and_retires_on_expired_mailbox(
    tmp_path: Path, fake_http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    doc = _job_doc("k7", ["sh", "-c", "printf hi > boot.txt"])
    fake_http.mailbox = [
        (404, b""),  # nothing submitted yet
        (200, json.dumps(doc).encode()),
        (200, json.dumps(doc).encode()),  # same doc id again: not reprocessed
        (403, b""),  # capability expired: the sandbox retires itself
    ]
    monkeypatch.setenv("CONVOY_SANDBOX_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("CONVOY_SANDBOX_MAILBOX_URL", "https://mailbox")
    monkeypatch.setenv("CONVOY_SANDBOX_POLL_SECONDS", "0.01")
    monkeypatch.setenv("CONVOY_SANDBOX_MAX_IDLE_SECONDS", "30")
    monkeypatch.delenv("CONVOY_SANDBOX_WORKSPACE_URL", raising=False)

    runner.main()

    assert (workspace / "boot.txt").read_text() == "hi"
    result_doc = json.loads(fake_http.uploaded(str(doc["result"]["url"])))
    assert result_doc["doc_id"] == doc["doc_id"]
    assert result_doc["result"]["exit_code"] == 0
    # One result publication: the duplicate mailbox read did not re-run.
    result_uploads = [u for u in fake_http.uploads if u[1] == str(doc["result"]["url"])]
    assert len(result_uploads) == 1


def test_main_rehydrates_workspace_before_polling(
    tmp_path: Path, fake_http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = tmp_path / "seed"
    (seed / runner.JOURNAL_DIR).mkdir(parents=True)
    (seed / "state.txt").write_text("truth")
    (seed / runner.JOURNAL_DIR / "old.json").write_text("{}")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(seed.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(seed)), recursive=False)
    fake_http.gets["https://snapshot"] = buffer.getvalue()
    fake_http.mailbox = [(403, b"")]

    workspace = tmp_path / "ws"
    monkeypatch.setenv("CONVOY_SANDBOX_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("CONVOY_SANDBOX_MAILBOX_URL", "https://mailbox")
    monkeypatch.setenv("CONVOY_SANDBOX_WORKSPACE_URL", "https://snapshot")
    monkeypatch.setenv("CONVOY_SANDBOX_POLL_SECONDS", "0.01")
    monkeypatch.setenv("CONVOY_SANDBOX_MAX_IDLE_SECONDS", "30")

    runner.main()
    assert (workspace / "state.txt").read_text() == "truth"
    assert (workspace / runner.JOURNAL_DIR / "old.json").is_file()


def test_main_exits_after_max_idle(
    tmp_path: Path, fake_http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONVOY_SANDBOX_WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("CONVOY_SANDBOX_MAILBOX_URL", "https://mailbox")
    monkeypatch.setenv("CONVOY_SANDBOX_POLL_SECONDS", "0.01")
    monkeypatch.setenv("CONVOY_SANDBOX_MAX_IDLE_SECONDS", "0.05")
    monkeypatch.delenv("CONVOY_SANDBOX_WORKSPACE_URL", raising=False)
    runner.main()  # returns instead of spinning forever


# ------------------------------------------------------- protocol contracts


def test_runner_conventions_match_the_local_provider() -> None:
    """The two providers share one on-disk contract: same journal location,
    same workspace directories, same credential-free base environment — a
    snapshot taken by either substrate rebuilds correctly on the other."""
    assert runner.JOURNAL_DIR == sandbox_local._JOURNAL_DIR  # pyright: ignore[reportPrivateUsage]
    assert runner.INPUTS_DIR == sandbox_local._INPUTS_DIR  # pyright: ignore[reportPrivateUsage]
    assert runner.OUTPUTS_DIR == sandbox_local._OUTPUTS_DIR  # pyright: ignore[reportPrivateUsage]
    assert runner.BASE_ENV == sandbox_local._BASE_ENV  # pyright: ignore[reportPrivateUsage]


def test_runner_source_is_stdlib_only() -> None:
    """The runner ships into arbitrary sandbox images as source fetched at
    boot; any non-stdlib import would break it there."""
    source = Path(runner.__file__).read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    stdlib = {
        "hashlib",
        "io",
        "json",
        "os",
        "pathlib",
        "shutil",
        "subprocess",
        "tarfile",
        "time",
        "typing",
        "urllib",
        "uuid",
        # Integrity-pinned, stdlib-only sibling source shipped by the worker;
        # convoy_runtime is the local-test fallback for the same module.
        "convoy_sandbox_browser",
        "convoy_runtime",
    }
    assert imported <= stdlib, f"non-stdlib imports in the sandbox runner: {imported - stdlib}"
