"use client";
/**
 * Workspace document client. `WorkspaceStore` reads the owner's "configurations"
 * document through `/api/platform/workspace-documents/{name}`, validates it and
 * falls back to the generic sample (404: no document yet; invalid or unreadable:
 * with a notice).
 *
 * Writes follow the documents contract: every PUT names what it replaces —
 * `If-Match: "<revision>"` for the stored document, `If-None-Match: *` while there
 * is none — so a save never overwrites a newer document and the sample is never
 * written over a document that appeared meanwhile. The PUT answers with metadata
 * (the new `revision`), so the document sent becomes the confirmed one. A change
 * shows at once (optimistic) and rolls back if the write fails; on 412 the store
 * re-reads the document and re-applies an updater function once, otherwise it
 * reports the conflict. Each write carries an Idempotency-Key, reused only when the
 * identical write is retried.
 *
 * React: mount `WorkspaceProvider` once (the Configurations layout does) and call
 * `useWorkspace()` in pages. The provider re-reads the document when the window
 * regains focus or the tab becomes visible. The pure selectors are re-exported here.
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
  /** Server `revision` of the stored document (also when it is invalid); null when there is none or it is unknown. */
  documentRevision: number | null;
  /** A document is stored for this account: true (valid or not), false (none yet), null (unknown: it could not be read). */
  documentExists: boolean | null;
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
  /** Clock for spacing background re-reads (default Date.now). */
  clock?: () => number;
  createKey?: () => string;
}

/** A focus or visibility re-read is skipped when the last read started less than this long ago. */
export const REFRESH_MIN_GAP_MS = 10_000;
/** Largest workspace file accepted for import, as the documents API stores it (2 MiB of JSON). */
export const IMPORT_MAX_BYTES = 2 * 1024 * 1024;

const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const revisionOf = (value: unknown): number | null => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
function serverText(data: unknown): string | null {
  const error = record(data).error;
  if (typeof error === "string" && error) return error;
  const message = record(error).message;
  return typeof message === "string" && message ? message : null;
}
function errorMessage(data: unknown, status: number): string {
  return serverText(data) ?? (status === 413 ? "The workspace is too large to save." : status === 429 ? "Too many requests. Wait a moment and try again." : "The workspace service is unavailable. Try again.");
}

interface Reply { status: number; data: unknown; retryAfter: string | null }

/**
 * Why a write was refused, in words that say what to do: the account's document
 * limit and a reused request key (both 409), a missing precondition (428), too
 * many writes (429, with Retry-After), a document that is too large (413).
 */
export function saveErrorMessage(reply: Pick<Reply, "status" | "data" | "retryAfter">): string {
  const text = serverText(reply.data) ?? "";
  switch (reply.status) {
    case 409:
      if (/limit/i.test(text)) return "This account already stores the most workspace documents allowed, so a new one cannot be created. Delete one first.";
      if (/idempot|request key|already submitted/i.test(text)) return "This save reused the request key of a different change, so it was refused. Make the change again.";
      return "The save conflicted with another request. Reload to see the latest version, then make the change again.";
    case 412: return "The workspace changed in another tab or session, so this change was not saved. Reload to see the latest version, then make it again.";
    case 413: return "The workspace is too large to save (2 MiB at most).";
    case 428: return "The save was refused because it did not name the version it replaces. Reload, then make the change again.";
    case 429: {
      const seconds = Number(reply.retryAfter);
      return Number.isFinite(seconds) && seconds > 0 ? `Too many saves in a short time. Wait ${Math.ceil(seconds)} s, then try again.` : "Too many saves in a short time. Wait a moment, then try again.";
    }
    default: return errorMessage(reply.data, reply.status);
  }
}

const LOADING: WorkspaceSnapshot = { status: "loading", workspace: null, source: null, reason: null, issues: [], warnings: [], error: null, documentUpdatedAt: null, documentRevision: null, documentExists: null, saving: false, canSave: false };
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
type Fetched =
  | { kind: "document"; workspace: ConvoyWorkspace; updatedAt: string | null; revision: number | null; warnings: ValidationIssue[] }
  | { kind: "missing" }
  | { kind: "invalid"; issues: ValidationIssue[]; updatedAt: string | null; revision: number | null }
  | { kind: "error"; status: number; message: string };
/** save: change the document (or create it from the sample) · create / replace: import a whole file. */
type WriteMode = "save" | "create" | "replace";

