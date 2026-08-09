from convoy_environments.console_api import build_console_app
from convoy_environments.db import Base, make_session_factory
from convoy_environments.gateway import GatewayService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class _UnusedSecrets:
    pass


def test_named_environment_inherits_workspace_grants_and_compiles_bindings() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    secrets = _UnusedSecrets()
    app = build_console_app(
        sessions,
        secrets,
        provisioning_token="test-internal-token",
    )
    client = TestClient(app)

    organization = client.post(
        "/organizations",
        headers={"X-Convoy-Internal": "test-internal-token"},
        json={"name": "Acme", "creatorEmail": "admin@example.com"},
    ).json()
    org_id = organization["organizationId"]
    user_id = organization["userId"]
    headers = {"X-Convoy-User": user_id}

    workspace = client.post(
        f"/organizations/{org_id}/workspaces",
        headers=headers,
        json={
            "name": "Operations",
            "purpose": "Owns operational systems",
            "sandboxTemplate": "workspace-default",
        },
    )
    assert workspace.status_code == 200, workspace.text
    workspace_id = workspace.json()["workspaceId"]

    created = client.post(
        f"/organizations/{org_id}/workspaces/{workspace_id}/environments",
        headers=headers,
        json={
            "name": "Production operations",
            "purpose": "Runs approved operational routines",
            "sandboxTemplate": "convoy-devbox-python",
            "browserPolicy": {
                "allowedDomains": ["app.example.com"],
                "persistProfile": True,
            },
        },
    )
    assert created.status_code == 200, created.text
    environment = created.json()
    assert environment["workspaceId"] == workspace_id
    assert environment["productionBindingId"] == environment["environmentId"]
    assert environment["rehearsalBindingId"] == f'{environment["environmentId"]}/sandbox'

    workspaces = client.get(f"/organizations/{org_id}/workspaces", headers=headers).json()
    assert [row["workspaceId"] for row in workspaces] == [workspace_id]
    environments = client.get(
        f"/organizations/{org_id}/workspaces/{workspace_id}/environments",
        headers=headers,
    ).json()
    assert [row["environmentId"] for row in environments] == [environment["environmentId"]]

    binding = GatewayService(sessions, secrets).environment_binding(
        environment["environmentId"], base_url="http://environments.test"
    )
    assert binding.tenant_id == org_id
    assert binding.sandbox_template == "convoy-devbox-python"
    assert binding.data_namespace == f'{org_id}/{environment["environmentId"]}'
