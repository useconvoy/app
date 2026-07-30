import { NextResponse } from "next/server";
import { createAccountSession } from "@/server/control-plane/auth";
import { apiError, jsonBody, textField } from "@/server/control-plane/http";
import { controlPlaneId } from "@/server/control-plane/ids";
import { hashPassword } from "@/server/control-plane/passwords";
import { createAccount } from "@/server/control-plane/store";
import { publicAccount, type Account } from "@/server/control-plane/types";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const body = await jsonBody(request);
    const name = textField(body, "name", { min: 2, max: 100 });
    const email = textField(body, "email", { min: 3, max: 320 }).toLowerCase();
    const password = textField(body, "password", { min: 10, max: 256 });
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      return NextResponse.json(
        { error: "Enter a valid email address." },
        { status: 400 },
      );
    }
    const account: Account = {
      id: controlPlaneId("acct"),
      email,
      name,
      passwordHash: hashPassword(password),
      createdAt: new Date().toISOString(),
    };
    await createAccount(account);
    await createAccountSession(account.id);
    return NextResponse.json(
      { account: publicAccount(account), needsWorkspace: true },
      { status: 201 },
    );
  } catch (error) {
    return apiError(error);
  }
}