export class WorkspaceStore {
  private fetcher: typeof fetch;
  private name: string;
  private now: () => number;
  private clock: () => number;
  private createKey: () => string;
  private listeners = new Set<() => void>();
  private snapshot: WorkspaceSnapshot = LOADING;
  /** Last document confirmed by the server (or the sample standing in for it). */
  private confirmed: ConvoyWorkspace | null = null;
  /** Revision of the stored document (valid or invalid); null when none is stored or the server does not report it. */
  private revision: number | null = null;
  /** Whether a document is stored: true, false (404), or null (unknown). */
  private exists: boolean | null = null;
  private pending: Array<{ id: number; apply: (current: ConvoyWorkspace) => ConvoyWorkspace }> = [];
  private sequence = 0;
  /** Completed writes; a read that started before one is older than the document it confirmed. */
  private writes = 0;
  private lastReadAt = Number.NEGATIVE_INFINITY;
  private reading = false;
  private queue: Promise<unknown> = Promise.resolve();
  private loading: Promise<void> | null = null;
  private keys = new Map<string, string>();
  private base: Base = { status: "loading", source: null, reason: null, issues: [], warnings: [], error: null, documentUpdatedAt: null };

  constructor(options: WorkspaceStoreOptions = {}) {
    this.fetcher = options.fetch ?? ((input, init) => fetch(input, init));
    this.name = options.name ?? WORKSPACE_DOCUMENT_NAME;
    this.now = options.now ?? (() => Date.now());
    this.clock = options.clock ?? (() => Date.now());
    this.createKey = options.createKey ?? (() => crypto.randomUUID());
  }

  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = (): WorkspaceSnapshot => this.snapshot;
  getServerSnapshot = (): WorkspaceSnapshot => LOADING;

  /** First read; later calls return the same promise. */
  load = (): Promise<void> => (this.loading ??= this.read());
  /** Reads the document again (a transient failure keeps the document already shown). */
  refresh = (): Promise<void> => this.read();
  /** A background re-read (window focus, tab visible): skipped while saving or reading, or right after another read. */
  refreshIfIdle = (): Promise<void> => {
    if (this.base.status !== "ready" || this.pending.length || this.reading || this.clock() - this.lastReadAt < REFRESH_MIN_GAP_MS) return Promise.resolve();
    return this.read();
  };

  /** Writes a change. Updates apply in order; each shows immediately and rolls back if the write fails. */
  save = (update: WorkspaceUpdate): Promise<SaveResult> => this.enqueue(update, "save");

  /**
   * Validates and stores a whole document (the "Import workspace" action); allowed
   * even when the stored one is invalid. With a document stored it only replaces it
   * when `replace` is set (the page confirms first); without one it creates it and
   * never overwrites a document stored meanwhile.
   */
  importDocument = async (value: unknown, options: { replace?: boolean } = {}): Promise<SaveResult> => {
    const result = validateWorkspace(value);
    if (!result.ok) return { ok: false, error: `This file is not a valid workspace: ${summarizeIssues(result.issues)}`, issues: result.issues };
    if (this.exists === true && !options.replace) return { ok: false, error: "A workspace document is already stored for this account. Confirm to replace it." };
    return this.enqueue(result.workspace, this.exists === true ? "replace" : "create");
  };

  private url() { return `/api/platform/workspace-documents/${encodeURIComponent(this.name)}`; }
  private async request(method: "GET" | "PUT", body?: string, key?: string, precondition: Record<string, string> = {}): Promise<Reply> {
    const response = await this.fetcher(this.url(), {
      method, credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Convoy-Client": "web", ...(key ? { "Idempotency-Key": key } : {}), ...precondition },
      ...(body === undefined ? {} : { body }),
    });
    return { status: response.status, data: await response.json().catch(() => null), retryAfter: response.headers?.get?.("Retry-After") ?? null };
  }
  /** What the next PUT replaces: the stored revision, nothing (create), or — for a server that reports no revision — no precondition. */
  private precondition(): Record<string, string> {
    if (this.exists === true) return this.revision === null ? {} : { "If-Match": `"${this.revision}"` };
    return { "If-None-Match": "*" };
  }

