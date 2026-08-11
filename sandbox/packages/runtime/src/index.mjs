import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;

export class SandboxError extends Error {
  constructor(message, { code = "sandbox_error", status = 400 } = {}) {
    super(message);
    this.name = "SandboxError";
    this.code = code;
    this.status = status;
  }
}

function clone(value) {
  return structuredClone(value);
}

function requireId(value, label = "sandbox id") {
  if (!ID_PATTERN.test(value ?? "")) {
    throw new SandboxError(`Invalid ${label}: ${value}`, { code: "invalid_id" });
  }
  return value;
}

function hashSecret(value) {
  return createHash("sha256").update(String(value)).digest("hex");
}

function normalizeIdentities(identities = {}) {
  return Object.fromEntries(
    Object.entries(identities).map(([provider, principals]) => [
      provider,
      principals.map((principal) => {
        const normalized = { ...principal };
        if (normalized.token) {
          normalized.tokenHash = hashSecret(normalized.token);
          delete normalized.token;
        }
        return normalized;
      }),
    ]),
  );
}

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}

async function writeJsonAtomic(filePath, value) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, filePath);
}

export function tokenMatches(principal, token) {
  return Boolean(token) && principal?.tokenHash === hashSecret(token);
}

export function bearerToken(headers = {}) {
  const authorization = headers.authorization ?? headers.Authorization ?? "";
  const match = /^Bearer\s+(.+)$/i.exec(authorization);
  return match?.[1] ?? null;
}

export class SandboxRuntime {
  constructor({ root, providers = [], webhookDispatcher = null } = {}) {
    if (!root) throw new SandboxError("SandboxRuntime requires a storage root.");
    this.root = path.resolve(root);
    this.providers = new Map(providers.map((provider) => [provider.id, provider]));
    this.webhookDispatcher = webhookDispatcher;
    this.locks = new Map();
  }

  sandboxDirectory(sandboxId) {
    return path.join(this.root, "sandboxes", requireId(sandboxId));
  }

  statePath(sandboxId) {
    return path.join(this.sandboxDirectory(sandboxId), "state.json");
  }

  baselinePath(sandboxId) {
    return path.join(this.sandboxDirectory(sandboxId), "baseline.json");
  }

  snapshotPath(sandboxId, snapshotId) {
    return path.join(this.sandboxDirectory(sandboxId), "snapshots", `${requireId(snapshotId, "snapshot id")}.json`);
  }

  async initialize() {
    await mkdir(path.join(this.root, "sandboxes"), { recursive: true });
    await mkdir(path.join(this.root, "destroyed"), { recursive: true });
    return this;
  }

