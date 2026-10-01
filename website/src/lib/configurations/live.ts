/**
 * Live device bindings: the only source of "measured" values in Configurations.
 *
 * A robot with `deviceId` reads the existing device endpoints:
 * - `GET /api/portal/snapshot` for the workspace's configured device
 *   (`CONFIGURED_DEVICE`, or an explicit id equal to it): telemetry series,
 *   latest sample, running release and recent gateway inference spans;
 * - `GET /api/platform/devices/{id}` for any other device (latest telemetry and
 *   identity; the series grows from samples received in this browser session),
 *   and for the configured device's hardware inventory every few minutes.
 *
 * `LiveDevicePoller` runs one shared poll for every bound robot on screen:
 * 15 s cadence, a single request cycle in flight, paused while the page is
 * hidden, exponential backoff on 429/5xx/network errors, and a stop on 401
 * (which ends the session). It is framework-free; the React provider lives in
 * src/components/configurations/LiveDeviceProvider.tsx.
 */
import { api, ApiError } from "../platform/client";
import type { PortalInference, PortalSnapshot, PortalTelemetry } from "../portal/types";
import { median, percentile } from "./format";
import { notifySessionExpired } from "./session-events";
import { CONFIGURED_DEVICE } from "./types";
import type { Provenance, Robot, SeriesPoint, TelemetryMetric, TelemetryReading } from "./types";

export const LIVE_POLL_INTERVAL_MS = 15_000;
export const LIVE_MAX_BACKOFF_MS = 120_000;
/** Data older than three missed polls is shown as stale. */
export const LIVE_STALE_AFTER_MS = 45_000;
export const LIVE_SERIES_LIMIT = 120;
const HARDWARE_REFRESH_MS = 5 * 60_000;

/* ---------- mapped shapes ---------- */

/** One gateway inference span (text inference on the device; not a robot action). */
export interface LiveInference {
  traceId: string;
  at: string | null;
  status: string | null;
  latencyMs: number | null;
  ttftMs: number | null;
  queueMs: number | null;
  tokensIn: number | null;
  tokensOut: number | null;
  tokensPerS: number | null;
}
/** Statistics of the received inference sample: median and nearest-rank p95, with n. */
export interface EdgeLatencySummary {
  n: number;
  p50Ms: number | null;
  p95Ms: number | null;
  ttftP50Ms: number | null;
  ttftP95Ms: number | null;
  tokensPerSP50: number | null;
  from: string | null;
  to: string | null;
}
export interface LiveRelease { id: string; name: string; version: string; modelRepo: string | null; modelFile: string | null; runtime: string | null; backend: string | null; contextWindow: number | null; outputLimit: number | null }
export interface LiveHardware { model: string | null; l4tRelease: string | null; cudaVersion: string | null; gpuName: string | null; computeCapability: string | null; cpuCount: number | null; memTotalMiB: number | null }
export type LiveSeries = Record<TelemetryMetric, SeriesPoint[]>;

export interface LiveDeviceData {
  deviceId: string;
  name: string;
  source: "portal-snapshot" | "platform-device";
  /** Server time of the read (snapshot `fetched_at`, or the response date). */
  fetchedAt: string;
  /** Control-plane status: online, offline, never_seen, retired, credential_revoked. */
  status: string;
  /** Live contact now: a recent live report and no terminal identity state. */
  online: boolean;
  /** "retired", "credential revoked" or "never seen" when that state overrides liveness. */
  identityState: string | null;
  liveAt: string | null;
  observedAt: string | null;
  observedHealth: string | null;
  agentVersion: string | null;
  runtimeState: string | null;
  releaseId: string | null;
  release: LiveRelease | null;
  hardware: LiveHardware | null;
  latest: TelemetryReading | null;
  series: LiveSeries;
  /** Gaps longer than this split chart lines. */
  staleAfterS: number | null;
  heartbeatIntervalS: number | null;
  /** Newest first. */
  inference: LiveInference[];
  edgeLatency: EdgeLatencySummary | null;
  chat: { eligible: boolean; online: boolean; reason: string | null } | null;
  /** `{ kind: "measured", at: latest sample time, n: samples }`. */
  provenance: Provenance;
}