  /** GET + validate. Returns the document, "missing" (404), an invalid document, or a failure. */
  private async fetchDocument(): Promise<Fetched> {
    let response: Reply;
    try { response = await this.request("GET"); }
    catch { return { kind: "error", status: 0, message: "The workspace document could not be reached." }; }
    if (response.status === 404) return { kind: "missing" };
    if (response.status === 401) notifySessionExpired();
    if (response.status !== 200) return { kind: "error", status: response.status, message: errorMessage(response.data, response.status) };
    const envelope = record(response.data);
    const revision = revisionOf(envelope.revision);
    const updatedAt = typeof envelope.updated_at === "string" ? envelope.updated_at : null;
    if (envelope.schema_version !== undefined && envelope.schema_version !== WORKSPACE_SCHEMA_VERSION) {
      return { kind: "invalid", revision, updatedAt, issues: [{ path: "schema_version", message: `expected ${WORKSPACE_SCHEMA_VERSION}, found ${String(envelope.schema_version)}` }] };
    }
    const result = validateWorkspace(envelope.body);
    if (!result.ok) return { kind: "invalid", issues: result.issues, revision, updatedAt };
    return { kind: "document", workspace: result.workspace, updatedAt, revision, warnings: result.warnings };
  }

  /** Takes a read as the state: the document, or the sample with the reason. A failed read keeps a document already shown. */
  private adopt(result: Fetched) {
    if (result.kind === "document") {
      this.confirmed = result.workspace;
      this.revision = result.revision;
      this.exists = true;
      this.base = { status: "ready", source: "document", reason: null, issues: [], warnings: result.warnings, error: null, documentUpdatedAt: result.updatedAt };
    } else if (this.base.source === "document" && result.kind === "error") {
      this.base = { ...this.base, error: `${result.message} Showing the last version read.` };
    } else {
      this.confirmed = createSampleWorkspace(this.now());
      if (result.kind === "missing") {
        this.revision = null; this.exists = false;
        this.base = { status: "ready", source: "sample", reason: "missing", issues: [], warnings: [], error: null, documentUpdatedAt: null };
      } else if (result.kind === "invalid") {
        this.revision = result.revision; this.exists = true;
        this.base = { status: "ready", source: "sample", reason: "invalid", issues: result.issues, warnings: [], error: `The stored workspace is not valid: ${summarizeIssues(result.issues)}`, documentUpdatedAt: result.updatedAt };
      } else {
        this.revision = null; this.exists = null;
        this.base = { status: "ready", source: "sample", reason: "unavailable", issues: [], warnings: [], error: result.message, documentUpdatedAt: null };
      }
    }
  }

  private async read(): Promise<void> {
    const writes = this.writes;
    this.reading = true;
    this.lastReadAt = this.clock();
    let result: Fetched;
    try { result = await this.fetchDocument(); }
    finally { this.reading = false; }
    // A save confirmed while this read was in flight is newer than what the read returned.
    if (this.writes !== writes) return;
    if (result.kind === "document" && this.revision !== null && result.revision !== null && result.revision < this.revision) return;
    this.adopt(result);
    this.emit();
  }

  private enqueue(update: WorkspaceUpdate, mode: WriteMode): Promise<SaveResult> {
    const id = ++this.sequence;
    const apply = typeof update === "function" ? update : () => update;
    this.pending.push({ id, apply });
    this.emit();
    const run = this.queue.then(() => this.commit(id, apply, typeof update === "function", mode));
    this.queue = run.catch(() => undefined);
    return run;
  }

