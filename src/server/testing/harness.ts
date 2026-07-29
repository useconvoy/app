import { db, persist } from "../db";
import { emit } from "../events";
import { id, now } from "../ids";
import { startRun, advanceRun, finishRun } from "../runtime/loop";
import { SANDBOX_ID } from "../seed";
import type { AssertionResult, AssertionSpec, CrmDeal, Run, TestRun, TestScenario } from "../types";

// Test harness (spec §6.6): scenario = {fixture, trigger, assertions[]}.
// Fixture seeding is idempotent (namespaced records, torn down after), and
// every assertion is checked against actual sandbox system state — never
// against agent self-report.

export async function runSuite(agentVersionId: string): Promise<TestRun[]> {
  const d = db();
  const version = d.agentVersions.find((v) => v.id === agentVersionId);
  if (!version) throw new Error("Unknown agent version");
  const agent = d.agents.find((a) => a.id === version.agentId)!;
  const scenarios = d.testScenarios.filter((s) => s.agentTemplateId === agent.templateId);
  if (scenarios.length === 0) throw new Error(`No scenario suite exists for '${agent.name}'`);
  const deployment = d.deployments.find(
    (x) => x.agentVersionId === agentVersionId && x.environmentId === SANDBOX_ID && x.status === "active"
  );
  if (!deployment) throw new Error("Bind this version to Sandbox before running the suite");

  const results: TestRun[] = [];
  for (const scenario of scenarios) {
    results.push(await runScenario(agentVersionId, deployment.id, scenario));
  }
  return results;
}

async function runScenario(agentVersionId: string, deploymentId: string, scenario: TestScenario): Promise<TestRun> {
  const d = db();
  const testRun: TestRun = {
    id: id("tr"),
    agentVersionId,
    scenarioId: scenario.id,
    runId: "",
    status: "running",
    results: [],
    at: now(),
  };
  d.testRuns.push(testRun);
  persist();
  emit({ type: "test_run_updated", payload: testRun });

  // Seed the namespaced fixture into the sandbox CRM, already at the trigger stage.
  const nsKey = `fixture:${testRun.id}`;
  const mainDeal: CrmDeal = {
    id: id("deal"),
    environmentId: SANDBOX_ID,
    stage: "closedwon",
    ownerName: "Test Harness",
    fields: {},
    namespace: nsKey,
    updatedAt: now(),
    name: "",
    company: "",
    amount: 0,
    currency: "USD",
    contactName: "",
    contactEmail: "",
    poNumber: null,
    discountPct: 0,
    termMonths: 12,
    ...scenario.fixture.deal,
  } as CrmDeal;
  d.crmDeals.push(mainDeal);
  for (const extra of scenario.fixture.extraDeals ?? []) {
    d.crmDeals.push({ ...mainDeal, ...extra, id: id("deal"), namespace: nsKey } as CrmDeal);
  }
  persist();

  const run = startRun({
    deploymentId,
    triggerType: "test",
    triggerPayload: { dealId: mainDeal.id, event: "deal.stage_changed", to_stage: "closedwon" },
    scenarioId: scenario.id,
  });
  testRun.runId = run.id;
  persist();
  await advanceRun(run.id);

  // Evaluate assertions against actual system state.
  const freshRun = db().runs.find((r) => r.id === run.id)!;
  const artifactNs = `ns:${run.id}`;
  testRun.results = scenario.assertions.map((spec) => evaluate(spec, freshRun, mainDeal, artifactNs));
  testRun.status = testRun.results.every((r) => r.pass) ? "pass" : "fail";
  persist();
  emit({ type: "test_run_updated", payload: testRun });

  // Teardown: resolve any expected pause, then remove namespaced fixtures + artifacts.
  teardown(freshRun, nsKey, artifactNs);
  return testRun;
}

