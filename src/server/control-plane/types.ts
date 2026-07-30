export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export interface Account {
  id: string;
  email: string;
  name: string;
  passwordHash: string;
  createdAt: string;
}

export interface PublicAccount {
  id: string;
  email: string;
  name: string;
  createdAt: string;
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  createdAt: string;
  createdByAccountId: string;
}

export interface WorkspaceMembership {
  accountId: string;
  workspaceId: string;
  role: WorkspaceRole;
  createdAt: string;
}

export type MissionState =
  | "accepted"
  | "preparing"
  | "running"
  | "paused_pending_approval"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface MissionEnvelope {
  deadline: string;
  maxParallelAgents: number;
  maxTotalAgents: number;
  maxDepth: number;
  checkpointIntervalSeconds: number;
  computeMode: "auto" | "fast" | "economy" | "dedicated";
  budgetCents: number;
}

export interface Mission {
  id: string;
  workspaceId: string;
  createdByAccountId: string;
  objective: string;
  state: MissionState;
  provider: "local" | "temporal-fargate";
  envelope: MissionEnvelope;
  providerTaskArn?: string;
  summary?: string;
  totalAgents?: number;
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
}

export interface Session {
  tokenHash: string;
  accountId: string;
  createdAt: string;
  expiresAt: string;
}

export interface AccountContext {
  account: PublicAccount;
  workspaces: Array<Workspace & { role: WorkspaceRole }>;
}

export function publicAccount(account: Account): PublicAccount {
  const { passwordHash: _passwordHash, ...view } = account;
  return view;
}
