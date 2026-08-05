/**
 * Shared sign-in completion for both providers (WorkOS callback, local dev
 * form): sync identity, mint the session, and pick a destination. A single
 * org becomes the active org immediately; multiple orgs go through the
 * switcher; none goes to onboarding.
 */
import "server-only";

import { createSession } from "@/lib/auth/session";
import { listUserOrgs, syncUser } from "@/lib/orgs/queries";

export interface SignInIdentity {
  email: string;
  name: string;
  workosUserId?: string;
}

/** Returns the post-sign-in destination path. */
export async function completeSignIn(identity: SignInIdentity): Promise<string> {
  const user = await syncUser(identity);
  const orgs = await listUserOrgs(user.id);
  const only = orgs.length === 1 ? orgs[0] : undefined;
  await createSession({
    userId: user.id,
    email: user.email,
    name: user.name,
    workosUserId: identity.workosUserId,
    orgId: only?.id,
  });
  if (orgs.length === 0) return "/onboarding";
  return only ? "/app" : "/switch";
}
