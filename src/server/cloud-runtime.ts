import { ECSClient, RunTaskCommand } from "@aws-sdk/client-ecs";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand } from "@aws-sdk/lib-dynamodb";
import type { CloudMission } from "./types";

const ecs = new ECSClient({});
const dynamodb = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable ${name}`);
  return value;
}

export function isCloudRuntimeEnabled(): boolean {
  return process.env.CONVOY_CLOUD_RUNTIME === "1";
}

export interface CloudMissionLaunch {
  objective: string;
  workspaceId?: string;
  deadline?: string;
  maxParallelAgents?: number;
  maxTotalAgents?: number;
  maxDepth?: number;
  checkpointIntervalSeconds?: number;
  computeMode?: "auto" | "fast" | "economy" | "dedicated";
  budgetCents?: number;
  deploymentId?: string;
  environmentId?: string;
  policyRef?: string;
  toolRefs?: string[];
  inputRefs?: string[];
}

export async function startCloudMission(
  missionId: string,
  input: string | CloudMissionLaunch,
): Promise<string> {
  const launch: CloudMissionLaunch =
    typeof input === "string" ? { objective: input } : input;
  const environment = [
    { name: "MISSION_ID", value: missionId },
    { name: "MISSION_OBJECTIVE", value: launch.objective },
    launch.workspaceId
      ? { name: "WORKSPACE_ID", value: launch.workspaceId }
      : undefined,
    launch.deadline
      ? { name: "MISSION_DEADLINE", value: launch.deadline }
      : undefined,
    launch.maxParallelAgents !== undefined
      ? {
          name: "DEFAULT_MAX_PARALLEL_AGENTS",
          value: String(launch.maxParallelAgents),
        }
      : undefined,
    launch.maxTotalAgents !== undefined
      ? {
          name: "DEFAULT_MAX_TOTAL_AGENTS",
          value: String(launch.maxTotalAgents),
        }
      : undefined,
    launch.maxDepth !== undefined
      ? { name: "DEFAULT_MAX_DEPTH", value: String(launch.maxDepth) }
      : undefined,
    launch.checkpointIntervalSeconds !== undefined
      ? {
          name: "CHECKPOINT_INTERVAL_SECONDS",
          value: String(launch.checkpointIntervalSeconds),
        }
      : undefined,
    launch.computeMode
      ? { name: "COMPUTE_MODE", value: launch.computeMode }
      : undefined,
    launch.budgetCents !== undefined
      ? {
          name: "DEFAULT_MAX_COST_CENTS",
          value: String(launch.budgetCents),
        }
      : undefined,
    launch.deploymentId
      ? { name: "DEPLOYMENT_ID", value: launch.deploymentId }
      : undefined,
    launch.environmentId
      ? { name: "ENVIRONMENT_ID", value: launch.environmentId }
      : undefined,
    launch.policyRef
      ? { name: "POLICY_REF", value: launch.policyRef }
      : undefined,
    launch.toolRefs
      ? { name: "TOOL_REFS", value: launch.toolRefs.join(",") }
      : undefined,
    launch.inputRefs
      ? { name: "INPUT_REFS", value: launch.inputRefs.join(",") }
      : undefined,
  ].filter(
    (item): item is { name: string; value: string } => item !== undefined,
  );
  const response = await ecs.send(
    new RunTaskCommand({
      cluster: required("ECS_CLUSTER_ARN"),
      taskDefinition: required("COORDINATOR_TASK_DEFINITION_ARN"),
      launchType: "FARGATE",
      count: 1,
      networkConfiguration: {
        awsvpcConfiguration: {
          assignPublicIp: "ENABLED",
          subnets: required("AGENT_SUBNET_IDS").split(","),
          securityGroups: [required("AGENT_SECURITY_GROUP_ID")],
        },
      },
      overrides: {
        containerOverrides: [
          {
            name: "coordinator",
            environment,
          },
        ],
      },
      tags: [
        { key: "Application", value: "Convoy" },
        { key: "MissionId", value: missionId },
        { key: "StartedBy", value: "ConvoyApp" },
      ],
      enableECSManagedTags: true,
      propagateTags: "TASK_DEFINITION",
    }),
  );
  if (response.failures?.length || !response.tasks?.[0]?.taskArn) {
    throw new Error(
      `ECS coordinator launch failed: ${JSON.stringify(response.failures ?? [])}`,
    );
  }
  return response.tasks[0].taskArn!;
}

export async function readCloudMission(
  missionId: string,
): Promise<CloudMission> {
  const response = await dynamodb.send(
    new QueryCommand({
      TableName: required("MISSION_TABLE_NAME"),
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: {
        ":pk": `MISSION#${missionId}`,
      },
      ConsistentRead: true,
    }),
  );
  const records = response.Items ?? [];
  const mission = records.find((item) => item.sk === "MISSION") ?? null;
  const agents = records
    .filter((item) => item.entityType === "AGENT")
    .map((item) => ({
      agentId: String(item.agentId),
      depth: Number(item.depth),
      status: item.status === "COMPLETED" ? "COMPLETED" as const : "RUNNING" as const,
      computeProvider: item.taskArn ? "AWS Fargate" : item.computeProvider,
      model: item.model ? String(item.model) : undefined,
      inputTokens:
        item.inputTokens === undefined ? undefined : Number(item.inputTokens),
      outputTokens:
        item.outputTokens === undefined ? undefined : Number(item.outputTokens),
      thesis: item.thesis ? String(item.thesis) : undefined,
      artifactKey: item.artifactKey,
      artifact: item.artifact,
      completedAt: item.completedAt,
    }))
    .sort(
      (left, right) =>
        left.depth - right.depth ||
        left.agentId.localeCompare(right.agentId),
    );
  return {
    missionId,
    status: mission?.status === "COMPLETED"
      ? "COMPLETED"
      : agents.length
        ? "RUNNING"
        : "QUEUED",
    mission: mission
      ? {
          totalAgents: Number(mission.totalAgents),
          completedAt: mission.completedAt,
        }
      : null,
    agents,
  };
}