/** unbound: no deviceId · loading: first read pending · fresh: read within the stale window · stale: last read older or failed (data kept) · unavailable: no data (not configured, unreachable) · unauthorized: session ended. */
export type LiveStatus = "unbound" | "loading" | "fresh" | "stale" | "unavailable" | "unauthorized";
export interface LiveBinding {
  /** Robot.deviceId (or null when unbound). */
  deviceKey: string | null;
  status: LiveStatus;
  data: LiveDeviceData | null;
  error: string | null;
  /** Monotonic receipt time (performance.now()) of the last successful read. */
  receivedAt: number | null;
}
export const UNBOUND: LiveBinding = Object.freeze({ deviceKey: null, status: "unbound", data: null, error: null, receivedAt: null });

/* ---------- mapping ---------- */

const METRICS: ReadonlyArray<[TelemetryMetric, keyof PortalTelemetry]> = [["cpuPct", "cpu_pct"], ["gpuPct", "gpu_pct"], ["memAvailableMiB", "mem_available_mb"], ["socTempC", "temp_max_c"], ["boardPowerW", "power_w"]];
const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const str = (value: unknown): string | null => typeof value === "string" && value ? value : null;
const num = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const time = (value: string | null) => (value ? Date.parse(value) : Number.NaN);
const emptySeries = (): LiveSeries => ({ cpuPct: [], gpuPct: [], memAvailableMiB: [], socTempC: [], boardPowerW: [] });

function identity(status: string): string | null {
  return ["retired", "credential_revoked", "never_seen"].includes(status) ? status.replaceAll("_", " ") : null;
}

/** Maps a device telemetry record (snapshot sample or `last_telemetry`) to a reading; missing values stay null. */
export function telemetryReading(value: unknown): TelemetryReading | null {
  if (value === null || value === undefined) return null;
  const t = record(value);
  return {
    at: str(t.ts) ?? str(t.source_ts) ?? str(t.at),
    cpuPct: num(t.cpu_pct), gpuPct: num(t.gpu_pct), memAvailableMiB: num(t.mem_available_mb), memTotalMiB: num(t.mem_total_mb),
    socTempC: num(t.temp_max_c), boardPowerW: num(t.power_w), runtimeState: str(t.runtime_state),
  };
}

export function mapInference(value: PortalInference): LiveInference {
  return { traceId: value.trace_id, at: value.start_ts, status: value.status, latencyMs: num(value.latency_ms), ttftMs: num(value.ttft_ms), queueMs: num(value.queue_ms), tokensIn: num(value.tokens_in), tokensOut: num(value.tokens_out), tokensPerS: num(value.tok_s) };
}

/** Median / nearest-rank p95 of the received spans; null when none carries a latency. */
export function summarizeLatency(inference: readonly LiveInference[]): EdgeLatencySummary | null {
  const latencies = inference.map(item => item.latencyMs).filter((value): value is number => value !== null);
  if (!latencies.length) return null;
  const times = inference.map(item => item.at).filter((at): at is string => !!at && Number.isFinite(Date.parse(at))).toSorted();
  const ttft = inference.map(item => item.ttftMs);
  return {
    n: latencies.length, p50Ms: median(latencies), p95Ms: percentile(latencies, 0.95),
    ttftP50Ms: median(ttft), ttftP95Ms: percentile(ttft, 0.95), tokensPerSP50: median(inference.map(item => item.tokensPerS)),
    from: times[0] ?? null, to: times.at(-1) ?? null,
  };
}

function measured(latest: TelemetryReading | null, fetchedAt: string, samples: number, name: string): Provenance {
  return { kind: "measured", at: latest?.at ?? fetchedAt, n: samples, source: name };
}

