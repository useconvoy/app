import { AuthenticationError, requireAccount } from "./auth";
import { getMembership } from "./store";
import type { Account, WorkspaceMembership } from "./types";

export async function requireWorkspaceAccess(
  workspaceId: string,
): Promise<{ account: Account; membership: WorkspaceMembership }> {
  const account = await requireAccount();
  const membership = await getMembership(account.id, workspaceId);
  if (!membership) {
    throw new AuthenticationError(
      "You do not have access to this workspace.",
    );
  }
  return { account, membership };
}
