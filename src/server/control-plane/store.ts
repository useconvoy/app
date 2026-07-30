import fs from "node:fs";
import path from "node:path";
import {
  DynamoDBClient,
} from "@aws-sdk/client-dynamodb";
import {
  DeleteCommand,
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  TransactWriteCommand,
} from "@aws-sdk/lib-dynamodb";
import type {
  Account,
  Mission,
  Session,
  Workspace,
  WorkspaceMembership,
  WorkspaceRole,
} from "./types";
import * as postgres from "./postgres-store";

interface LocalControlPlane {
  accounts: Account[];
  workspaces: Workspace[];
  memberships: WorkspaceMembership[];
  missions: Mission[];
  sessions: Session[];
}

export class ControlPlaneConflictError extends Error {}

const tableName =
  process.env.CONTROL_PLANE_TABLE_NAME ??
  process.env.MISSION_TABLE_NAME;

const dynamodb = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});

const dataDir =
  process.env.CONVOY_DATA_DIR ?? path.join(process.cwd(), ".data");
const localFile = path.join(dataDir, "control-plane.json");
const globalStore = globalThis as unknown as {
  __convoyControlPlane?: LocalControlPlane;
};

function emptyLocalStore(): LocalControlPlane {
  return {
    accounts: [],
    workspaces: [],
    memberships: [],
    missions: [],
    sessions: [],
  };
}

function localStore(): LocalControlPlane {
  if (globalStore.__convoyControlPlane) return globalStore.__convoyControlPlane;
  if (fs.existsSync(localFile)) {
    globalStore.__convoyControlPlane = JSON.parse(
      fs.readFileSync(localFile, "utf8"),
    ) as LocalControlPlane;
  } else {
    globalStore.__convoyControlPlane = emptyLocalStore();
  }
  return globalStore.__convoyControlPlane;
}

function persistLocal(): void {
  fs.mkdirSync(dataDir, { recursive: true });
  const temporaryFile = `${localFile}.tmp`;
  fs.writeFileSync(
    temporaryFile,
    JSON.stringify(localStore(), null, 2),
  );
  fs.renameSync(temporaryFile, localFile);
}

function record<T>(item: Record<string, unknown> | undefined): T | null {
  return item?.data ? (item.data as T) : null;
}

function isTransactionConflict(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "TransactionCanceledException" ||
      error.name === "ConditionalCheckFailedException")
  );
}

export function usesAwsControlPlane(): boolean {
  return Boolean(tableName) && !postgres.enabled();
}

export async function createAccount(account: Account): Promise<void> {
  const normalizedEmail = account.email.toLowerCase();
  if (postgres.enabled()) {
    try {
      await postgres.createAccount({ ...account, email: normalizedEmail });
      return;
    } catch (error) {
      if (
        error instanceof Error &&
        "code" in error &&
        (error as Error & { code?: string }).code === "23505"
      ) {
        throw new ControlPlaneConflictError(
          "An account with this email already exists.",
        );
      }
      throw error;
    }
  }
  if (!tableName) {
    const store = localStore();
    if (store.accounts.some((item) => item.email === normalizedEmail)) {
      throw new ControlPlaneConflictError("An account with this email already exists.");
    }
    store.accounts.push({ ...account, email: normalizedEmail });
    persistLocal();
    return;
  }

  try {
    await dynamodb.send(
      new TransactWriteCommand({
        TransactItems: [
          {
            Put: {
              TableName: tableName,
              Item: {
                pk: `EMAIL#${normalizedEmail}`,
                sk: "ACCOUNT",
                entityType: "ACCOUNT_EMAIL",
                accountId: account.id,
              },
              ConditionExpression: "attribute_not_exists(pk)",
            },
          },
          {
            Put: {
              TableName: tableName,
              Item: {
                pk: `ACCOUNT#${account.id}`,
                sk: "PROFILE",
                entityType: "ACCOUNT",
                data: { ...account, email: normalizedEmail },
              },
              ConditionExpression: "attribute_not_exists(pk)",
            },
          },
        ],
      }),
    );
  } catch (error) {
    if (isTransactionConflict(error)) {
      throw new ControlPlaneConflictError(
        "An account with this email already exists.",
      );
    }
    throw error;
  }
}

export async function getAccount(accountId: string): Promise<Account | null> {
  if (postgres.enabled()) return postgres.getAccount(accountId);
  if (!tableName) {
    return localStore().accounts.find((item) => item.id === accountId) ?? null;
  }
  const response = await dynamodb.send(
    new GetCommand({
      TableName: tableName,
      Key: { pk: `ACCOUNT#${accountId}`, sk: "PROFILE" },
      ConsistentRead: true,
    }),
  );
  return record<Account>(response.Item);
}

