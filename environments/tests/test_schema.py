from convoy_environments.schema import (
    BrowserPolicy,
    Connection,
    ConnectionManifest,
    Environment,
    EnvironmentConnection,
    ToolSpec,
    canonical_json,
    policy_hash,
)


def _manifest():
    return ConnectionManifest(
        tools=[
            ToolSpec(name="slack.post_message", execution="promoted", sideEffecting=True),
            ToolSpec(name="slack.list_channels", execution="inline", sideEffecting=False),
        ]
    )


def test_canonical_json_is_order_insensitive():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})


def test_manifest_hash_changes_on_any_tool_change():
    m1 = _manifest()
    m2 = _manifest()
    assert m1.hash == m2.hash
    m2.tools[1].sideEffecting = True
    assert m1.hash != m2.hash


def test_policy_hash_is_connection_order_insensitive_but_content_sensitive():
    a = EnvironmentConnection(connectionId="c-a", manifestHash="h1", toolAllowlist=["x", "y"])
    b = EnvironmentConnection(connectionId="c-b", manifestHash="h2", toolAllowlist=["z"])
    h_ab = policy_hash("live", [a, b])
    h_ba = policy_hash("live", [b, a])
    assert h_ab == h_ba
    # allowlist order is also canonicalized
    a2 = EnvironmentConnection(connectionId="c-a", manifestHash="h1", toolAllowlist=["y", "x"])
    assert policy_hash("live", [a2, b]) == h_ab
    # but content changes the hash
    a3 = EnvironmentConnection(connectionId="c-a", manifestHash="h1", toolAllowlist=["x"])
    assert policy_hash("live", [a3, b]) != h_ab
    # and so does backing type
    assert policy_hash("hermetic", [a, b]) != h_ab


def test_environment_policy_hash_includes_browser_policy():
    env = Environment(
        environmentId="env1", workspaceId="w1", name="renewal-prep", version=1,
        connections=[EnvironmentConnection(connectionId="c1", manifestHash="h1", toolAllowlist=["t"])],
    )
    h_no_browser = env.policyHash
    env2 = env.model_copy(update={"browserPolicy": BrowserPolicy(allowedDomains=["dmv.ca.gov"])})
    assert env2.policyHash != h_no_browser


def test_connection_manifest_hash_exposed():
    conn = Connection(
        connectionId="c1", workspaceId="w1", kind="mcp_managed", provider="slack",
        displayName="Slack", manifest=_manifest(), secretRef="sec-1",
    )
    assert conn.manifestHash == _manifest().hash
