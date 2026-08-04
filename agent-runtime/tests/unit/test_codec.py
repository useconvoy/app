"""Codec unit tests: round-trip and payloads-are-ciphertext (TESTING §5-M0)."""

import pytest
from _support.common import TEST_CODEC_KEY, build_data_converter, fixture_run_state
from temporalio.api.common.v1 import Payload

from convoy_core import RunState
from convoy_runtime.codec import ENCODING, EncryptionCodec

pytestmark = pytest.mark.anyio

SECRET = b"tenant-secret-transcript-body"


async def test_codec_round_trip_and_ciphertext() -> None:
    codec = EncryptionCodec(TEST_CODEC_KEY)
    original = Payload(metadata={"encoding": b"json/plain"}, data=SECRET)

    [encoded] = await codec.encode([original])
    assert encoded.metadata["encoding"] == ENCODING
    assert SECRET not in encoded.data  # ciphertext only leaves the worker

    [decoded] = await codec.decode([encoded])
    assert decoded == original


async def test_codec_rejects_bad_key_size() -> None:
    with pytest.raises(ValueError):
        EncryptionCodec(b"short")


async def test_converter_encrypts_pydantic_payloads() -> None:
    """The runtime data converter (pydantic + codec) emits only ciphertext."""
    converter = build_data_converter()
    state = fixture_run_state(run_id="run-codec-test")
    payloads = await converter.encode([state])
    assert all(p.metadata["encoding"] == ENCODING for p in payloads)
    assert all(b"run-codec-test" not in p.data for p in payloads)

    [decoded] = await converter.decode(payloads, [RunState])
    assert isinstance(decoded, RunState)
    assert decoded == state


async def test_codec_passes_through_foreign_payloads() -> None:
    """Payloads not produced by our codec (server internals) pass through decode."""
    codec = EncryptionCodec(TEST_CODEC_KEY)
    foreign = Payload(metadata={"encoding": b"json/plain"}, data=b"{}")
    [decoded] = await codec.decode([foreign])
    assert decoded == foreign
