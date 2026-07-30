import {
  ECSClient,
  RunTaskCommand,
  type KeyValuePair,
} from "@aws-sdk/client-ecs";
import {
  GetCommand,
  PutCommand,
  TransactWriteCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import { PutObjectCommand } from "@aws-sdk/client-s3";
import type {
  AdmitSpawnInput,
  AdmitSpawnResult,
  LaunchEpisodeInput,
  MissionSpec,
  MissionResult,
} from "./contracts";
import { documentClient, s3Client } from "./aws";

const ecsClient = new ECSClient({});

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable ${name}`);
  return value;
}

function isConditionalFailure(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "ConditionalCheckFailedException" ||
      error.name === "TransactionCanceledException")
  );
}

export async function initializeMission(spec: MissionSpec): Promise<void> {
  try {
    await documentClient.send(
      new PutCommand({
        TableName: required("MISSION_TABLE_NAME"),
        Item: {
          pk: `MISSION#${spec.missionId}`,
          sk: "MISSION",
          entityType: "MISSION",
          missionId: spec.missionId,
          status: "RUNNING",
          totalAgents: 1,
          activeAgents: 0,
          spec,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
        ConditionExpression: "attribute_not_exists(pk)",
      }),
    );
  } catch (error) {
    if (!isConditionalFailure(error)) throw error;
  }
}

export async function acquireAgentSlot(input: {
  missionId: string;
  agentId: string;
  maxParallelAgents: number;
}): Promise<void> {
  const tableName = required("MISSION_TABLE_NAME");
  const slotKey = {
    pk: `MISSION#${input.missionId}`,
    sk: `SLOT#${input.agentId}`,
  };
  const existing = await documentClient.send(
    new GetCommand({ TableName: tableName, Key: slotKey }),
  );
  if (existing.Item) return;

  try {
    await documentClient.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Put: {
              TableName: tableName,
              Item: {
                ...slotKey,
                entityType: "AGENT_SLOT",
                agentId: input.agentId,
                acquiredAt: new Date().toISOString(),
              },
              ConditionExpression: "attribute_not_exists(pk)",
            },
          },
          {
            Update: {
              TableName: tableName,
              Key: {
                pk: `MISSION#${input.missionId}`,
                sk: "MISSION",
              },
              UpdateExpression:
                "SET activeAgents = if_not_exists(activeAgents, :zero) + :one, updatedAt = :now",
              ConditionExpression:
                "attribute_exists(pk) AND (attribute_not_exists(activeAgents) OR activeAgents < :limit)",
              ExpressionAttributeValues: {
                ":zero": 0,
                ":one": 1,
                ":limit": input.maxParallelAgents,
                ":now": new Date().toISOString(),
              },
            },
          },
        ],
      }),
    );
  } catch (error) {
    if (isConditionalFailure(error)) {
      const retryCheck = await documentClient.send(
        new GetCommand({ TableName: tableName, Key: slotKey }),
      );
      if (retryCheck.Item) return;
      throw new Error(
        `No agent slot available for ${input.agentId}; Temporal will retry`,
      );
    }
    throw error;
  }
}

export async function releaseAgentSlot(input: {
  missionId: string;
  agentId: string;
}): Promise<void> {
  const tableName = required("MISSION_TABLE_NAME");
  const slotKey = {
    pk: `MISSION#${input.missionId}`,
    sk: `SLOT#${input.agentId}`,
  };
  const existing = await documentClient.send(
    new GetCommand({ TableName: tableName, Key: slotKey }),
  );
  if (!existing.Item) return;

  try {
    await documentClient.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Delete: {
              TableName: tableName,
              Key: slotKey,
              ConditionExpression: "attribute_exists(pk)",
            },
          },
          {
            Update: {
              TableName: tableName,
              Key: {
                pk: `MISSION#${input.missionId}`,
                sk: "MISSION",
              },
              UpdateExpression:
                "SET activeAgents = activeAgents - :one, updatedAt = :now",
              ConditionExpression: "activeAgents > :zero",
              ExpressionAttributeValues: {
                ":zero": 0,
                ":one": 1,
                ":now": new Date().toISOString(),
              },
            },
          },
        ],
      }),
    );
  } catch (error) {
    if (!isConditionalFailure(error)) throw error;
  }
}

