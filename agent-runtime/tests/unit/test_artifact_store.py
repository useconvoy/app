"""ArtifactRef claim-check store round-trip (fast lane via moto; the same store
runs against real MinIO in the e2e lane)."""

from collections.abc import Iterator

import pytest
from moto import mock_aws

from convoy_runtime.providers.artifact_store import ArtifactIntegrityError, ArtifactStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def store() -> Iterator[ArtifactStore]:
    with mock_aws():
        yield ArtifactStore(
            bucket="convoy-test",
            region="us-east-1",
            access_key="test",
            secret_key="test",
        )


async def test_bytes_round_trip(store: ArtifactStore) -> None:
    await store.ensure_bucket()
    data = b"hello claim check"
    ref = await store.put_bytes("runs/run-1/blob.bin", data)
    assert ref.bucket == "convoy-test"
    assert ref.key == "runs/run-1/blob.bin"
    assert ref.size_bytes == len(data)
    assert await store.get_bytes(ref) == data


async def test_json_round_trip(store: ArtifactStore) -> None:
    await store.ensure_bucket()
    obj = {"goal": "test", "criteria": ["a", "b"]}
    ref = await store.put_json("runs/run-1/pinned.json", obj)
    assert ref.content_type == "application/json"
    assert await store.get_json(ref) == obj


async def test_integrity_check_rejects_tampered_blob(store: ArtifactStore) -> None:
    await store.ensure_bucket()
    ref = await store.put_bytes("runs/run-1/blob.bin", b"original")
    tampered = ref.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ArtifactIntegrityError):
        await store.get_bytes(tampered)


async def test_ensure_bucket_is_idempotent(store: ArtifactStore) -> None:
    await store.ensure_bucket()
    await store.ensure_bucket()
    ref = await store.put_bytes("k", b"v")
    assert await store.get_bytes(ref) == b"v"
