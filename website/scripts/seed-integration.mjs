#!/usr/bin/env node
/**
 * Seed the integrated three-system stack with one runnable Agent, then
 * launch a rehearsal run through all three services:
 *
 *   website Postgres  — org, user, admin membership, workspace + agent rows
 *   environments      — registry org, Slack connection, workspace policy,
 *                       agent runtime (production + rehearsal bindings)
 *   agent runtime     — POST /runs against the rehearsal binding; the
 *                       scripted executor's promoted call exercises the
 *                       gateway's simulated data plane end to end
 *
 * Prerequisites (see website/scripts/local-stack.sh + dev-runtime.sh):
 *   - website migrations applied to convoy_website
 *   - environments service on :8780 (gateway + console)
 *   - runtime worker + control plane on :8700 pointed at the real gateway
 *   - sandbox control plane on :8790 (optional; stand-ins cover its absence)
 *
 * Idempotent: reruns reuse the org, connection, workspace, and agent, and
 * only launch a fresh run. Pass --no-run to seed without launching.
 */

import pg from "pg";
import { randomBytes, randomUUID } from "node:crypto";

const cfg = {
  websiteAdminDsn:
    process.env.WEBSITE_PG_ADMIN_DSN ??
    "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy_website",
  environmentsUrl: (process.env.CONVOY_ENVIRONMENTS_URL ?? "http://localhost:8780/console")
    .replace(/\/+$/, ""),
  internalToken:
    process.env.CONVOY_ENVIRONMENTS_INTERNAL_TOKEN ?? "dev-internal-token-demo-profile-only",
  controlPlaneUrl: (process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700")
    .replace(/\/+$/, ""),
  controlPlaneToken: process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token",
  email: process.env.SEED_EMAIL ?? "demo@convoylabs.dev",
  userName: process.env.SEED_NAME ?? "Convoy Demo",
  orgName: process.env.SEED_ORG_NAME ?? "Convoy Demo Org",
  launchRun: !process.argv.includes("--no-run"),
};

/** The Messaging (Slack) grant, mirroring lib/workspaces/system-catalog. */
const SLACK_TOOLS = [
  { name: "slack.list_channels", execution: "inline", sideEffecting: false },
  { name: "slack.read_messages", execution: "inline", sideEffecting: false },
  { name: "slack.post_message", execution: "promoted", sideEffecting: true },
];

async function registry(path, { method = "GET", body, actsFor } = {}) {
  const headers = { "X-Convoy-Internal": cfg.internalToken };
  if (actsFor) headers["X-Convoy-Acts-For"] = actsFor;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${cfg.environmentsUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(
      `environments registry ${method} ${path} -> ${response.status}: ${await response.text()}`,
    );
  }
  return response.json();
}

async function controlPlane(path, { method = "GET", body, tenantId } = {}) {
  const headers = {
    Authorization: `Bearer ${cfg.controlPlaneToken}`,
    "X-Actor-Id": cfg.email,
    "X-Tenant-Id": tenantId,
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${cfg.controlPlaneUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(
      `control plane ${method} ${path} -> ${response.status}: ${await response.text()}`,
    );
  }
  return response.json();
}

async function main() {
  const db = new pg.Client({ connectionString: cfg.websiteAdminDsn });
  await db.connect();
  try {
    // -- website identity: user, org, admin membership ---------------------
    const user = await db.query(
      `INSERT INTO users (id, email, name) VALUES ($1, $2, $3)
       ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
       RETURNING id`,
      [randomUUID(), cfg.email, cfg.userName],
    );
    const userId = user.rows[0].id;

    let org = await db.query(
      `SELECT id, tenant_id, environments_org_id FROM organizations WHERE name = $1`,
      [cfg.orgName],
    );
    if (org.rows.length === 0) {
      org = await db.query(
        `INSERT INTO organizations (id, tenant_id, name)
         VALUES ($1, $2, $3) RETURNING id, tenant_id, environments_org_id`,
        [randomUUID(), `tenant-${randomBytes(6).toString("hex")}`, cfg.orgName],
      );
    }
    const orgId = org.rows[0].id;
    await db.query(
      `INSERT INTO memberships (org_id, user_id, role, status)
       VALUES ($1, $2, 'admin', 'active')
       ON CONFLICT (org_id, user_id) DO UPDATE SET role = 'admin', status = 'active'`,
      [orgId, userId],
    );
    console.log(`org ${cfg.orgName} (${orgId}) with admin ${cfg.email}`);

    // -- registry org (the runtime tenant, per migration 0010) -------------
    let registryOrgId = org.rows[0].environments_org_id;
    if (!registryOrgId) {
      const provisioned = await registry("/organizations", {
        method: "POST",
        body: { name: cfg.orgName, creatorEmail: cfg.email },
      });
      registryOrgId = provisioned.organizationId;
      await db.query(`UPDATE organizations SET environments_org_id = $1 WHERE id = $2`, [
        registryOrgId,
        orgId,
      ]);
    }
    console.log(`registry org ${registryOrgId}`);

    // -- Slack connection (declared manifest; no credential: rehearsal-only,
    //    the gateway substitutes synthetic sandbox credentials) ------------
    const connections = await registry(`/organizations/${registryOrgId}/connections`, {
      actsFor: cfg.email,
    });
    let connection = connections.find((row) => row.provider === "slack");
    if (!connection) {
      connection = await registry(`/organizations/${registryOrgId}/connections`, {
        method: "POST",
        actsFor: cfg.email,
        body: {
          kind: "mcp_managed",
          provider: "slack",
          displayName: "Messaging (Slack)",
          config: {},
          manifest: {
            tools: SLACK_TOOLS.map((tool) => ({
              name: tool.name,
              description: tool.name,
              inputSchema: { type: "object" },
              execution: tool.execution,
              sideEffecting: tool.sideEffecting,
            })),
            domains: [],
          },
        },
      });
    }
    const connectionId = connection.connectionId;
    console.log(`slack connection ${connectionId}`);

    // -- registry workspace (the shared grant boundary) --------------------
    const workspaces = await registry(`/organizations/${registryOrgId}/workspaces`, {
      actsFor: cfg.email,
    });
    let workspace = workspaces.find((row) => row.name === "Demo Workspace");
    if (!workspace) {
      workspace = await registry(`/organizations/${registryOrgId}/workspaces`, {
        method: "POST",
        actsFor: cfg.email,
        body: {
          name: "Demo Workspace",
          purpose: "Integration smoke: Slack rehearsal runs",
          connections: [
            { connectionId, toolAllowlist: SLACK_TOOLS.map((tool) => tool.name) },
          ],
          sandboxTemplate: "convoy-devbox-python",
        },
      });
    }
    const registryWorkspaceId = workspace.workspaceId;
    console.log(`registry workspace ${registryWorkspaceId}`);

    // -- registry agent runtime (nested execution environment) -------------
    const runtimes = await registry(
      `/organizations/${registryOrgId}/workspaces/${registryWorkspaceId}/environments`,
      { actsFor: cfg.email },
    );
    let runtime = runtimes.find((row) => row.name === "Demo Agent");
    if (!runtime) {
      runtime = await registry(
        `/organizations/${registryOrgId}/workspaces/${registryWorkspaceId}/environments`,
        {
          method: "POST",
          actsFor: cfg.email,
          body: {
            name: "Demo Agent",
            purpose: "Integration smoke agent",
            sandboxTemplate: "convoy-devbox-python",
          },
        },
      );
    }
    console.log(
      `agent runtime ${runtime.environmentId} (production ${runtime.productionBindingId}, rehearsal ${runtime.rehearsalBindingId})`,
    );

    // -- mirror rows the console reads (workspace grants + agent) ----------
    const grants = [
      {
        systemId: "messaging",
        displayName: "Messaging",
        scope: "write",
        sideEffecting: true,
        standIn: {
          note: "Stand-in for Messaging: messages are held in the outbox instead of being sent.",
        },
        tools: SLACK_TOOLS.map((tool) => ({
          name: tool.name,
          sideEffecting: tool.sideEffecting,
        })),
      },
    ];
    const workspaceRow = await db.query(
      `INSERT INTO workspaces (id, org_id, name, purpose, environment_id,
         rehearsal_environment_id, systems, clock_mode, versions, environments_workspace_id)
       VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, 'wall', '[]'::jsonb, $8)
       ON CONFLICT (org_id, environments_workspace_id) WHERE environments_workspace_id IS NOT NULL
       DO UPDATE SET systems = EXCLUDED.systems
       RETURNING id`,
      [
        randomUUID(),
        orgId,
        workspace.name ?? "Demo Workspace",
        "Integration smoke: Slack rehearsal runs",
        registryWorkspaceId,
        `${registryWorkspaceId}/sandbox`,
        JSON.stringify(grants),
        registryWorkspaceId,
      ],
    );
    const workspaceId = workspaceRow.rows[0].id;

    const goal =
      "Post the weekly team summary to the demo Slack channel after reading recent messages.";
    const agentRow = await db.query(
      `INSERT INTO agents (id, org_id, workspace_id, registry_environment_id, name,
         purpose, production_binding_id, rehearsal_binding_id, sandbox_template,
         version, is_default, created_by, goal, systems, budget_cap_usd,
         plan_steps, automation_configured)
       VALUES ($1, $2, $3, $4, 'Demo Agent', 'Integration smoke agent', $5, $6,
         'convoy-devbox-python', $7, true, $8, $9, $10, 25,
         '["Read the latest messages", "Post the summary"]'::jsonb, true)
       ON CONFLICT (org_id, registry_environment_id) WHERE registry_environment_id IS NOT NULL
       DO UPDATE SET goal = EXCLUDED.goal, systems = EXCLUDED.systems,
         automation_configured = true
       RETURNING id`,
      [
        randomUUID(),
        orgId,
        workspaceId,
        runtime.environmentId,
        runtime.productionBindingId,
        runtime.rehearsalBindingId,
        runtime.version ?? 1,
        userId,
        goal,
        ["messaging"],
      ],
    );
    const agentId = agentRow.rows[0].id;
    console.log(`console agent ${agentId} in workspace ${workspaceId}`);

    if (!cfg.launchRun) {
      console.log("seeded (run skipped: --no-run)");
      return;
    }

    // -- rehearsal run through all three systems ---------------------------
    const run = await controlPlane("/runs", {
      method: "POST",
      tenantId: registryOrgId,
      body: {
        goal,
        environment_id: runtime.rehearsalBindingId,
        budget_usd: 25,
        tools: SLACK_TOOLS.map((tool) => tool.name),
        success_criteria: [],
        fixture_gates: {},
        max_children: 5,
      },
    });
    console.log(`run ${run.run_id} accepted (${run.status})`);

    const deadline = Date.now() + 90_000;
    let view = null;
    while (Date.now() < deadline) {
      view = await controlPlane(`/runs/${run.run_id}`, { tenantId: registryOrgId });
      if (["landed", "completed", "failed", "aborted"].includes(view.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    const steps = (view?.steps ?? [])
      .map((step) => `${step.step_id}:${step.status}`)
      .join(", ");
    console.log(`run ${run.run_id} -> ${view?.status} [${steps}]`);
    console.log(
      `sign in as ${cfg.email} at http://localhost:3000/sign-in to see it in the console`,
    );
    if (!["landed", "completed"].includes(view?.status ?? "")) {
      process.exitCode = 1;
    }
  } finally {
    await db.end();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