export async function admitAgentSpawns(
  input: AdmitSpawnInput,
): Promise<AdmitSpawnResult> {
  const tableName = required("MISSION_TABLE_NAME");
  const decisionKey = {
    pk: `MISSION#${input.spec.missionId}`,
    sk: `ADMISSION#${input.parentAgentId}`,
  };
  const previous = await documentClient.send(
    new GetCommand({ TableName: tableName, Key: decisionKey }),
  );
  if (previous.Item?.decision) {
    return previous.Item.decision as AdmitSpawnResult;
  }

  let reason: string | undefined;
  if (Date.now() >= Date.parse(input.spec.deadline)) {
    reason = "deadline_reached";
  } else if (input.parentDepth >= input.spec.maxDepth) {
    reason = "max_depth_reached";
  }

  for (;;) {
    const mission = await documentClient.send(
      new GetCommand({
        TableName: tableName,
        Key: {
          pk: `MISSION#${input.spec.missionId}`,
          sk: "MISSION",
        },
        ConsistentRead: true,
      }),
    );
    const currentTotal = Number(mission.Item?.totalAgents ?? 1);
    const costLimit = Math.floor(
      input.spec.budget.maxCostCents /
        Math.max(1, input.spec.budget.estimatedCostPerAgentCents),
    );
    const hardLimit = Math.min(input.spec.maxTotalAgents, costLimit);
    const remaining = reason ? 0 : Math.max(0, hardLimit - currentTotal);
    if (!reason && remaining === 0) reason = "agent_or_budget_limit_reached";

    const admitted = input.candidates.slice(0, remaining);
    const decision: AdmitSpawnResult = {
      admitted,
      rejectedCount: input.candidates.length - admitted.length,
      ...(reason ? { reason } : {}),
    };

    try {
      await documentClient.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Put: {
                TableName: tableName,
                Item: {
                  ...decisionKey,
                  entityType: "SPAWN_ADMISSION",
                  parentAgentId: input.parentAgentId,
                  decision,
                  createdAt: new Date().toISOString(),
                },
                ConditionExpression: "attribute_not_exists(pk)",
              },
            },
            {
              Update: {
                TableName: tableName,
                Key: {
                  pk: `MISSION#${input.spec.missionId}`,
                  sk: "MISSION",
                },
                UpdateExpression:
                  "SET totalAgents = :nextTotal, updatedAt = :now",
                ConditionExpression: "totalAgents = :currentTotal",
                ExpressionAttributeValues: {
                  ":currentTotal": currentTotal,
                  ":nextTotal": currentTotal + admitted.length,
                  ":now": new Date().toISOString(),
                },
              },
            },
          ],
        }),
      );
      return decision;
    } catch (error) {
      if (!isConditionalFailure(error)) throw error;
      const raced = await documentClient.send(
        new GetCommand({ TableName: tableName, Key: decisionKey }),
      );
      if (raced.Item?.decision) {
        return raced.Item.decision as AdmitSpawnResult;
      }
    }
  }
}

export async function launchAgentEpisode(
  input: LaunchEpisodeInput,
): Promise<{ taskArn: string }> {
  const tableName = required("MISSION_TABLE_NAME");
  const environment: KeyValuePair[] = Object.entries({
    MISSION_ID: input.spec.missionId,
    AGENT_ID: input.agentId,
    AGENT_WORKFLOW_ID: input.workflowId,
    AGENT_DEPTH: String(input.depth),
    AGENT_OBJECTIVE: input.objective,
    EPISODE_INDEX: String(input.episodeIndex),
    MAX_DEPTH: String(input.spec.maxDepth),
    MAX_FANOUT: process.env.DEFAULT_MAX_FANOUT ?? "3",
    DEADLINE: input.spec.deadline,
    CHECKPOINT_INTERVAL_SECONDS: String(
      input.spec.checkpointIntervalSeconds,
    ),
    COMPUTE_MODE: input.spec.computeMode,
  }).map(([name, value]) => ({ name, value }));

  await documentClient.send(
    new PutCommand({
      TableName: tableName,
      Item: {
        pk: `MISSION#${input.spec.missionId}`,
        sk: `AGENT#${input.agentId}`,
        entityType: "AGENT",
        missionId: input.spec.missionId,
        agentId: input.agentId,
        depth: input.depth,
        objective: input.objective,
        status: "LAUNCHING",
        workflowId: input.workflowId,
        updatedAt: new Date().toISOString(),
      },
    }),
  );

  const response = await ecsClient.send(
    new RunTaskCommand({
      cluster: required("ECS_CLUSTER_ARN"),
      taskDefinition: required("AGENT_TASK_DEFINITION_ARN"),
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
            name: "agent",
            environment,
          },
        ],
      },
      tags: [
        { key: "Application", value: "Convoy" },
        { key: "MissionId", value: input.spec.missionId },
        { key: "AgentId", value: input.agentId },
      ],
      enableECSManagedTags: true,
      propagateTags: "TASK_DEFINITION",
    }),
  );

  if (response.failures?.length || !response.tasks?.[0]?.taskArn) {
    throw new Error(
      `ECS RunTask failed: ${JSON.stringify(response.failures ?? [])}`,
    );
  }

  const taskArn = response.tasks[0].taskArn;
  await documentClient.send(
    new PutCommand({
      TableName: tableName,
      Item: {
        pk: `MISSION#${input.spec.missionId}`,
        sk: `AGENT#${input.agentId}`,
        entityType: "AGENT",
        missionId: input.spec.missionId,
        agentId: input.agentId,
        depth: input.depth,
        objective: input.objective,
        status: "RUNNING",
        workflowId: input.workflowId,
        taskArn,
        updatedAt: new Date().toISOString(),
      },
    }),
  );

  return { taskArn };
}

export async function recordMissionSummary(
  result: MissionResult,
): Promise<void> {
  const tableName = required("MISSION_TABLE_NAME");
  const bucketName = required("ARTIFACT_BUCKET_NAME");
  const artifactKey = `missions/${result.missionId}/summary.json`;
  const body = JSON.stringify(result, null, 2);

  await Promise.all([
    documentClient.send(
      new UpdateCommand({
        TableName: tableName,
        Key: {
          pk: `MISSION#${result.missionId}`,
          sk: "MISSION",
        },
        UpdateExpression:
          "SET #status = :status, totalAgents = :totalAgents, activeAgents = :zero, artifactKey = :artifactKey, completedAt = :completedAt, updatedAt = :completedAt",
        ExpressionAttributeNames: { "#status": "status" },
        ExpressionAttributeValues: {
          ":status": "COMPLETED",
          ":totalAgents": result.totalAgents,
          ":zero": 0,
          ":artifactKey": artifactKey,
          ":completedAt": result.completedAt,
        },
      }),
    ),
    s3Client.send(
      new PutObjectCommand({
        Bucket: bucketName,
        Key: artifactKey,
        Body: body,
        ContentType: "application/json",
      }),
    ),
  ]);
}
