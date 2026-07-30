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

export async function startCloudMission(
  missionId: string,
  objective: string,
): Promise<void> {
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
            environment: [
              { name: "MISSION_ID", value: missionId },
              { name: "MISSION_OBJECTIVE", value: objective },
            ],
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