  private async commit(id: number, apply: (current: ConvoyWorkspace) => ConvoyWorkspace, reapply: boolean, mode: WriteMode): Promise<SaveResult> {
    const done = (result: SaveResult): SaveResult => {
      this.pending = this.pending.filter(item => item.id !== id);
      if (!result.ok) this.base = { ...this.base, error: result.error };
      this.emit();
      return result;
    };
    if (this.base.status !== "ready" || !this.confirmed) return done({ ok: false, error: "The workspace is still loading. Try again in a moment." });
    if (mode === "save" && this.base.source === "sample" && this.base.reason !== "missing") {
      return done({ ok: false, error: "The stored workspace could not be read, so changes are not saved. Reload to try again, or import a valid workspace." });
    }
    if (mode === "create" && this.exists === true) return done({ ok: false, error: "A workspace document is already stored for this account. Confirm to replace it." });
    for (let attempt = 0; attempt < 2; attempt++) {
      let next: ConvoyWorkspace;
      try { next = apply(this.confirmed); }
      catch (cause) { return done({ ok: false, error: cause instanceof Error ? cause.message : "The change could not be applied." }); }
      const validation = validateWorkspace(next);
      if (!validation.ok) return done({ ok: false, error: `The change was not saved: ${summarizeIssues(validation.issues)}`, issues: validation.issues });
      const precondition = this.precondition();
      const body = JSON.stringify({ schema_version: WORKSPACE_SCHEMA_VERSION, body: next });
      // An identical retried write (same document, same precondition) reuses its key, so an uncertain earlier attempt cannot apply twice.
      const write = `${JSON.stringify(precondition)}\n${body}`;
      const key = this.keys.get(write) ?? this.createKey();
      this.keys.set(write, key);
      let response: Reply;
      try { response = await this.request("PUT", body, key, precondition); }
      catch { return done({ ok: false, error: "The change could not be confirmed. Check the connection and try again; a retry of the same change reuses its request key." }); }
      this.keys.delete(write);
      if (response.status === 200 || response.status === 201) {
        const meta = record(response.data);
        // The documents API answers with metadata; an older server echoed the stored body.
        const echo = meta.body !== undefined ? validateWorkspace(meta.body) : null;
        this.confirmed = echo?.ok ? echo.workspace : next;
        this.revision = revisionOf(meta.revision);
        this.exists = true;
        this.writes++;
        this.base = { status: "ready", source: "document", reason: null, issues: [], warnings: echo?.ok ? echo.warnings : validation.warnings, error: null, documentUpdatedAt: typeof meta.updated_at === "string" ? meta.updated_at : this.base.documentUpdatedAt };
        return done({ ok: true, workspace: this.confirmed });
      }
      if (response.status === 401) { notifySessionExpired(); return done({ ok: false, error: "Your session ended. Sign in to continue." }); }
      if (response.status === 412) {
        // Someone else wrote first (or created the document): re-read, never write over it blindly.
        const fresh = await this.fetchDocument();
        this.adopt(fresh);
        if (attempt === 0 && reapply && fresh.kind === "document") continue;
        return done({ ok: false, conflict: true, error: conflictMessage(mode, fresh) });
      }
      return done({ ok: false, conflict: response.status === 409, error: saveErrorMessage(response) });
    }
    return done({ ok: false, conflict: true, error: "The workspace changed again while saving, so this change was not saved. Reload, then make it again." });
  }

  private emit() {
    const { status, source, reason, issues, warnings, error, documentUpdatedAt } = this.base;
    const saving = this.pending.length > 0;
    const known = { documentRevision: this.revision, documentExists: this.exists };
    if (status === "ready" && source && this.confirmed) {
      // Pending updates show immediately; a failing updater is skipped here and reported by its save.
      const workspace = this.pending.reduce((current, item) => {
        try { return item.apply(current); } catch { return current; }
      }, this.confirmed);
      this.snapshot = { status, workspace, source, reason, issues, warnings, error, documentUpdatedAt, ...known, saving, canSave: source === "document" || reason === "missing" };
    } else {
      this.snapshot = { ...LOADING, ...known, saving };
    }
    for (const listener of [...this.listeners]) listener();
  }
}

/** The conflict notice after a 412 that was not (or could not be) resolved by re-applying the change. */
function conflictMessage(mode: WriteMode, fresh: Fetched): string {
  if (mode === "create") return "A workspace document was stored for this account meanwhile, so the file was not imported over it. Review it, then import again to replace it.";
  if (mode === "replace") return "The stored workspace changed since it was read, so it was not replaced. Review the latest version, then import again.";
  if (fresh.kind === "missing") return "The stored workspace was removed in another session, so this change was not saved. The sample workspace is shown.";
  if (fresh.kind !== "document") return "The workspace changed elsewhere and could not be re-read. Reload before trying again.";
  return "The workspace changed in another tab or session, so this change was not saved. The latest version is shown; make the change again.";
}

/* ---------- React ---------- */

const WorkspaceContext = createContext<WorkspaceStore | null>(null);

/**
 * Mount once per signed-in Configurations session; it loads the document on
 * mount and re-reads it when the window regains focus or the tab becomes visible.
 */
export function WorkspaceProvider({ children, store }: { children: ReactNode; store?: WorkspaceStore }) {
  const [instance] = useState(() => store ?? new WorkspaceStore());
  useEffect(() => { void instance.load(); }, [instance]);
  useEffect(() => {
    const wake = () => { if (document.visibilityState === "visible") void instance.refreshIfIdle(); };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => { window.removeEventListener("focus", wake); document.removeEventListener("visibilitychange", wake); };
  }, [instance]);
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
