import { NextResponse } from "next/server";
import {
  isCloudRuntimeEnabled,
  startCloudMission,
} from "@/server/cloud-runtime";
import {
  apiError,
  boundedInteger,
  jsonBody,
  textField,
} from "@/server/control-plane/http";
import { controlPlaneId } from "@/server/control-plane/ids";
import { requireWorkspaceAccess } from "@/server/control-plane/permissions";
import {
  createMission,
  listMissions,
  updateMission,
} from "@/server/control-plane/store";
import type {
  Mission,
  MissionEnvelope,
} from "@/server/control-plane/types";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const workspaceId = new URL(request.url).searchParams.get("workspaceId");
    if (!workspaceId) {
      return NextResponse.json(
        { error: "workspaceId is required." },
        { status: 400 },
      );
    }
    await requireWorkspaceAccess(workspaceId);
    return NextResponse.json({
      missions: await listMissions(workspaceId),
    });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const body = await jsonBody(request);
    const workspaceId = textField(body, "workspaceId", {
      min: 4,
      max: 100,
    });
    const { account } = await requireWorkspaceAccess(workspaceId);
    const objective = textField(body, "objective", {
      min: 12,
      max: 8_000,
    });
    const deadlineMinutes = boundedInteger(
      body,
      "deadlineMinutes",
      15,
      5,
      1_440,
    );
    const envelope: MissionEnvelope = {
      deadline: new Date(
        Date.now() + deadlineMinutes * 60 * 1000,
      ).toISOString(),
      maxParallelAgents: boundedInteger(
        body,
        "maxParallelAgents",
        6,
        1,
        24,
      ),
      maxTotalAgents: boundedInteger(
        body,
        "maxTotalAgents",
        13,
        1,
        500,
      ),
      maxDepth: boundedInteger(body, "maxDepth", 2, 0, 8),
      checkpointIntervalSeconds: boundedInteger(
        body,
        "checkpointIntervalSeconds",
        60,
        15,
        3_600,
      ),
      computeMode:
        body.computeMode === "fast" ||
        body.computeMode === "economy" ||
        body.computeMode === "dedicated"
          ? body.computeMode
          : "auto",
      budgetCents: boundedInteger(body, "budgetCents", 100, 1, 100_000),
    };
    const now = new Date().toISOString();
    const mission: Mission = {
      id: controlPlaneId("mis"),
      workspaceId,
      createdByAccountId: account.id,
      objective,
      state: "accepted",
      provider: isCloudRuntimeEnabled() ? "temporal-fargate" : "local",
      envelope,
      createdAt: now,
      updatedAt: now,
    };
    await createMission(mission);

    if (!isCloudRuntimeEnabled()) {
      return NextResponse.json(
        {
          mission,
          warning:
            "Mission was saved locally. Enable CONVOY_CLOUD_RUNTIME to launch it on AWS.",
        },
        { status: 201 },
      );
    }

    try {
      const preparing = await updateMission(mission.id, {
        state: "preparing",
      });
      const providerTaskArn = await startCloudMission(mission.id, {
        objective,
        workspaceId,
        deadline: envelope.deadline,
        maxParallelAgents: envelope.maxParallelAgents,
        maxTotalAgents: envelope.maxTotalAgents,
        maxDepth: envelope.maxDepth,
        checkpointIntervalSeconds: envelope.checkpointIntervalSeconds,
        computeMode: envelope.computeMode,
        budgetCents: envelope.budgetCents,
        deploymentId: `workspace/${workspaceId}`,
        environmentId: "aws-poc",
        policyRef: `policy://workspace/${workspaceId}/default`,
        toolRefs: ["tool://s3", "tool://dynamodb"],
      });
      const launched = await updateMission(preparing.id, {
        providerTaskArn,
        state: "preparing",
      });
      return NextResponse.json({ mission: launched }, { status: 201 });
    } catch (error) {
      await updateMission(mission.id, {
        state: "failed",
        summary:
          error instanceof Error
            ? `AWS launch failed: ${error.message}`
            : "AWS launch failed.",
        completedAt: new Date().toISOString(),
      });
      throw error;
    }
  } catch (error) {
    return apiError(error);
  }
}