export async function getAccountByEmail(email: string): Promise<Account | null> {
  const normalizedEmail = email.toLowerCase();
  if (postgres.enabled()) return postgres.getAccountByEmail(normalizedEmail);
  if (!tableName) {
    return (
      localStore().accounts.find((item) => item.email === normalizedEmail) ??
      null
    );
  }
  const lookup = await dynamodb.send(
    new GetCommand({
      TableName: tableName,
      Key: { pk: `EMAIL#${normalizedEmail}`, sk: "ACCOUNT" },
      ConsistentRead: true,
    }),
  );
  const accountId = lookup.Item?.accountId;
  return typeof accountId === "string" ? getAccount(accountId) : null;
}

export async function createSession(session: Session): Promise<void> {
  if (postgres.enabled()) return postgres.createSession(session);
  if (!tableName) {
    const store = localStore();
    store.sessions = store.sessions.filter(
      (item) => item.tokenHash !== session.tokenHash,
    );
    store.sessions.push(session);
    persistLocal();
    return;
  }
  await dynamodb.send(
    new PutCommand({
      TableName: tableName,
      Item: {
        pk: `SESSION#${session.tokenHash}`,
        sk: "SESSION",
        entityType: "SESSION",
        expiresAtEpoch: Math.floor(Date.parse(session.expiresAt) / 1000),
        data: session,
      },
    }),
  );
}

export async function getSession(tokenHash: string): Promise<Session | null> {
  if (postgres.enabled()) return postgres.getSession(tokenHash);
  if (!tableName) {
    return (
      localStore().sessions.find((item) => item.tokenHash === tokenHash) ?? null
    );
  }
  const response = await dynamodb.send(
    new GetCommand({
      TableName: tableName,
      Key: { pk: `SESSION#${tokenHash}`, sk: "SESSION" },
      ConsistentRead: true,
    }),
  );
  return record<Session>(response.Item);
}

export async function deleteSession(tokenHash: string): Promise<void> {
  if (postgres.enabled()) return postgres.deleteSession(tokenHash);
  if (!tableName) {
    const store = localStore();
    store.sessions = store.sessions.filter(
      (item) => item.tokenHash !== tokenHash,
    );
    persistLocal();
    return;
  }
  await dynamodb.send(
    new DeleteCommand({
      TableName: tableName,
      Key: { pk: `SESSION#${tokenHash}`, sk: "SESSION" },
    }),
  );
}

export async function createWorkspace(
  workspace: Workspace,
  membership: WorkspaceMembership,
): Promise<void> {
  if (postgres.enabled()) {
    return postgres.createWorkspace(workspace, membership);
  }
  if (!tableName) {
    const store = localStore();
    store.workspaces.push(workspace);
    store.memberships.push(membership);
    persistLocal();
    return;
  }
  await dynamodb.send(
    new TransactWriteCommand({
      TransactItems: [
        {
          Put: {
            TableName: tableName,
            Item: {
              pk: `WORKSPACE#${workspace.id}`,
              sk: "METADATA",
              entityType: "WORKSPACE",
              data: workspace,
            },
            ConditionExpression: "attribute_not_exists(pk)",
          },
        },
        {
          Put: {
            TableName: tableName,
            Item: {
              pk: `ACCOUNT#${membership.accountId}`,
              sk: `WORKSPACE#${workspace.id}`,
              entityType: "WORKSPACE_MEMBERSHIP",
              data: membership,
            },
          },
        },
        {
          Put: {
            TableName: tableName,
            Item: {
              pk: `WORKSPACE#${workspace.id}`,
              sk: `MEMBER#${membership.accountId}`,
              entityType: "WORKSPACE_MEMBERSHIP",
              data: membership,
            },
          },
        },
      ],
    }),
  );
}

export async function getWorkspace(
  workspaceId: string,
): Promise<Workspace | null> {
  if (postgres.enabled()) return postgres.getWorkspace(workspaceId);
  if (!tableName) {
    return (
      localStore().workspaces.find((item) => item.id === workspaceId) ?? null
    );
  }
  const response = await dynamodb.send(
    new GetCommand({
      TableName: tableName,
      Key: { pk: `WORKSPACE#${workspaceId}`, sk: "METADATA" },
      ConsistentRead: true,
    }),
  );
  return record<Workspace>(response.Item);
}

export async function getMembership(
  accountId: string,
  workspaceId: string,
): Promise<WorkspaceMembership | null> {
  if (postgres.enabled()) {
    return postgres.getMembership(accountId, workspaceId);
  }
  if (!tableName) {
    return (
      localStore().memberships.find(
        (item) =>
          item.accountId === accountId && item.workspaceId === workspaceId,
      ) ?? null
    );
  }
  const response = await dynamodb.send(
    new GetCommand({
      TableName: tableName,
      Key: {
        pk: `ACCOUNT#${accountId}`,
        sk: `WORKSPACE#${workspaceId}`,
      },
      ConsistentRead: true,
    }),
  );
  return record<WorkspaceMembership>(response.Item);
}

