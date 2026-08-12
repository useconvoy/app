import httpx
from convoy_environments.db import Base, SqlEventLog, make_session_factory
from convoy_environments.db.tables import Connection, Environment, EnvironmentConnection, Organization
from convoy_environments.gateway import GatewayService
from convoy_environments.gateway.mcp_server import build_app as build_gateway_app
from convoy_environments.schema import ConnectionManifest, ToolSpec
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class _NeverRevealSecrets:
    def reveal(self, _secret_ref: str) -> str:
        raise AssertionError("a rehearsal must never reveal a connector credential")


def _service(connection_config=None, transport=None, sandbox_url=""):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    manifest = ConnectionManifest(
        tools=[
            ToolSpec(name="slack.list_channels", execution="inline", sideEffecting=False),
            ToolSpec(name="slack.post_message", execution="promoted", sideEffecting=True),
        ]
    )
    with sessions() as session:
        session.add(Organization(id="org-1", name="Sandbox organization"))
        session.add(
            Connection(
                id="conn-1",
                organization_id="org-1",
                kind="mcp_managed",
                provider="slack",
                display_name="Slack",
                config=connection_config
                or {
                    "rehearsalFixtures": {
                        "slack.list_channels": {
                            "ok": True,
                            "channels": [{"id": "C-DEMO", "name": "demo"}],
                        }
                    }
                },
                secret_ref=None,
                manifest=manifest.model_dump(),
                manifest_hash=manifest.hash,
                status="active",
            )
        )
        session.add(
            Environment(
                id="env-1",
                version=1,
                organization_id="org-1",
                name="Demo",
                backing_type="live",
                browser_policy=None,
                sandbox_template="python",
                data_namespace="org-1/env-1",
                policy_hash="policy",
            )
        )
        session.add(
            EnvironmentConnection(
                environment_id="env-1",
                environment_version=1,
                connection_id="conn-1",
                manifest_hash=manifest.hash,
                tool_allowlist=["slack.list_channels", "slack.post_message"],
                promote_overrides=[],
            )
        )
        session.commit()
    return sessions, GatewayService(
        sessions,
        _NeverRevealSecrets(),
        transport=transport,
        sandbox_url=sandbox_url,
    )


def test_rehearsal_binding_routes_to_standins_and_journals_one_effect() -> None:
    sessions, service = _service()
    binding = service.environment_binding("env-1", kind="sandbox", base_url="http://gateway.test")
    assert str(binding.connector_endpoints["data_plane"]) == (
        "http://gateway.test/simulated-data-plane/env-1/1"
    )
    assert "slack.post_message" in {grant.tool_id for grant in binding.tool_registry}

    app = build_gateway_app(
        service,
        internal_token="internal",
        public_url="http://gateway.test",
        allow_anonymous_data_plane=False,
    )
    client = TestClient(app)
    path = "/simulated-data-plane/env-1/1"
    headers = {"X-Convoy-Internal": "internal"}
    read = client.post(
        path + "/tools/slack.list_channels",
        json={"run_id": "run-1", "args": {}},
        headers=headers,
    )
    assert read.status_code == 200
    assert read.json()["result"]["channels"][0]["name"] == "demo"

    effect_body = {
        "run_id": "run-1",
        "idempotency_key": "same-key",
        "args": {"channel": "C-DEMO", "text": "hello"},
    }
    first = client.post(path + "/effects/slack.post_message", json=effect_body, headers=headers)
    replay = client.post(path + "/effects/slack.post_message", json=effect_body, headers=headers)
    assert first.status_code == 200
    assert first.json()["result"]["simulated"] is True
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True

    events = SqlEventLog(sessions).for_mission("run-1")
    assert [event.type for event in events].count("simulated_effect") == 1


def test_managed_rehearsal_calls_provider_sandbox_through_real_connector() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(201, json={"sandboxId": "created"})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [{"id": "C_OPERATIONS", "name": "operations"}],
            },
        )

    sessions, service = _service(
        transport=httpx.MockTransport(handler),
        sandbox_url="http://sandbox.test",
    )
    app = build_gateway_app(
        service,
        internal_token="internal",
        public_url="http://gateway.test",
        allow_anonymous_data_plane=False,
    )
    response = TestClient(app).post(
        "/simulated-data-plane/env-1/1/tools/slack.list_channels",
        json={"run_id": "run-stateful", "args": {}},
        headers={"X-Convoy-Internal": "internal"},
    )

    assert response.status_code == 200
    assert response.json()["result"]["channels"][0]["name"] == "operations"
    assert seen[0] == "http://sandbox.test/v1/sandboxes"
    assert seen[1].startswith("http://sandbox.test/s/run-")
    assert seen[1].endswith("/slack/api/conversations.list?limit=100&types=public_channel")
    event = SqlEventLog(sessions).for_mission("run-stateful")[-1]
    assert event.result["channels"][0]["name"] == "operations"