/** Maps the curated device snapshot (`/api/portal/snapshot`). */
export function mapSnapshot(snapshot: PortalSnapshot, hardware: LiveHardware | null = null): LiveDeviceData {
  const samples = snapshot.telemetry.filter(sample => Number.isFinite(time(sample.ts))).toSorted((a, b) => time(a.ts) - time(b.ts));
  const series = emptySeries();
  for (const [metric, key] of METRICS) series[metric] = samples.map(sample => ({ at: sample.ts as string, value: num(sample[key]) }));
  const inference = snapshot.recent_inference.map(mapInference);
  const latest = telemetryReading(snapshot.latest_telemetry);
  const identityState = identity(snapshot.device.status);
  const release = snapshot.release;
  return {
    deviceId: snapshot.device.id, name: snapshot.device.name, source: "portal-snapshot", fetchedAt: snapshot.fetched_at,
    status: snapshot.device.status, online: !!snapshot.chat.online && !identityState, identityState,
    liveAt: snapshot.device.live_at, observedAt: snapshot.device.observed_at, observedHealth: snapshot.device.observed_health,
    agentVersion: snapshot.device.agent_version, runtimeState: snapshot.device.runtime_state ?? latest?.runtimeState ?? null,
    releaseId: snapshot.device.observed_active_release_id,
    release: release ? { id: release.id, name: release.name, version: release.version, modelRepo: release.model_repo, modelFile: release.model_file, runtime: release.runtime_name, backend: release.runtime_backend, contextWindow: num(release.context_window), outputLimit: num(release.output_limit) } : null,
    hardware, latest, series, staleAfterS: num(snapshot.telemetry_stale_after_s), heartbeatIntervalS: num(snapshot.heartbeat_interval_s),
    inference, edgeLatency: summarizeLatency(inference),
    chat: { eligible: !!snapshot.chat.eligible, online: !!snapshot.chat.online, reason: snapshot.chat.reason },
    provenance: measured(latest, snapshot.fetched_at, samples.length, snapshot.device.name),
  };
}

export function mapHardware(value: unknown): LiveHardware | null {
  const h = record(value);
  if (!Object.keys(h).length) return null;
  const text = (v: unknown) => str(v) ?? (typeof v === "number" && Number.isFinite(v) ? String(v) : null);
  return { model: str(h.jetson_model) ?? str(h.gpu_name), l4tRelease: text(h.l4t_release), cudaVersion: text(h.cuda_version), gpuName: str(h.gpu_name), computeCapability: text(h.compute_capability), cpuCount: num(h.cpu_count), memTotalMiB: num(h.mem_total_mb) };
}

/** Maps `/api/platform/devices/{id}`; the series appends the latest sample to `previous` when it is new. */
export function mapPlatformDevice(value: unknown, previous: LiveDeviceData | null, fetchedAt: string): LiveDeviceData {
  const d = record(value);
  const id = str(d.id) ?? previous?.deviceId ?? "unknown";
  const name = str(d.name) ?? previous?.name ?? id;
  const status = str(d.status) ?? "unknown";
  const latest = telemetryReading(Object.keys(record(d.last_telemetry)).length ? d.last_telemetry : null);
  const series = previous ? { ...previous.series } : emptySeries();
  const last = series.socTempC.at(-1)?.at ?? null;
  if (latest?.at && Number.isFinite(time(latest.at)) && (!last || time(latest.at) > time(last))) {
    for (const [metric] of METRICS) series[metric] = [...series[metric], { at: latest.at, value: latest[metric] }].slice(-LIVE_SERIES_LIMIT);
  }
  const identityState = identity(status);
  return {
    deviceId: id, name, source: "platform-device", fetchedAt, status, online: status === "online" && !identityState, identityState,
    liveAt: str(d.live_at), observedAt: str(d.observed_at), observedHealth: str(d.observed_health), agentVersion: str(d.agent_version),
    runtimeState: latest?.runtimeState ?? null, releaseId: str(d.observed_active_release_id), release: null,
    hardware: mapHardware(d.hardware) ?? previous?.hardware ?? null, latest, series, staleAfterS: previous?.staleAfterS ?? null, heartbeatIntervalS: previous?.heartbeatIntervalS ?? null,
    inference: [], edgeLatency: null, chat: null,
    provenance: measured(latest, fetchedAt, series.socTempC.length, name),
  };
}

/* ---------- transport ---------- */

export class LiveRequestError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export interface LiveTransport {
  snapshot(): Promise<PortalSnapshot>;
  device(id: string): Promise<{ body: unknown; date: string | null }>;
}

