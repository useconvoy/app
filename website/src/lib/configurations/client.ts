"use client";
/**
 * Workspace document client. `WorkspaceStore` reads the owner's "configurations"
 * document through `/api/platform/workspace-documents/{name}`, validates it and
 * falls back to the generic sample (404: no document yet; invalid or unreadable:
 * with a notice). `save()` writes with PUT and an Idempotency-Key, shows the
 * change optimistically, rolls it back on failure, and on a 409 conflict re-reads
 * the document and re-applies an updater function once.
 *
 * React: mount `WorkspaceProvider` once (the Configurations layout does) and call
 * `useWorkspace()` in pages. The pure selectors are re-exported here.
 */
import { createContext, createElement, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { createSampleWorkspace } from "./sample";
import { notifySessionExpired } from "./session-events";
import { WORKSPACE_DOCUMENT_NAME, WORKSPACE_SCHEMA_VERSION } from "./types";
import type { ConvoyWorkspace } from "./types";
import { summarizeIssues, validateWorkspace } from "./validate";
import type { ValidationIssue } from "./validate";

export * from "./selectors";

export type WorkspaceSource = "document" | "sample";
/** Why the sample is shown: no document yet (404), the document failed validation, or it could not be read. */
export type SampleReason = "missing" | "invalid" | "unavailable";
interface SnapshotCommon {
  /** Validation problems of the stored document (reason "invalid"). */
  issues: ValidationIssue[];
  /** Unknown keys in the stored document (kept, ignored). */
  warnings: ValidationIssue[];
  /** Last load or save failure, for a notice. */
  error: string | null;
  /** Server `updated_at` of the stored document. */
  documentUpdatedAt: string | null;
  saving: boolean;
  /** False while the sample stands in for an unreadable or invalid document (saving would overwrite it). */
  canSave: boolean;
}
export type WorkspaceSnapshot =
  | (SnapshotCommon & { status: "loading"; workspace: null; source: null; reason: null })
  | (SnapshotCommon & { status: "ready"; workspace: ConvoyWorkspace; source: WorkspaceSource; reason: SampleReason | null });

/** A full replacement, or (preferred) an updater that is re-applied to a fresher document after a conflict. */
export type WorkspaceUpdate = ConvoyWorkspace | ((current: ConvoyWorkspace) => ConvoyWorkspace);
export type SaveResult =
  | { ok: true; workspace: ConvoyWorkspace }
  | { ok: false; error: string; issues?: ValidationIssue[]; conflict?: boolean };

export interface WorkspaceStoreOptions {
  /** Defaults to the browser fetch. */
  fetch?: typeof fetch;
  /** Document name; defaults to "configurations". */
  name?: string;
  /** Wall clock for the sample's relative times. */
  now?: () => number;
  createKey?: () => string;
}

const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
function errorMessage(data: unknown, status: number): string {
  const error = record(data).error;
  if (typeof error === "string" && error) return error;
  const message = record(error).message;
  if (typeof message === "string" && message) return message;
  return status === 413 ? "The workspace is too large to save." : status === 429 ? "Too many requests. Wait a moment and try again." : "The workspace service is unavailable. Try again.";
}
const LOADING: WorkspaceSnapshot = { status: "loading", workspace: null, source: null, reason: null, issues: [], warnings: [], error: null, documentUpdatedAt: null, saving: false, canSave: false };
/** Store state apart from the displayed workspace. */
interface Base {
  status: "loading" | "ready";
  source: WorkspaceSource | null;
  reason: SampleReason | null;
  issues: ValidationIssue[];
  warnings: ValidationIssue[];
  error: string | null;
  documentUpdatedAt: string | null;
}

export class WorkspaceStore {
  private fetcher: typeof fetch;
  private name: string;
  private now: () => number;
  private createKey: () => string;
  private listeners = new Set<() => void>();
  private snapshot: WorkspaceSnapshot = LOADING;
  /** Last document confirmed by the server (or the sample standing in for it). */
  private confirmed: ConvoyWorkspace | null = null;
  private pending: Array<{ id: number; apply: (current: ConvoyWorkspace) => ConvoyWorkspace }> = [];
  private sequence = 0;
  private queue: Promise<unknown> = Promise.resolve();
  private loading: Promise<void> | null = null;
  private keys = new Map<string, string>();
  private base: Base = { status: "loading", source: null, reason: null, issues: [], warnings: [], error: null, documentUpdatedAt: null };

  constructor(options: WorkspaceStoreOptions = {}) {
    this.fetcher = options.fetch ?? ((input, init) => fetch(input, init));
    this.name = options.name ?? WORKSPACE_DOCUMENT_NAME;
    this.now = options.now ?? (() => Date.now());
    this.createKey = options.createKey ?? (() => crypto.randomUUID());
  }

  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = (): WorkspaceSnapshot => this.snapshot;
  getServerSnapshot = (): WorkspaceSnapshot => LOADING;

  /** First read; later calls return the same promise. */
  load = (): Promise<void> => (this.loading ??= this.read());
  /** Reads the document again (a transient failure keeps the document already shown). */
  refresh = (): Promise<void> => this.read();

  /** Writes a change. Updates apply in order; each shows immediately and rolls back if the write fails. */
  save = (update: WorkspaceUpdate): Promise<SaveResult> => this.enqueue(update, false);

  /** Validates and stores a whole document (the "Import workspace" action); allowed even when the stored one is invalid. */
  importDocument = async (value: unknown): Promise<SaveResult> => {
    const result = validateWorkspace(value);
    if (!result.ok) return { ok: false, error: `This file is not a valid workspace: ${summarizeIssues(result.issues)}`, issues: result.issues };
    return this.enqueue(result.workspace, true);
  };

  private url() { return `/api/platform/workspace-documents/${encodeURIComponent(this.name)}`; }
  private async request(method: "GET" | "PUT", body?: string, key?: string): Promise<{ status: number; data: unknown }> {
    const response = await this.fetcher(this.url(), {
      method, credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Convoy-Client": "web", ...(key ? { "Idempotency-Key": key } : {}) },
      ...(body === undefined ? {} : { body }),
    });
    return { status: response.status, data: await response.json().catch(() => null) };
  }

  /** GET + validate. Returns the document, "missing" (404), or a failure. */
  private async fetchDocument(): Promise<{ kind: "document"; workspace: ConvoyWorkspace; updatedAt: string | null; warnings: ValidationIssue[] } | { kind: "missing" } | { kind: "invalid"; issues: ValidationIssue[] } | { kind: "error"; status: number; message: string }> {
    let response: { status: number; data: unknown };
    try { response = await this.request("GET"); }
    catch { return { kind: "error", status: 0, message: "The workspace document could not be reached." }; }
    if (response.status === 404) return { kind: "missing" };
    if (response.status === 401) notifySessionExpired();
    if (response.status !== 200) return { kind: "error", status: response.status, message: errorMessage(response.data, response.status) };
    const envelope = record(response.data);
    if (envelope.schema_version !== undefined && envelope.schema_version !== WORKSPACE_SCHEMA_VERSION) {
      return { kind: "invalid", issues: [{ path: "schema_version", message: `expected ${WORKSPACE_SCHEMA_VERSION}, found ${String(envelope.schema_version)}` }] };
    }
    const result = validateWorkspace(envelope.body);
    if (!result.ok) return { kind: "invalid", issues: result.issues };
    return { kind: "document", workspace: result.workspace, updatedAt: typeof envelope.updated_at === "string" ? envelope.updated_at : null, warnings: result.warnings };
  }

  private async read(): Promise<void> {
    const result = await this.fetchDocument();
    if (result.kind === "document") {
      this.confirmed = result.workspace;
      this.base = { status: "ready", source: "document", reason: null, issues: [], warnings: result.warnings, error: null, documentUpdatedAt: result.updatedAt };
    } else if (this.base.source === "document" && result.kind === "error") {
      this.base = { ...this.base, error: `${result.message} Showing the last version read.` };
    } else {
      this.confirmed = createSampleWorkspace(this.now());
      this.base = result.kind === "missing"
        ? { status: "ready", source: "sample", reason: "missing", issues: [], warnings: [], error: null, documentUpdatedAt: null }
        : result.kind === "invalid"
          ? { status: "ready", source: "sample", reason: "invalid", issues: result.issues, warnings: [], error: `The stored workspace is not valid: ${summarizeIssues(result.issues)}`, documentUpdatedAt: null }
          : { status: "ready", source: "sample", reason: "unavailable", issues: [], warnings: [], error: result.message, documentUpdatedAt: null };
    }
    this.emit();
  }

  private enqueue(update: WorkspaceUpdate, replace: boolean): Promise<SaveResult> {
    const id = ++this.sequence;
    const apply = typeof update === "function" ? update : () => update;
    this.pending.push({ id, apply });
    this.emit();
    const run = this.queue.then(() => this.commit(id, apply, typeof update === "function", replace));
    this.queue = run.catch(() => undefined);
    return run;
  }

  private async commit(id: number, apply: (current: ConvoyWorkspace) => ConvoyWorkspace, reapply: boolean, replace: boolean): Promise<SaveResult> {
    const done = (result: SaveResult): SaveResult => {
      this.pending = this.pending.filter(item => item.id !== id);
      if (!result.ok) this.base = { ...this.base, error: result.error };
      this.emit();
      return result;
    };
    if (this.base.status !== "ready" || !this.confirmed) return done({ ok: false, error: "The workspace is still loading. Try again in a moment." });
    if (!replace && this.base.source === "sample" && this.base.reason !== "missing") {
      return done({ ok: false, error: "The stored workspace could not be read, so changes are not saved. Reload to try again, or import a valid workspace." });
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      let next: ConvoyWorkspace;
      try { next = apply(this.confirmed); }
      catch (cause) { return done({ ok: false, error: cause instanceof Error ? cause.message : "The change could not be applied." }); }
      const validation = validateWorkspace(next);
      if (!validation.ok) return done({ ok: false, error: `The change was not saved: ${summarizeIssues(validation.issues)}`, issues: validation.issues });
      const body = JSON.stringify({ schema_version: WORKSPACE_SCHEMA_VERSION, body: next });
      // An identical retried write reuses its key, so an uncertain earlier attempt cannot apply twice.
      const key = this.keys.get(body) ?? this.createKey();
      this.keys.set(body, key);
      let response: { status: number; data: unknown };
      try { response = await this.request("PUT", body, key); }
      catch { return done({ ok: false, error: "The change could not be confirmed. Check the connection and try again; a retry of the same change reuses its request key." }); }
      this.keys.delete(body);
      if (response.status === 200 || response.status === 201) {
        const envelope = record(response.data);
        const stored = validateWorkspace(envelope.body);
        this.confirmed = stored.ok ? stored.workspace : next;
        this.base = { status: "ready", source: "document", reason: null, issues: [], warnings: stored.ok ? stored.warnings : [], error: null, documentUpdatedAt: typeof envelope.updated_at === "string" ? envelope.updated_at : this.base.documentUpdatedAt };
        return done({ ok: true, workspace: this.confirmed });
      }
      if (response.status === 401) { notifySessionExpired(); return done({ ok: false, error: "Your session ended. Sign in to continue." }); }
      if (response.status === 409 && attempt === 0) {
        const fresh = await this.fetchDocument();
        if (fresh.kind !== "document") return done({ ok: false, conflict: true, error: "The workspace changed elsewhere and could not be re-read. Reload before trying again." });
        this.confirmed = fresh.workspace;
        this.base = { ...this.base, documentUpdatedAt: fresh.updatedAt };
        if (!reapply) return done({ ok: false, conflict: true, error: "The workspace changed elsewhere. Review the latest version and make the change again." });
        continue;
      }
      return done({ ok: false, conflict: response.status === 409, error: errorMessage(response.data, response.status) });
    }
    return done({ ok: false, conflict: true, error: "The workspace changed again while saving. Reload before trying again." });
  }

  private emit() {
    const { status, source, reason, issues, warnings, error, documentUpdatedAt } = this.base;
    const saving = this.pending.length > 0;
    if (status === "ready" && source && this.confirmed) {
      // Pending updates show immediately; a failing updater is skipped here and reported by its save.
      const workspace = this.pending.reduce((current, item) => {
        try { return item.apply(current); } catch { return current; }
      }, this.confirmed);
      this.snapshot = { status, workspace, source, reason, issues, warnings, error, documentUpdatedAt, saving, canSave: source === "document" || reason === "missing" };
    } else {
      this.snapshot = { ...LOADING, saving };
    }
    for (const listener of [...this.listeners]) listener();
  }
}

/* ---------- React ---------- */

const WorkspaceContext = createContext<WorkspaceStore | null>(null);

/** Mount once per signed-in Configurations session; it loads the document on mount. */
export function WorkspaceProvider({ children, store }: { children: ReactNode; store?: WorkspaceStore }) {
  const [instance] = useState(() => store ?? new WorkspaceStore());
  useEffect(() => { void instance.load(); }, [instance]);
  return createElement(WorkspaceContext.Provider, { value: instance }, children);
}

export type WorkspaceView = WorkspaceSnapshot & Pick<WorkspaceStore, "save" | "refresh" | "importDocument">;

/** The workspace state and actions. `workspace` is non-null once `status === "ready"`. */
export function useWorkspace(): WorkspaceView {
  const store = useContext(WorkspaceContext);
  if (!store) throw new Error("useWorkspace() needs a WorkspaceProvider above it.");
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getServerSnapshot);
  return useMemo(() => ({ ...snapshot, save: store.save, refresh: store.refresh, importDocument: store.importDocument }), [snapshot, store]);
}