  async list() {
    await this.initialize();
    const entries = await readdir(path.join(this.root, "sandboxes"), { withFileTypes: true });
    const sandboxes = [];
    for (const entry of entries.filter((item) => item.isDirectory())) {
      try {
        const state = await readJson(this.statePath(entry.name));
        sandboxes.push(this.summary(state));
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
    return sandboxes.sort((a, b) => a.sandboxId.localeCompare(b.sandboxId));
  }

  summary(state) {
    return {
      sandboxId: state.sandboxId,
      status: state.status,
      scenario: state.scenario,
      clock: state.clock,
      providers: Object.keys(state.providers),
      updatedAt: state.updatedAt,
    };
  }

  async read(sandboxId) {
    try {
      return await readJson(this.statePath(sandboxId));
    } catch (error) {
      if (error.code === "ENOENT") {
        throw new SandboxError(`Sandbox not found: ${sandboxId}`, { code: "not_found", status: 404 });
      }
      throw error;
    }
  }

  async provision({ sandboxId, scenario = "connector-development", fixture = {}, clock = "2026-01-01T00:00:00.000Z" }) {
    requireId(sandboxId);
    try {
      await this.read(sandboxId);
      throw new SandboxError(`Sandbox already exists: ${sandboxId}`, { code: "already_exists", status: 409 });
    } catch (error) {
      if (!(error instanceof SandboxError) || error.code !== "not_found") throw error;
    }

    const now = new Date(clock);
    if (Number.isNaN(now.valueOf())) throw new SandboxError(`Invalid clock: ${clock}`);
    const state = {
      schemaVersion: 1,
      sandboxId,
      status: "active",
      scenario,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
      clock: { now: now.toISOString(), tick: 0 },
      identities: normalizeIdentities(fixture.identities),
      providers: {},
      webhooks: { destinations: clone(fixture.webhooks?.destinations ?? []), queue: [], deliveries: [] },
      requestJournal: {},
      auditLog: [],
      metadata: clone(fixture.metadata ?? {}),
      snapshotCount: 0,
    };
    for (const [providerId, provider] of this.providers) {
      state.providers[providerId] = await provider.seed(clone(fixture.providers?.[providerId] ?? {}), {
        sandboxId,
        clock: state.clock,
      });
    }
    state.auditLog.push({ id: "audit_0001", at: state.clock.now, event: "sandbox.provisioned", scenario });
    await writeJsonAtomic(this.statePath(sandboxId), state);
    await writeJsonAtomic(this.baselinePath(sandboxId), state);
    return clone(state);
  }

  async seed(sandboxId, fixture) {
    const state = await this.read(sandboxId);
    state.identities = normalizeIdentities(fixture.identities ?? {});
    for (const [providerId, provider] of this.providers) {
      state.providers[providerId] = await provider.seed(clone(fixture.providers?.[providerId] ?? {}), {
        sandboxId,
        clock: state.clock,
      });
    }
    state.webhooks = { destinations: clone(fixture.webhooks?.destinations ?? []), queue: [], deliveries: [] };
    state.metadata = clone(fixture.metadata ?? {});
    state.updatedAt = state.clock.now;
    this.record(state, "sandbox.seeded", {});
    await writeJsonAtomic(this.statePath(sandboxId), state);
    await writeJsonAtomic(this.baselinePath(sandboxId), state);
    return clone(state);
  }

  async inspect(sandboxId, { includeSecrets = false } = {}) {
    const state = await this.read(sandboxId);
    if (includeSecrets) return clone(state);
    const visible = clone(state);
    for (const principals of Object.values(visible.identities)) {
      for (const principal of principals) delete principal.tokenHash;
    }
    for (const providerState of Object.values(visible.providers)) {
      if (providerState?.accessTokens) providerState.accessTokens = Object.keys(providerState.accessTokens);
    }
    return visible;
  }

  record(state, event, detail) {
    state.auditLog.push({
      id: `audit_${String(state.auditLog.length + 1).padStart(4, "0")}`,
      at: state.clock.now,
      event,
      ...clone(detail),
    });
  }

  async invoke(sandboxId, providerId, request) {
    const previous = this.locks.get(sandboxId) ?? Promise.resolve();
    let release;
    const current = new Promise((resolve) => { release = resolve; });
    this.locks.set(sandboxId, current);
    await previous;
    try {
      return await this._invokeUnlocked(sandboxId, providerId, request);
    } finally {
      release();
      if (this.locks.get(sandboxId) === current) this.locks.delete(sandboxId);
    }
  }

  async _invokeUnlocked(sandboxId, providerId, request) {
    const provider = this.providers.get(providerId);
    if (!provider) throw new SandboxError(`Unknown provider: ${providerId}`, { code: "unknown_provider", status: 404 });
    const state = await this.read(sandboxId);
    if (state.status !== "active") {
      throw new SandboxError(`Sandbox is ${state.status}: ${sandboxId}`, { code: "inactive", status: 409 });
    }
    const idempotencyKey = request.headers?.["idempotency-key"] ?? request.headers?.["x-convoy-idempotency-key"];
    if (idempotencyKey && state.requestJournal[idempotencyKey]) {
      this.record(state, "provider.request_replayed", { provider: providerId, idempotencyKey });
      await writeJsonAtomic(this.statePath(sandboxId), state);
      return clone(state.requestJournal[idempotencyKey]);
    }
    const providerState = state.providers[providerId];
    const emitted = [];
    const emitWebhook = (event) => emitted.push({ provider: providerId, ...clone(event) });
    const response = await provider.handle({
      request: clone(request),
      state: providerState,
      identities: state.identities[providerId] ?? [],
      clock: state.clock,
      emitWebhook,
      sandboxId,
    });
    if (this.webhookDispatcher) {
      for (const event of emitted) this.webhookDispatcher.enqueue(state, event);
    }
    if (idempotencyKey) state.requestJournal[idempotencyKey] = clone(response);
    state.updatedAt = state.clock.now;
    this.record(state, "provider.request", {
      provider: providerId,
      method: request.method,
      path: request.path,
      status: response.status,
      principalId: response.principalId,
    });
    await writeJsonAtomic(this.statePath(sandboxId), state);
    return response;
  }

  async snapshot(sandboxId, { label = "" } = {}) {
    const state = await this.read(sandboxId);
    state.snapshotCount += 1;
    const snapshotId = `snap_${String(state.snapshotCount).padStart(4, "0")}`;
    const snapshot = {
      snapshotId,
      sandboxId,
      label,
      createdAt: state.clock.now,
      state: clone(state),
    };
    this.record(state, "sandbox.snapshotted", { snapshotId, label });
    await writeJsonAtomic(this.snapshotPath(sandboxId, snapshotId), snapshot);
    await writeJsonAtomic(this.statePath(sandboxId), state);
    return { snapshotId, sandboxId, label, createdAt: snapshot.createdAt };
  }

  async restore(sandboxId, snapshotId) {
    const current = await this.read(sandboxId);
    const snapshot = await readJson(this.snapshotPath(sandboxId, snapshotId));
    const restored = clone(snapshot.state);
    restored.snapshotCount = Math.max(current.snapshotCount, restored.snapshotCount);
    restored.status = "active";
    restored.updatedAt = restored.clock.now;
    this.record(restored, "sandbox.restored", { snapshotId });
    await writeJsonAtomic(this.statePath(sandboxId), restored);
    return clone(restored);
  }

  async fork(sourceSandboxId, snapshotId, targetSandboxId) {
    requireId(targetSandboxId);
    try {
      await this.read(targetSandboxId);
      throw new SandboxError(`Sandbox already exists: ${targetSandboxId}`, { code: "already_exists", status: 409 });
    } catch (error) {
      if (!(error instanceof SandboxError) || error.code !== "not_found") throw error;
    }
    const snapshot = await readJson(this.snapshotPath(sourceSandboxId, snapshotId));
    const forked = clone(snapshot.state);
    forked.sandboxId = targetSandboxId;
    forked.status = "active";
    forked.snapshotCount = 0;
    forked.createdAt = forked.clock.now;
    forked.updatedAt = forked.clock.now;
    this.record(forked, "sandbox.forked", { sourceSandboxId, snapshotId });
    await writeJsonAtomic(this.statePath(targetSandboxId), forked);
    await writeJsonAtomic(this.baselinePath(targetSandboxId), forked);
    return clone(forked);
  }

  async reset(sandboxId) {
    const current = await this.read(sandboxId);
    const baseline = await readJson(this.baselinePath(sandboxId));
    baseline.snapshotCount = current.snapshotCount;
    baseline.status = "active";
    this.record(baseline, "sandbox.reset", {});
    await writeJsonAtomic(this.statePath(sandboxId), baseline);
    return clone(baseline);
  }

  async advanceClock(sandboxId, milliseconds) {
    const state = await this.read(sandboxId);
    if (!Number.isInteger(milliseconds) || milliseconds < 0) {
      throw new SandboxError("milliseconds must be a non-negative integer");
    }
    state.clock.now = new Date(Date.parse(state.clock.now) + milliseconds).toISOString();
    state.clock.tick += 1;
    if (this.webhookDispatcher) await this.webhookDispatcher.deliverDue(state);
    state.updatedAt = state.clock.now;
    this.record(state, "clock.advanced", { milliseconds });
    await writeJsonAtomic(this.statePath(sandboxId), state);
    return clone(state.clock);
  }

  async destroy(sandboxId, { purge = false } = {}) {
    const state = await this.read(sandboxId);
    state.status = "destroyed";
    state.updatedAt = state.clock.now;
    this.record(state, "sandbox.destroyed", { purge });
    await writeJsonAtomic(this.statePath(sandboxId), state);
    if (purge) {
      await rm(this.sandboxDirectory(sandboxId), { recursive: true, force: false });
    } else {
      const destination = path.join(this.root, "destroyed", `${sandboxId}-${Date.now()}`);
      await rename(this.sandboxDirectory(sandboxId), destination);
    }
    return { sandboxId, status: "destroyed", purged: purge };
  }
}
