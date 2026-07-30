import { NextResponse } from "next/server";
import { readCloudMission } from "@/server/cloud-runtime";
import { apiError } from "@/server/control-plane/http";
import { requireWorkspaceAccess } from "@/server/control-plane/permissions";
import {
  getMission,
  updateMission,
} from "@/server/control-plane/store";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    let mission = await getMission(id);
    if (!mission) {
      return NextResponse.json({ error: "Mission not found." }, { status: 404 });
    }
    await requireWorkspaceAccess(mission.workspaceId);

    let cloudMission;
    if (
      mission.provider === "temporal-fargate" &&
      !["succeeded", "failed", "cancelled"].includes(mission.state)
    ) {
      cloudMission = await readCloudMission(mission.id);
      if (cloudMission.status === "COMPLETED") {
        mission = await updateMission(mission.id, {
          state: "succeeded",
          totalAgents:
            cloudMission.mission?.totalAgents ?? cloudMission.agents.length,
          summary: `${
            cloudMission.mission?.totalAgents ?? cloudMission.agents.length
          } governed agent episodes completed on AWS.`,
          completedAt:
            cloudMission.mission?.completedAt ?? new Date().toISOString(),
        });
      } else if (
        cloudMission.status === "RUNNING" &&
        mission.state !== "running"
      ) {
        mission = await updateMission(mission.id, { state: "running" });
      }
    }

    return NextResponse.json({ mission, cloudMission });
  } catch (error) {
    return apiError(error);
  }
}
