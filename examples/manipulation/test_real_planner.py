"""Preserve a previous qualification when a real-planner output path is reused."""

import sys

import pytest
import real_planner


def test_existing_output_preserves_recorded_planner_identity(monkeypatch, tmp_path):
    output = tmp_path / "previous-run"
    output.mkdir()
    identity_path = output / "planner-identity.json"
    previous_identity = b'{"gateway_identity": {"release_id": "previous-model"}}\n'
    identity_path.write_bytes(previous_identity)
    # Metadata only: no native model, gateway connection or action policy loads.
    monkeypatch.setattr(real_planner.GatewayBackend, "inspect", lambda _: {"release_id": "new-model"})
    monkeypatch.setattr(real_planner, "artifact_descriptor", lambda _: {"fixture": "new-model"})
    monkeypatch.setattr(sys, "argv", [
        "real_planner.py", "--output", str(output),
        "--gateway-url", "http://127.0.0.1:9100", "--planner-placement", "development-local",
    ])

    with pytest.raises(FileExistsError):
        real_planner.main()

    assert identity_path.read_bytes() == previous_identity
    assert list(output.iterdir()) == [identity_path]
