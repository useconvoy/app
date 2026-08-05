import pytest
from sqlalchemy import select

from convoy_environments.db.tables import AuditLog, Secret, Workspace
from convoy_environments.secrets import BuiltinBackend, MasterKey, SecretsService


@pytest.fixture()
def service(session_factory):
    with session_factory() as s:
        s.add(Workspace(id="w1", name="Acme"))
        s.commit()
    mk = MasterKey(MasterKey.generate().encode())
    return SecretsService(session_factory, {"builtin": BuiltinBackend(mk)}), session_factory


def test_create_reveal_roundtrip(service):
    svc, sf = service
    sid = svc.create("w1", "slack-bot-token", "xoxb-secret-value")
    assert svc.reveal(sid) == "xoxb-secret-value"


def test_value_is_encrypted_at_rest(service):
    svc, sf = service
    sid = svc.create("w1", "notion-token", "ntn_supersecret")
    with sf() as s:
        row = s.get(Secret, sid)
        assert b"ntn_supersecret" not in (row.ciphertext or b"")
        assert row.dek_wrapped and row.backend_ref is None


def test_rotate_bumps_version_and_invalidates_cache(service):
    svc, sf = service
    sid = svc.create("w1", "gh-pat", "old-value")
    assert svc.reveal(sid) == "old-value"
    svc.rotate(sid, "new-value")
    assert svc.reveal(sid) == "new-value"
    with sf() as s:
        row = s.get(Secret, sid)
        assert row.key_version == 2 and row.rotated_at is not None


def test_control_plane_changes_audited(service):
    svc, sf = service
    sid = svc.create("w1", "k", "v")
    svc.rotate(sid, "v2")
    with sf() as s:
        actions = s.execute(select(AuditLog.action).where(AuditLog.subject_id == sid)).scalars().all()
    assert actions == ["secret.create", "secret.rotate"]


def test_unknown_backend_rejected(service):
    svc, _ = service
    with pytest.raises(ValueError):
        svc.create("w1", "x", "y", backend="vault")
