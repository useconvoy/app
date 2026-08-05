/**
 * WorkOS AuthKit integration: hosted sign-in inside our branded frame.
 * Configuration is entirely environment-driven; when WorkOS is not
 * configured (local development, CI), the explicit dev sign-in provider in
 * app/(auth) takes over behind the same session contract.
 */
import "server-only";

import { WorkOS } from "@workos-inc/node";

let client: WorkOS | undefined;

export function workosEnabled(): boolean {
  return Boolean(process.env.WORKOS_API_KEY && process.env.WORKOS_CLIENT_ID);
}

/**
 * Whether the explicit email-and-name sign-in may be offered. It exists so a
 * laptop and CI can hold a session without an identity provider, and it
 * verifies nothing: anyone reaching it can claim any address. A deployment
 * therefore refuses it outright, leaving hosted sign-in as the only door.
 * Failing closed is deliberate: a deployment missing its identity
 * configuration must reject sign-in, not fall back to an open form.
 */
export function localSignInAllowed(): boolean {
  return !workosEnabled() && process.env.NODE_ENV !== "production";
}

function workos(): WorkOS {
  if (!client) {
    const apiKey = process.env.WORKOS_API_KEY;
    if (!apiKey) throw new Error("WORKOS_API_KEY is not configured");
    client = new WorkOS(apiKey);
  }
  return client;
}

export function authorizationUrl(redirectUri: string): string {
  return workos().userManagement.getAuthorizationUrl({
    provider: "authkit",
    clientId: process.env.WORKOS_CLIENT_ID ?? "",
    redirectUri,
  });
}

export interface WorkosIdentity {
  workosUserId: string;
  email: string;
  name: string;
}

export async function exchangeCode(code: string): Promise<WorkosIdentity> {
  const { user } = await workos().userManagement.authenticateWithCode({
    clientId: process.env.WORKOS_CLIENT_ID ?? "",
    code,
  });
  const name = [user.firstName, user.lastName].filter(Boolean).join(" ") || user.email;
  return { workosUserId: user.id, email: user.email, name };
}