function evaluate(spec: AssertionSpec, run: Run, deal: CrmDeal, artifactNs: string): AssertionResult {
  const d = db();
  const docs = d.docs.filter((x) => x.namespace === artifactNs);
  const emails = d.emails.filter((x) => x.namespace === artifactNs);
  const chats = d.chatMessages.filter((x) => x.namespace === artifactNs);
  const flags = d.approvals.filter((a) => a.runId === run.id);

  switch (spec.type) {
    case "run_state": {
      const pass = run.state === spec.expect;
      return { spec, pass, detail: pass ? `run is ${run.state}` : `expected ${spec.expect}, run is ${run.state}` };
    }
    case "flag_raised": {
      const hit = flags.find((f) => f.kind === spec.kind && (f.reason ?? "").includes(spec.reasonContains));
      return { spec, pass: !!hit, detail: hit ? `flag raised: "${hit.reason}"` : `no ${spec.kind} flag containing "${spec.reasonContains}" (${flags.length} approval item(s))` };
    }
    case "no_flag_raised":
      return { spec, pass: flags.length === 0, detail: flags.length === 0 ? "no approval items raised" : `${flags.length} approval item(s) raised` };
    case "crm_deal_field": {
      const fresh = d.crmDeals.find((x) => x.id === deal.id);
      const value = fresh?.fields[spec.field] ?? "";
      const pass = value.includes(spec.expectContains);
      return { spec, pass, detail: `CRM ${spec.field}="${value}"` };
    }
    case "doc_exists": {
      const doc = docs.find((x) => x.title.includes(spec.titleContains));
      if (!doc) return { spec, pass: false, detail: `no doc titled *${spec.titleContains}* (${docs.length} doc(s) created)` };
      if (spec.contentContains && !doc.content.includes(spec.contentContains)) {
        return { spec, pass: false, detail: `doc '${doc.title}' exists but does not contain "${spec.contentContains}"` };
      }
      return { spec, pass: true, detail: `doc '${doc.title}' exists${spec.contentContains ? ` and contains "${spec.contentContains}"` : ""}` };
    }
    case "no_doc": {
      const doc = docs.find((x) => x.title.includes(spec.titleContains));
      return { spec, pass: !doc, detail: doc ? `doc '${doc.title}' was created` : `no doc titled *${spec.titleContains}*` };
    }
    case "email_sent": {
      const email = emails.find((x) => x.to.includes(spec.toContains) && (!spec.bodyContains || x.body.includes(spec.bodyContains)));
      return { spec, pass: !!email, detail: email ? `email sent to ${email.to}` : `no matching email in sandbox outbox (${emails.length} sent)` };
    }
    case "no_email_sent":
      return { spec, pass: emails.length === 0, detail: emails.length === 0 ? "no email sent" : `${emails.length} email(s) sent` };
    case "chat_posted": {
      const msg = chats.find((x) => x.text.includes(spec.textContains));
      return { spec, pass: !!msg, detail: msg ? `posted to ${msg.channel}` : "no matching chat message" };
    }
  }
}

function teardown(run: Run, fixtureNs: string, artifactNs: string): void {
  const d = db();
  // Close out expected pauses so the approvals queue stays clean.
  for (const approval of d.approvals.filter((a) => a.runId === run.id && a.status === "pending")) {
    approval.status = "rejected";
    approval.approver = "Test harness (auto-teardown)";
    approval.decidedAt = now();
    const tc = d.toolCalls.find((t) => t.id === approval.toolCallId);
    if (tc) tc.status = "rejected";
  }
  if (run.state === "paused_pending_approval") {
    finishRun(run, "rejected", `${run.summary ?? ""}Scenario verified the expected pause; harness closed the run at teardown.`.trim());
  }
  d.crmDeals = d.crmDeals.filter((x) => x.namespace !== fixtureNs);
  d.docs = d.docs.filter((x) => x.namespace !== artifactNs);
  d.emails = d.emails.filter((x) => x.namespace !== artifactNs);
  d.chatMessages = d.chatMessages.filter((x) => x.namespace !== artifactNs);
  persist();
}

export function latestSuiteStatus(agentVersionId: string): { total: number; passed: number; runs: TestRun[] } {
  const d = db();
  const version = d.agentVersions.find((v) => v.id === agentVersionId);
  const agent = version && d.agents.find((a) => a.id === version.agentId);
  const scenarios = agent ? d.testScenarios.filter((s) => s.agentTemplateId === agent.templateId) : [];
  const latest: TestRun[] = [];
  for (const s of scenarios) {
    const runs = d.testRuns
      .filter((t) => t.agentVersionId === agentVersionId && t.scenarioId === s.id)
      .sort((a, b) => b.at.localeCompare(a.at));
    if (runs[0]) latest.push(runs[0]);
  }
  return { total: scenarios.length, passed: latest.filter((t) => t.status === "pass").length, runs: latest };
}
