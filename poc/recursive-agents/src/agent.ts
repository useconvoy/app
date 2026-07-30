import os from "node:os";
import { PutObjectCommand } from "@aws-sdk/client-s3";
import { PutCommand } from "@aws-sdk/lib-dynamodb";
import { Client, Connection } from "@temporalio/client";
import { documentClient, loadTemporalConfig, s3Client } from "./aws";
import { episodeCompleted } from "./workflows";
import type { EpisodeResult } from "./contracts";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable ${name}`);
  return value;
}

export async function runAgentEpisode(): Promise<void> {
  const missionId = required("MISSION_ID");
  const agentId = required("AGENT_ID");
  const workflowId = required("AGENT_WORKFLOW_ID");
  const depth = Number(required("AGENT_DEPTH"));
  const maxDepth = Number(required("MAX_DEPTH"));
  const maxFanout = Number(required("MAX_FANOUT"));
  const episodeIndex = Number(process.env.EPISODE_INDEX ?? "0");
  const objective = required("AGENT_OBJECTIVE");
  const tableName = required("MISSION_TABLE_NAME");
  const bucketName = required("ARTIFACT_BUCKET_NAME");

  const nextIntent =
    depth < maxDepth
      ? ({
          type: "spawn_agents" as const,
          candidates: Array.from({ length: maxFanout }, (_, index) => ({
            objective: `${objective} — investigate branch ${index + 1}`,
          })),
        })
      : ({
          type: "complete" as const,
          summary: `Returned bounded evidence for: ${objective}`,
        });
  const artifactKey = `missions/${missionId}/agents/${agentId}.json`;
  const completedAt = new Date().toISOString();
  const result: EpisodeResult = {
    missionId,
    agentId,
    depth,
    episodeIndex,
    nextIntent,
    artifactKey,
    completedAt,
  };

  const artifact = {
    ...result,
    thesis:
      depth === 0
        ? "Decompose the mission into independent specialist investigations."
        : depth === maxDepth
          ? "Return bounded evidence to the supervising agent."
          : "Expand the strongest investigative branches in parallel.",
    evidence: [
      `agent ${agentId} completed an isolated Fargate episode`,
      `runtime ${os.arch()} / Node ${process.version}`,
      `returned typed intent ${nextIntent.type}`,
    ],
  };

  await Promise.all([
    s3Client.send(
      new PutObjectCommand({
        Bucket: bucketName,
        Key: artifactKey,
        Body: JSON.stringify(artifact, null, 2),
        ContentType: "application/json",
      }),
    ),
    documentClient.send(
      new PutCommand({
        TableName: tableName,
        Item: {
          pk: `MISSION#${missionId}`,
          sk: `AGENT#${agentId}`,
          entityType: "AGENT",
          ...result,
          objective,
          computeProvider: "AWS Fargate",
          workflowId,
          status: "COMPLETED",
          updatedAt: completedAt,
        },
      }),
    ),
  ]);

  const temporal = await loadTemporalConfig();
  const connection = await Connection.connect({
    address: temporal.endpoint,
    tls: true,
    apiKey: temporal.apiKey,
  });
  const client = new Client({
    connection,
    namespace: temporal.namespace,
  });
  await client.workflow.getHandle(workflowId).signal(episodeCompleted, result);
  await connection.close();

  console.log(
    JSON.stringify({
      event: "agent.episode.completed",
      missionId,
      agentId,
      depth,
      nextIntent: nextIntent.type,
      artifactKey,
    }),
  );
}
