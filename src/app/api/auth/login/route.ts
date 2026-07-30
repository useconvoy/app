import { NextResponse } from "next/server";
import { createAccountSession } from "@/server/control-plane/auth";
import { apiError, jsonBody, textField } from "@/server/control-plane/http";
import { verifyPassword } from "@/server/control-plane/passwords";
import {
  getAccountByEmail,
  listWorkspacesForAccount,
} from "@/server/control-plane/store";
import { publicAccount } from "@/server/control-plane/types";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const body = await jsonBody(request);
    const email = textField(body, "email", { min: 3, max: 320 }).toLowerCase();
    const password = textField(body, "password", { min: 1, max: 256 });
    const account = await getAccountByEmail(email);
    if (!account || !verifyPassword(password, account.passwordHash)) {
      return NextResponse.json(
        { error: "Email or password is incorrect." },
        { status: 401 },
      );
    }
    await createAccountSession(account.id);
    const workspaces = await listWorkspacesForAccount(account.id);
    return NextResponse.json({
      account: publicAccount(account),
      workspaces,
      needsWorkspace: workspaces.length === 0,
    });
  } catch (error) {
    return apiError(error);
  }
}