/** Same request shape as the device view's `request()` (Portal.tsx) and the platform `api()` helper. */
export const browserTransport: LiveTransport = {
  async snapshot() {
    const response = await fetch("/api/portal/snapshot", { credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-Convoy-Client": "web" } });
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok || data === null) {
      const error = record(record(data).error);
      throw new LiveRequestError(response.status || 502, str(error.code) ?? "unavailable", str(error.message) ?? "Convoy could not read the device. Try again.");
    }
    return data as PortalSnapshot;
  },
  async device(id) {
    const body = await api<unknown>(`devices/${encodeURIComponent(id)}`);
    return { body, date: null };
  },
};

function failure(error: unknown): { status: number; code: string; message: string } {
  if (error instanceof LiveRequestError) return { status: error.status, code: error.code, message: error.message };
  if (error instanceof ApiError) return { status: error.status, code: "platform", message: error.message };
  return { status: 0, code: "network", message: "The device could not be reached. Retrying." };
}

/* ---------- poller ---------- */

export interface LiveEntry { status: Exclude<LiveStatus, "unbound">; data: LiveDeviceData | null; error: string | null; receivedAt: number | null }
export interface LiveState {
  /** Keyed by device key: `CONFIGURED_DEVICE` or an explicit device id. */
  devices: Readonly<Record<string, LiveEntry>>;
  /** Id behind `CONFIGURED_DEVICE`: undefined until the first snapshot, null when no device is configured. */
  configuredDeviceId: string | null | undefined;
  polling: boolean;
  failures: number;
  /** Delay before the next poll (grows with backoff). */
  nextDelayMs: number;
  /** Monotonic time of the last completed poll. */
  lastPollAt: number | null;
}
export interface LivePollerOptions {
  transport?: LiveTransport;
  intervalMs?: number;
  maxBackoffMs?: number;
  staleAfterMs?: number;
  /** Monotonic clock in ms (default performance.now). */
  clock?: () => number;
  /** Wall clock in ms (default Date.now), for `fetchedAt` when the server gives no time. */
  wallClock?: () => number;
  timers?: { set(callback: () => void, ms: number): unknown; clear(handle: unknown): void };
  visibility?: { visible(): boolean; subscribe(listener: () => void): () => void };
}

const defaultVisibility = {
  visible: () => typeof document === "undefined" || document.visibilityState === "visible",
  subscribe: (listener: () => void) => {
    if (typeof document === "undefined") return () => undefined;
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  },
};

/** One shared, rate-friendly poll of every retained device key. See the module comment. */
export class LiveDevicePoller {
  private transport: LiveTransport;
  private interval: number;
  private maxBackoff: number;
  private staleAfter: number;
  private clock: () => number;
  private wallClock: () => number;
  private timers: NonNullable<LivePollerOptions["timers"]>;
  private visibility: NonNullable<LivePollerOptions["visibility"]>;
  private refs = new Map<string, number>();
  private listeners = new Set<() => void>();
  private entries = new Map<string, LiveEntry>();
  private hardware = new Map<string, { value: LiveHardware | null; at: number }>();
  private configured: string | null | undefined = undefined;
  private failures = 0;
  private lastPollAt: number | null = null;
  private timer: unknown = null;
  private inFlight = false;
  /** A key was added during a poll: poll again right after it. */
  private rerun = false;
  private started = false;
  private unauthorized = false;
  private unsubscribeVisibility: (() => void) | null = null;
  private state: LiveState;

  constructor(options: LivePollerOptions = {}) {
    this.transport = options.transport ?? browserTransport;
    this.interval = options.intervalMs ?? LIVE_POLL_INTERVAL_MS;
    this.maxBackoff = options.maxBackoffMs ?? LIVE_MAX_BACKOFF_MS;
    this.staleAfter = options.staleAfterMs ?? LIVE_STALE_AFTER_MS;
    this.clock = options.clock ?? (() => (typeof performance === "undefined" ? Date.now() : performance.now()));
    this.wallClock = options.wallClock ?? (() => Date.now());
    this.timers = options.timers ?? { set: (callback, ms) => setTimeout(callback, ms), clear: handle => clearTimeout(handle as ReturnType<typeof setTimeout>) };
    this.visibility = options.visibility ?? defaultVisibility;
    this.state = this.build();
  }

  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = (): LiveState => this.state;

  /** Keeps these device keys polled until the returned release is called. */
  retain = (keys: readonly string[]): (() => void) => {
    let added = false;
    for (const key of new Set(keys)) {
      if (!key) continue;
      const count = this.refs.get(key) ?? 0;
      this.refs.set(key, count + 1);
      if (!count) { added = true; if (!this.entries.has(key)) this.entries.set(key, { status: "loading", data: null, error: null, receivedAt: null }); }
    }
    if (added) { this.unauthorized = false; this.emit(); if (this.inFlight) this.rerun = true; else this.schedule(0); }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      for (const key of new Set(keys)) {
        const count = this.refs.get(key) ?? 0;
        if (count <= 1) this.refs.delete(key); else this.refs.set(key, count - 1);
      }
      if (!this.refs.size) this.cancel();
    };
  };

  start = (): void => {
    if (this.started) return;
    this.started = true;
    this.unsubscribeVisibility = this.visibility.subscribe(this.onVisibility);
    if (this.refs.size) this.schedule(0);
  };
  stop = (): void => {
    this.started = false;
    this.cancel();
    this.unsubscribeVisibility?.();
    this.unsubscribeVisibility = null;
  };
  /** Polls now (e.g. a "Check again" action); ignored while a poll is in flight. */
  refresh = (): Promise<void> => { this.unauthorized = false; return this.poll(); };

  private onVisibility = () => {
    if (!this.started || !this.visibility.visible() || !this.refs.size || this.inFlight) return;
    const since = this.lastPollAt === null ? Infinity : this.clock() - this.lastPollAt;
    this.schedule(Math.max(0, this.delay() - since));
  };
  private delay() { return this.failures ? Math.min(this.interval * 2 ** this.failures, this.maxBackoff) : this.interval; }
  private cancel() { if (this.timer !== null) this.timers.clear(this.timer); this.timer = null; }
  private schedule(ms: number) {
    this.cancel();
    if (!this.started || !this.refs.size || this.unauthorized) return;
    this.timer = this.timers.set(() => { this.timer = null; void this.poll(); }, ms);
  }

  private async poll(): Promise<void> {
    if (this.inFlight || !this.refs.size) return;
    if (!this.visibility.visible()) return; // resumes on visibilitychange
    this.inFlight = true;
    this.cancel();
    this.emit();
    const keys = [...this.refs.keys()];
    const explicit = keys.filter(key => key !== CONFIGURED_DEVICE && key !== this.configured);
    const wantSnapshot = keys.includes(CONFIGURED_DEVICE) || this.configured === undefined || (this.configured !== null && keys.includes(this.configured));
    let backoff = false, ended = false;
    const put = (key: string, entry: Partial<LiveEntry>) => {
      const current = this.entries.get(key) ?? { status: "loading" as const, data: null, error: null, receivedAt: null };
      this.entries.set(key, { ...current, ...entry });
    };
    const fail = (key: string, problem: ReturnType<typeof failure>) => {
      if (problem.status === 401) { ended = true; return; }
      if (problem.status === 0 || problem.status === 429 || problem.status >= 500) backoff = true;
      const current = this.entries.get(key);
      put(key, { status: current?.data ? "stale" : "unavailable", error: problem.message });
    };
    try {
      const [snapshotResult, ...deviceResults] = await Promise.allSettled([
        wantSnapshot ? this.transport.snapshot() : Promise.resolve(null),
        ...explicit.map(id => this.transport.device(id)),
      ]);
      const now = this.clock();
      if (snapshotResult.status === "fulfilled" && snapshotResult.value) {
        const snapshot = snapshotResult.value;
        this.configured = snapshot.device.id;
        const data = mapSnapshot(snapshot, this.hardware.get(snapshot.device.id)?.value ?? null);
        for (const key of [CONFIGURED_DEVICE, snapshot.device.id]) if (this.refs.has(key)) put(key, { status: "fresh", data, error: null, receivedAt: now });
      } else if (snapshotResult.status === "rejected") {
        const problem = failure(snapshotResult.reason);
        if (problem.status === 503 && problem.code === "unavailable") this.configured = null;
        if (this.refs.has(CONFIGURED_DEVICE)) fail(CONFIGURED_DEVICE, problem);
        else if (problem.status === 401) ended = true;
        else if (problem.status === 0 || problem.status === 429 || problem.status >= 500) backoff = true;
      }
      explicit.forEach((id, i) => {
        const result = deviceResults[i];
        if (result.status === "rejected") return fail(id, failure(result.reason));
        if (id === this.configured && this.entries.get(id)?.data?.source === "portal-snapshot") {
          // The snapshot already covered this id; keep its richer data and take the hardware inventory.
          const hardware = mapHardware(record(result.value.body).hardware);
          this.hardware.set(id, { value: hardware, at: now });
          for (const key of [CONFIGURED_DEVICE, id]) {
            const entry = this.entries.get(key);
            if (entry?.data?.source === "portal-snapshot") put(key, { data: { ...entry.data, hardware } });
          }
          return;
        }
        const fetchedAt = result.value.date && Number.isFinite(Date.parse(result.value.date)) ? new Date(result.value.date).toISOString() : new Date(this.wallClock()).toISOString();
        put(id, { status: "fresh", data: mapPlatformDevice(result.value.body, this.entries.get(id)?.data ?? null, fetchedAt), error: null, receivedAt: now });
      });
      // Hardware inventory for the configured device comes from the platform device record, refreshed every few minutes.
      const configured = this.configured;
      if (!ended && configured && (this.refs.has(CONFIGURED_DEVICE) || this.refs.has(configured))) {
        const known = this.hardware.get(configured);
        if (!known || now - known.at > HARDWARE_REFRESH_MS) {
          try {
            const result = await this.transport.device(configured);
            const hardware = mapHardware(record(result.body).hardware);
            this.hardware.set(configured, { value: hardware, at: now });
            for (const key of [CONFIGURED_DEVICE, configured]) {
              const entry = this.entries.get(key);
              if (entry?.data?.source === "portal-snapshot") put(key, { data: { ...entry.data, hardware } });
            }
          } catch (error) {
            const problem = failure(error);
            if (problem.status === 401) ended = true;
            this.hardware.set(configured, { value: known?.value ?? null, at: now });
          }
        }
      }
    } finally {
      this.inFlight = false;
      this.lastPollAt = this.clock();
    }
    if (ended) {
      this.unauthorized = true;
      this.rerun = false;
      for (const key of this.refs.keys()) {
        const current = this.entries.get(key);
        this.entries.set(key, { status: "unauthorized", data: current?.data ?? null, error: "Your session ended. Sign in to continue.", receivedAt: current?.receivedAt ?? null });
      }
      this.emit();
      notifySessionExpired();
      return;
    }
    this.failures = backoff ? this.failures + 1 : 0;
    this.emit();
    this.schedule(this.rerun ? 0 : this.delay());
    this.rerun = false;
  }

  private emit() { this.state = this.build(); for (const listener of [...this.listeners]) listener(); }
  private build(): LiveState {
    const now = this.clock();
    const devices: Record<string, LiveEntry> = {};
    for (const [key, entry] of this.entries) {
      const old = entry.receivedAt !== null && now - entry.receivedAt > this.staleAfter;
      devices[key] = entry.status === "fresh" && old ? { ...entry, status: "stale" } : entry;
    }
    return { devices, configuredDeviceId: this.configured, polling: this.inFlight, failures: this.failures, nextDelayMs: this.delay(), lastPollAt: this.lastPollAt };
  }
}

/** The binding for one robot in a poller state (unbound robots get `UNBOUND`). */
export function bindingFor(robot: Pick<Robot, "deviceId"> | null | undefined, state: LiveState | null): LiveBinding {
  const key = robot?.deviceId;
  if (!key) return UNBOUND;
  const entry = state?.devices[key];
  if (!entry) return { deviceKey: key, status: "loading", data: null, error: null, receivedAt: null };
  return { deviceKey: key, status: entry.status, data: entry.data, error: entry.error, receivedAt: entry.receivedAt };
}
