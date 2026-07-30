import { NextResponse } from "next/server";
import { requireAccount } from "@/server/control-plane/auth";
import { apiError, jsonBody, textField } from "@/server/control-plane/http";
import {
  controlPlaneId,
  workspaceSlug,
} from "@/server/control-plane/ids";
import {
  createWorkspace,
  listWorkspacesForAccount,
} from "@/server/control-plane/store";
import type {
  Workspace,
  WorkspaceMembership,
} from "@/server/control-plane/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const account = await requireAccount();
    return NextResponse.json({
      workspaces: await listWorkspacesForAccount(account.id),
    });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const account = await requireAccount();
    const body = await jsonBody(request);
    const name = textField(body, "name", { min: 2, max: 100 });
    const createdAt = new Date().toISOString();
    const workspace: Workspace = {
      id: controlPlaneId("ws"),
      name,
      slug: workspaceSlug(name),
      createdAt,
      createdByAccountId: account.id,
    };
    const membership: WorkspaceMembership = {
      accountId: account.id,
      workspaceId: workspace.id,
      role: "owner",
      createdAt,
    };
    await createWorkspace(workspace, membership);
    return NextResponse.json(
      { workspace: { ...workspace, role: membership.role } },
      { status: 201 },
    );
  } catch (error) {
    return apiError(error);
  }
}
