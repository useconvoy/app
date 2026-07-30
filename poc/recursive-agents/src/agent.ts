import os from "node:os";
import Anthropic from "@anthropic-ai/sdk";
import { PutObjectCommand } from "@aws-sdk/client-s3";
import { PutCommand } from "@aws-sdk/lib-dynamodb";
import { Client, Connection } from "@temporalio/client";
import { documentClient, loadTemporalConfig, s3Client } from "./aws";
import { episodeCompleted } from "./workflows";
import type {
  EpisodeResult,
  NextIntent,
  SpawnCandidate,
} from "./contracts";

const MODEL = process.env.CONVOY_MODEL ?? "claude-sonnet-5";
const MAX_OUTPUT_TOKENS = Number(
  process.env.CONVOY_MAX_OUTPUT_TOKENS ?? "1600",
);

interface ModelEpisode {
  thesis: string;
  evidence: string[];
  nextIntent: NextIntent;
  inputTokens: number;
  outputTokens: number;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable ${name}`);
  return value;
}

function cleanText(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim()
    ? value.trim()
    : fallback;
}

function cleanCandidates(value: unknown, maxFanout: number): SpawnCandidate[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((candidate) =>
      candidate &&
      typeof candidate === "object" &&
      "objective" in candidate
        ? cleanText(candidate.objective, "")
        : "",
    )
    .filter(Boolean)
    .slice(0, maxFanout)
    .map((objective) => ({ objective }));
}

async function reasonAboutEpisode(input: {
  objective: string;
  agentId: string;
  depth: number;
  maxDepth: number;
  maxFanout: number;
}): Promise<ModelEpisode> {
  const client = new Anthropic();
  const canSpawn = input.depth < input.maxDepth;
  const response = await client.messages.create({
    model: MODEL,
    max_tokens: MAX_OUTPUT_TOKENS,
    system: [
      "You are a bounded specialist inside a governed recursive mission.",
      "Analyze only the assigned objective. Be concrete, skeptical, and concise.",
      canSpawn
        ? `You may propose at most ${input.maxFanout} independent child investigations when decomposition materially improves the answer.`
        : "You are at the depth limit and must complete the assigned investigation without spawning children.",
      "Do not claim to have browsed, queried systems, or gathered external evidence. Your evidence is model reasoning unless the prompt itself provides sources.",
      "Submit exactly one structured episode result through the provided tool.",
    ].join(" "),
    messages: [
      {
        role: "user",
        content: [
          {
            type: "text",
            text: JSON.stringify({
              agentId: input.agentId,
              depth: input.depth,
              maxDepth: input.maxDepth,
              objective: input.objective,
            }),
          },
        ],
      },
    ],
    tools: [
      {
        name: "submit_episode",
        description:
          "Submit the specialist's analysis and its bounded next action.",
        input_schema: {
          type: "object",
          properties: {
            thesis: {
              type: "string",
              description: "The strongest concise conclusion.",
            },
            evidence: {
              type: "array",
              items: { type: "string" },
              minItems: 1,
              maxItems: 6,
              description:
                "Specific reasoning, assumptions, risks, or findings supporting the thesis.",
            },
            decision: {
              type: "string",
              enum: ["spawn_agents", "complete"],
            },
            candidates: {
              type: "array",
              maxItems: input.maxFanout,
              items: {
                type: "object",
                properties: {
                  objective: { type: "string" },
                },
                required: ["objective"],
                additionalProperties: false,
              },
            },
            summary: {
              type: "string",
              description:
                "Completion summary. Use an empty string when spawning.",
            },
          },
          required: [
            "thesis",
            "evidence",
            "decision",
            "candidates",
            "summary",
          ],
          additionalProperties: false,
        },
      },
    ],
    tool_choice: {
      type: "tool",
      name: "submit_episode",
      disable_parallel_tool_use: true,
    },
  });

  const submission = response.content.find(
    (block): block is Anthropic.ToolUseBlock =>
      block.type === "tool_use" && block.name === "submit_episode",
  );
  if (!submission) {
    throw new Error(`Model ${MODEL} did not submit an episode result`);
  }
  const payload = submission.input as Record<string, unknown>;
  const thesis = cleanText(
    payload.thesis,
    `Analysis completed for ${input.objective}`,
  );
  const evidence = Array.isArray(payload.evidence)
    ? payload.evidence
        .map((item) => cleanText(item, ""))
        .filter(Boolean)
        .slice(0, 6)
    : [];
  const candidates = cleanCandidates(payload.candidates, input.maxFanout);
  const shouldSpawn =
    canSpawn &&
    payload.decision === "spawn_agents" &&
    candidates.length > 0;
  return {
    thesis,
    evidence:
      evidence.length > 0
        ? evidence
        : ["The model returned a thesis without separate supporting findings."],
    nextIntent: shouldSpawn
      ? { type: "spawn_agents", candidates }
      : {
          type: "complete",
          summary: cleanText(payload.summary, thesis),
        },
    inputTokens: response.usage.input_tokens,
    outputTokens: response.usage.output_tokens,
  };
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

  const modelEpisode = await reasonAboutEpisode({
    objective,
    agentId,
    depth,
    maxDepth,
    maxFanout,
  });
  const nextIntent = modelEpisode.nextIntent;
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
    model: MODEL,
    tokenUsage: {
      input: modelEpisode.inputTokens,
      output: modelEpisode.outputTokens,
    },
    thesis: modelEpisode.thesis,
    evidence: modelEpisode.evidence,
    runtime: `${os.arch()} / Node ${process.version}`,
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
          model: MODEL,
          inputTokens: modelEpisode.inputTokens,
          outputTokens: modelEpisode.outputTokens,
          thesis: modelEpisode.thesis,
          evidence: modelEpisode.evidence,
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
      model: MODEL,
      inputTokens: modelEpisode.inputTokens,
      outputTokens: modelEpisode.outputTokens,
      artifactKey,
    }),
  );
}
