import { Client, Connection } from "@temporalio/client";
import { NativeConnection, Worker } from "@temporalio/worker";
import { loadTemporalConfig } from "./aws";
import * as activities from "./activities";
import type { MissionResult, MissionSpec } from "./contracts";
import { missionWorkflow } from "./workflows";

async function createWorker(
  temporal: Awaited<ReturnType<typeof loadTemporalConfig>>,
  taskQueue: string,
): Promise<{ worker: Worker; connection: NativeConnection }> {
  const connection = await NativeConnection.connect({
    address: temporal.endpoint,
    tls: true,
    apiKey: temporal.apiKey,
  });
  const worker = await Worker.create({
    connection,
    namespace: temporal.namespace,
    taskQueue,
    workflowsPath: require.resolve("./workflows"),
    activities,
  });
  return { worker, connection };
}

export async function runWorkerService(): Promise<void> {
  const temporal = await loadTemporalConfig();
  const taskQueue = process.env.TEMPORAL_TASK_QUEUE ?? "convoy-poc";
  const { worker, connection } = await createWorker(temporal, taskQueue);

  console.log(JSON.stringify({ event: "worker.ready", taskQueue }));
  try {
    await worker.run();
  } finally {
    await connection.close();
  }
}

export async function runCoordinator(): Promise<void> {
  const temporal = await loadTemporalConfig();
  const taskQueue = process.env.TEMPORAL_TASK_QUEUE ?? "convoy-poc";
  const maxFanout = Number(process.env.DEFAULT_MAX_FANOUT ?? "3");
  const maxDepth = Number(process.env.DEFAULT_MAX_DEPTH ?? "2");
  const maxTotalAgents = Number(process.env.DEFAULT_MAX_TOTAL_AGENTS ?? "13");
  const maxParallelAgents = Number(
    process.env.DEFAULT_MAX_PARALLEL_AGENTS ?? "6",
  );
  const missionId =
    process.env.MISSION_ID ??
    `convoy-poc-${new Date().toISOString().replace(/[:.]/g, "-")}`;

  const input: MissionSpec = {
    missionId,
    objective:
      process.env.MISSION_OBJECTIVE ??
      "Research an enterprise problem, test competing hypotheses, and synthesize an evidence-backed recommendation.",
    deadline: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    maxParallelAgents,
    maxTotalAgents,
    maxDepth,
    checkpointIntervalSeconds: 60,
    computeMode: "economy",
    budget: {
      maxCostCents: Number(process.env.DEFAULT_MAX_COST_CENTS ?? "100"),
      estimatedCostPerAgentCents: Number(
        process.env.ESTIMATED_COST_PER_AGENT_CENTS ?? "1",
      ),
    },
    deploymentId: process.env.DEPLOYMENT_ID ?? "convoy-poc",
    environmentId: process.env.ENVIRONMENT_ID ?? "aws-poc",
    policyRef: process.env.POLICY_REF ?? "policy://convoy/poc/default",
    toolRefs: (process.env.TOOL_REFS ?? "tool://s3,tool://dynamodb").split(","),
    inputRefs: (process.env.INPUT_REFS ?? "").split(",").filter(Boolean),
  };

  const [{ worker, connection: nativeConnection }, clientConnection] =
    await Promise.all([
      createWorker(temporal, taskQueue),
    Connection.connect({
      address: temporal.endpoint,
      tls: true,
      apiKey: temporal.apiKey,
    }),
  ]);
  const client = new Client({
    connection: clientConnection,
    namespace: temporal.namespace,
  });

  console.log(
    JSON.stringify({
      event: "mission.starting",
      missionId,
      taskQueue,
      maxFanout,
      maxDepth,
      maxTotalAgents,
      maxParallelAgents,
    }),
  );

  const result = await worker.runUntil(async (): Promise<MissionResult> => {
    const handle = await client.workflow.start(missionWorkflow, {
      taskQueue,
      workflowId: missionId,
      workflowExecutionTimeout: "15 minutes",
      args: [input],
    });
    return handle.result();
  });

  await Promise.all([nativeConnection.close(), clientConnection.close()]);
  console.log(JSON.stringify({ event: "mission.completed", result }));
}
