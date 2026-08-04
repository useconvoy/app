"""History-is-ciphertext verification: the raw Temporal workflow history —
fetched with a codec-less client, so payloads arrive exactly as the server
stores them — must contain no plaintext run material anywhere, and every
runtime-produced payload must carry the encrypted encoding.

Two entry points share one scanner:

- The compose test runs a small run against the local stack and scans it;
  this is the automated e2e gate.
- The live test is the runbook's stamped-stack verification: point it at a
  Temporal Cloud namespace via TEMPORAL_ADDRESS / TEMPORAL_NAMESPACE (and the
  TEMPORAL_TLS_CERT_PEM / TEMPORAL_TLS_KEY_PEM pair for mTLS), name the run
  with CONVOY_CIPHERTEXT_RUN_ID, and list the expected plaintext markers
  (goal text, tenant id) in CONVOY_CIPHERTEXT_MARKERS. It runs standalone —
  no compose stack, no other fixtures:

      TEMPORAL_ADDRESS=<ns>.tmprl.cloud:7233 TEMPORAL_NAMESPACE=<ns> \\
      TEMPORAL_TLS_CERT_PEM="$(cat client.pem)" \\
      TEMPORAL_TLS_KEY_PEM="$(cat client.key)" \\
      CONVOY_CIPHERTEXT_RUN_ID=<run-id> \\
      CONVOY_CIPHERTEXT_MARKERS='<goal text>,<tenant-id>' \\
      uv run pytest \\
        agent-runtime/tests/integration/test_codec_ciphertext.py::test_live_history_is_ciphertext
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from _support.e2e import auth_headers, collect_sse
from temporalio.client import Client

from convoy_runtime.codec import ENCODING
from convoy_runtime.config import temporal_tls_from_env

pytestmark = pytest.mark.e2e

# Payloads at these paths are produced by the server or SDK internals (worker
# build ids and other indexed metadata), not by the runtime's converter, so
# they legitimately carry non-encrypted encodings. They still must not leak
# run material — the marker scan below covers every byte of every event.
_SERVER_OWNED_PATH_FRAGMENTS = ("search_attributes",)

# The SDK's own patch markers record only patch ids for replay versioning;
# their details never pass through the application converter. Anything else
# recorded as a marker is held to the encrypted-encoding bar.
_SDK_MARKER_NAMES = ("core_patch",)


def _is_sdk_marker_event(event: Any) -> bool:
    return (
        event.WhichOneof("attributes") == "marker_recorded_event_attributes"
        and event.marker_recorded_event_attributes.marker_name in _SDK_MARKER_NAMES
    )


def _walk_payloads(message: Any, path: str) -> Iterator[tuple[str, Any]]:
    descriptor = message.DESCRIPTOR
    if descriptor.full_name == "temporal.api.common.v1.Payload":
        yield path, message
        return
    for field, value in message.ListFields():
        if field.type != field.TYPE_MESSAGE:
            continue
        field_path = f"{path}.{field.name}"
        if field.message_type is not None and field.message_type.GetOptions().map_entry:
            entry_type = field.message_type.fields_by_name["value"]
            if entry_type.type != field.TYPE_MESSAGE:
                continue
            for key in value:
                yield from _walk_payloads(value[key], f"{field_path}[{key}]")
        elif hasattr(value, "DESCRIPTOR"):
            # A singular message field: the value is the message itself.
            yield from _walk_payloads(value, field_path)
        else:
            # A repeated message field: the value is a container of messages.
            for index, item in enumerate(value):
                yield from _walk_payloads(item, f"{field_path}[{index}]")


def _assert_history_is_ciphertext(events: list[Any], markers: list[str]) -> None:
    assert events, "fetched an empty history"
    assert markers and all(markers), "marker list must name real plaintext material"

    runtime_payloads = 0
    for event in events:
        event_path = f"events[{event.event_id}].{event.WhichOneof('attributes')}"

        # No marker may appear anywhere in the raw event — payload bodies,
        # headers, memos, anything. Encrypted payloads cannot contain them.
        raw = event.SerializeToString()
        for marker in markers:
            assert marker.encode() not in raw, (
                f"plaintext marker {marker!r} leaked into raw history at {event_path}"
            )

        # Every runtime-produced payload is ciphertext, declared as such.
        if _is_sdk_marker_event(event):
            continue
        for path, payload in _walk_payloads(event, f"events[{event.event_id}]"):
            if any(fragment in path for fragment in _SERVER_OWNED_PATH_FRAGMENTS):
                continue
            encoding = payload.metadata.get("encoding", b"")
            assert encoding == ENCODING, (
                f"payload at {path} has encoding {encoding!r}; every workflow "
                f"payload must be {ENCODING!r}"
            )
            runtime_payloads += 1

    # The scan must have actually seen workflow data (inputs and results at
    # minimum) or the assertions above were vacuous.
    assert runtime_payloads >= 4, f"only {runtime_payloads} runtime payloads found"


async def _fetch_raw_history(address: str, namespace: str, workflow_id: str) -> list[Any]:
    # Deliberately no payload codec on this client: history must be read the
    # way Temporal stores it, so decryption cannot mask a leak.
    client = await Client.connect(address, namespace=namespace, tls=temporal_tls_from_env())
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    return list(history.events)


def test_compose_history_is_ciphertext(api: httpx.Client) -> None:
    sentinel = f"ciphertext-probe-{uuid.uuid4().hex[:8]}"
    goal = f"prove temporal only sees ciphertext {sentinel}"
    run_id = f"run-e2e-cipher-{uuid.uuid4().hex[:8]}"
    created = api.post(
        "/runs",
        json={"goal": goal, "success_criteria": ["history is ciphertext"], "run_id": run_id},
        headers=auth_headers(),
    )
    assert created.status_code == 202
    events = collect_sse(api, run_id, terminal={"run_completed", "run_failed"})
    assert events[-1]["type"] == "run_completed"

    run: dict[str, Any] = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    descriptions = [str(step["description"]) for step in run["steps"]]
    assert descriptions

    markers = [
        goal,  # the run goal
        sentinel,  # its unique probe token
        "tenant-e2e",  # the tenant id
        f"runs/{run_id}/",  # artifact keys under the run prefix
        *descriptions,  # plan step descriptions
    ]
    history = asyncio.run(_fetch_raw_history("localhost:7233", "default", run_id))
    _assert_history_is_ciphertext(history, markers)


@pytest.mark.skipif(
    not os.environ.get("CONVOY_CIPHERTEXT_RUN_ID"),
    reason=(
        "live codec verification targets a stamped stack: set TEMPORAL_ADDRESS, "
        "TEMPORAL_NAMESPACE, the TEMPORAL_TLS_*_PEM pair, CONVOY_CIPHERTEXT_RUN_ID, "
        "and CONVOY_CIPHERTEXT_MARKERS"
    ),
)
def test_live_history_is_ciphertext() -> None:
    run_id = os.environ["CONVOY_CIPHERTEXT_RUN_ID"]
    markers = [m.strip() for m in os.environ.get("CONVOY_CIPHERTEXT_MARKERS", "").split(",")]
    markers = [m for m in markers if m]
    assert markers, "CONVOY_CIPHERTEXT_MARKERS must list the run's plaintext markers"

    history = asyncio.run(
        _fetch_raw_history(
            os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
            os.environ.get("TEMPORAL_NAMESPACE", "default"),
            run_id,
        )
    )
    _assert_history_is_ciphertext(history, markers)
