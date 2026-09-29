"""Native supervisor wiring; tiny real children, not model-quality evidence."""

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest
from convoy_agent.gateway import Gateway
from convoy_agent.owned_process import OwnedProcess
from convoy_agent.runtime import RuntimeError_, RuntimeSupervisor

psutil = pytest.importorskip("psutil")
pytestmark = pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"} or psutil.__version__ != "7.2.2",
    reason="requires pinned local-host process identity backend",
)


@pytest.fixture
def native(tmp_path, monkeypatch):
    # Exercise actual spawn/ownership/log drainage. Health/model evidence is
    # intentionally stubbed: separate native Qwen acceptance supplies that proof.
    monkeypatch.setattr("convoy_agent.runtime.argv_for", lambda _cfg, **kw: [
        sys.executable, "-c", "import time; time.sleep(60)", kw["api_key_file"],
    ])
    monkeypatch.setattr(RuntimeSupervisor, "wait_healthy", lambda *_: (True, {}))
    monkeypatch.setattr(RuntimeSupervisor, "collect_evidence", lambda _: {
        "binary_sha256": "b" * 64, "chat_template_sha256": "c" * 64,
    })
    model = tmp_path / "model"
    model.write_bytes(b"test-model")
    launch = dict(release_id="test", spec={"model": {"file": {
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}},
        "runtime": {"artifact_sha256": "a" * 64}}, model_path=model,
        template_path=None, binary=Path(sys.executable), lib_dir=None)
    return tmp_path / "runtime", launch


def test_owned_native_start_stop_and_implementation_identity(native):
    directory, launch = native
    with OwnedProcess(directory / "owner") as owner:
        supervisor = RuntimeSupervisor(directory, process_owner=owner)
        try:
            supervisor.start(**launch)
            child = supervisor.proc
            marker = supervisor.api_key_file
            record = json.loads(owner.record_path.read_text())
            assert record["pid"] == child.pid and record["marker"] == str(marker)
            assert marker.read_text() == supervisor.api_key
            assert supervisor.argv.count(str(marker)) == 1
            assert not (directory / "child.json").exists()
            gateway = Gateway(supervisor)
            gateway.set_mode("production")
            sources = gateway.runtime_identity()["implementation_sha256"]
            assert set(sources) == {"gateway.py", "runtime.py", "runtime_args.py",
                                    "owned_process.py", "psutil-7.2.2"}
            assert all(len(value) == 64 for value in sources.values())
            result = supervisor.stop(deadline=time.monotonic() + 3)
            assert result["stopped"] and child.poll() is not None
            assert not marker.exists() and not supervisor._reader.is_alive()
            supervisor.start(**launch)
            assert supervisor.api_key_file != marker
        finally:
            assert supervisor.stop(deadline=time.monotonic() + 3)["stopped"]


def test_successor_recovers_before_replacement_and_leaves_legacy_evidence(native):
    directory, launch = native
    first = None
    with OwnedProcess(directory / "owner") as owner:
        first = RuntimeSupervisor(directory, process_owner=owner)
        first.start(**launch)
        original = first.proc
    # Releasing the owner deliberately does not claim its child stopped.
    try:
        with OwnedProcess(directory / "owner") as successor:
            second = RuntimeSupervisor(directory, process_owner=successor)
            try:
                second.start(**launch)
                assert original.poll() is not None
                assert second.proc.pid != original.pid
                assert second.orphan_note["action"] == "stopped"
                assert second.stop(deadline=time.monotonic() + 3)["stopped"]
                legacy = directory / "child.json"
                legacy.write_text('{"pid":12345,"marker":"unverified-old-record"}')
                before = legacy.read_bytes()
                with pytest.raises(RuntimeError_, match="legacy native ownership"):
                    second.start(**launch)
                assert legacy.read_bytes() == before and second.proc is None
                assert not second.stop()["stopped"]
                legacy.unlink()  # this test's synthetic evidence; no actual legacy child
            finally:
                assert second.stop(deadline=time.monotonic() + 3)["stopped"]
    finally:
        # The original Popen retains its exact child identity for test cleanup.
        if original.poll() is None:
            original.kill()
            original.wait(timeout=3)
        first._reader.join(timeout=3)


def test_bad_model_pin_does_not_recover_or_rotate_existing_child(native):
    directory, launch = native
    with OwnedProcess(directory / "owner") as owner:
        first = RuntimeSupervisor(directory, process_owner=owner)
        try:
            first.start(**launch)
            original_key = first.api_key_file.read_bytes()
            second = RuntimeSupervisor(directory, process_owner=owner)
            wrong = {**launch, "spec": {"model": {"file": {"sha256": "0" * 64}}}}
            with pytest.raises(RuntimeError_, match="model bytes differ"):
                second.start(**wrong)
            assert first.proc.poll() is None
            assert first.api_key_file.read_bytes() == original_key
            assert second.generation == 0
        finally:
            assert first.stop(deadline=time.monotonic() + 3)["stopped"]


def test_missing_record_and_incomplete_output_never_claim_cleanup(native, monkeypatch):
    directory, launch = native
    with OwnedProcess(directory / "owner") as owner:
        supervisor = RuntimeSupervisor(directory, process_owner=owner)
        before = None
        try:
            supervisor.start(**launch)
            child, key = supervisor.proc, supervisor.api_key_file
            before = owner.record_path.read_bytes()
            owner.record_path.unlink()  # model is still alive: absence is not proof
            result = supervisor.stop(deadline=time.monotonic() + 0.1)
            assert not result["stopped"] and result["reason"] == "child_exit_unverified"
            assert supervisor.proc is child and child.poll() is None and key.exists()
            owner.record_path.write_bytes(before)
            owner.record_path.chmod(0o600)
            with monkeypatch.context() as patch:
                patch.setattr(supervisor._reader, "is_alive", lambda: True)
                result = supervisor.stop(deadline=time.monotonic() + 1)
                assert not result["stopped"] and result["reason"] == "output_drain_incomplete"
                assert supervisor.proc is child and child.poll() is not None and key.exists()
                with pytest.raises(RuntimeError_, match="finished draining"):
                    supervisor.start(**launch)
            assert supervisor.stop(deadline=time.monotonic() + 1)["stopped"]
            assert not key.exists()
        finally:
            if before is not None and not owner.record_path.exists():
                owner.record_path.write_bytes(before)
                owner.record_path.chmod(0o600)
            assert supervisor.stop(deadline=time.monotonic() + 3)["stopped"]


def test_failed_reader_start_closes_pipe_after_verified_exit_and_allows_restart(native, monkeypatch):
    directory, launch = native
    with OwnedProcess(directory / "owner") as owner:
        supervisor = RuntimeSupervisor(directory, process_owner=owner)

        def fail_start(_):
            raise RuntimeError("test reader thread could not start")

        try:
            with monkeypatch.context() as patch:
                patch.setattr("convoy_agent.runtime._LogReader.start", fail_start)
                with pytest.raises(RuntimeError, match="reader thread could not start"):
                    supervisor.start(**launch)
            child, reader, marker = supervisor.proc, supervisor._reader, supervisor.api_key_file
            assert child.poll() is None and reader.ident is None and not child.stdout.closed
            result = supervisor.stop(deadline=time.monotonic() + 3)
            assert result["stopped"] and child.poll() is not None
            assert child.stdout.closed and not marker.exists()
            assert json.loads(owner.record_path.read_text())["state"] == "stopped"
            supervisor.start(**launch)
            assert supervisor.proc is not child and supervisor._reader.ident is not None
        finally:
            assert supervisor.stop(deadline=time.monotonic() + 3)["stopped"]