export async function listWorkspacesForAccount(
  accountId: string,
): Promise<Array<Workspace & { role: WorkspaceRole }>> {
  if (postgres.enabled()) {
    return postgres.listWorkspacesForAccount(accountId);
  }
  if (!tableName) {
    const store = localStore();
    return store.memberships
      .filter((item) => item.accountId === accountId)
      .map((membership) => {
        const workspace = store.workspaces.find(
          (item) => item.id === membership.workspaceId,
        );
        return workspace ? { ...workspace, role: membership.role } : null;
      })
      .filter(
        (item): item is Workspace & { role: WorkspaceRole } => item !== null,
      );
  }
  const response = await dynamodb.send(
    new QueryCommand({
      TableName: tableName,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :workspace)",
      ExpressionAttributeValues: {
        ":pk": `ACCOUNT#${accountId}`,
        ":workspace": "WORKSPACE#",
      },
      ConsistentRead: true,
    }),
  );
  const memberships = (response.Items ?? [])
    .map((item) => record<WorkspaceMembership>(item))
    .filter((item): item is WorkspaceMembership => item !== null);
  const workspaces = await Promise.all(
    memberships.map(async (membership) => {
      const workspace = await getWorkspace(membership.workspaceId);
      return workspace ? { ...workspace, role: membership.role } : null;
    }),
  );
  return workspaces
    .filter(
      (item): item is Workspace & { role: WorkspaceRole } => item !== null,
    )
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
}

function missionIndexItem(mission: Mission): Record<string, unknown> {
  return {
    pk: `WORKSPACE#${mission.workspaceId}`,
    sk: `MISSION#${mission.createdAt}#${mission.id}`,
    entityType: "CONTROL_MISSION_INDEX",
    data: mission,
  };
}

export async function createMission(mission: Mission): Promise<void> {
  if (postgres.enabled()) return postgres.createMission(mission);
  if (!tableName) {
    localStore().missions.push(mission);
    persistLocal();
    return;
  }
  await dynamodb.send(
    new TransactWriteCommand({
      TransactItems: [
        {
          Put: {
            TableName: tableName,
            Item: {
              pk: `MISSION#${mission.id}`,
              sk: "CONTROL",
              entityType: "CONTROL_MISSION",
              data: mission,
            },
            ConditionExpression: "attribute_not_exists(pk)",
          },
        },
        {
          Put: {
            TableName: tableName,
            Item: missionIndexItem(mission),
          },
        },
      ],
    }),
  );
}

export async function getMission(missionId: string): Promise<Mission | null> {
  if (postgres.enabled()) return postgres.getMission(missionId);
  if (!tableName) {
    return localStore().missions.find((item) => item.id === missionId) ?? null;
  }
  const response = await dynamodb.send(
    new GetCommand({
      TableName: tableName,
      Key: { pk: `MISSION#${missionId}`, sk: "CONTROL" },
      ConsistentRead: true,
    }),
  );
  return record<Mission>(response.Item);
}

export async function updateMission(
  missionId: string,
  patch: Partial<Mission>,
): Promise<Mission> {
  const current = await getMission(missionId);
  if (!current) throw new Error(`Mission ${missionId} was not found.`);
  const updated: Mission = {
    ...current,
    ...patch,
    id: current.id,
    workspaceId: current.workspaceId,
    createdAt: current.createdAt,
    updatedAt: new Date().toISOString(),
  };
  if (postgres.enabled()) {
    await postgres.updateMission(updated);
    return updated;
  }
  if (!tableName) {
    const store = localStore();
    const index = store.missions.findIndex((item) => item.id === missionId);
    store.missions[index] = updated;
    persistLocal();
    return updated;
  }
  await dynamodb.send(
    new TransactWriteCommand({
      TransactItems: [
        {
          Put: {
            TableName: tableName,
            Item: {
              pk: `MISSION#${updated.id}`,
              sk: "CONTROL",
              entityType: "CONTROL_MISSION",
              data: updated,
            },
          },
        },
        {
          Put: {
            TableName: tableName,
            Item: missionIndexItem(updated),
          },
        },
      ],
    }),
  );
  return updated;
}

export async function listMissions(
  workspaceId: string,
): Promise<Mission[]> {
  if (postgres.enabled()) return postgres.listMissions(workspaceId);
  if (!tableName) {
    return localStore().missions
      .filter((item) => item.workspaceId === workspaceId)
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }
  const response = await dynamodb.send(
    new QueryCommand({
      TableName: tableName,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :mission)",
      ExpressionAttributeValues: {
        ":pk": `WORKSPACE#${workspaceId}`,
        ":mission": "MISSION#",
      },
      ScanIndexForward: false,
      ConsistentRead: true,
    }),
  );
  return (response.Items ?? [])
    .map((item) => record<Mission>(item))
    .filter((item): item is Mission => item !== null);
}
