import test from "node:test";
import assert from "node:assert/strict";
import { bindingFor, LiveDevicePoller, LiveRequestError, mapPlatformDevice, mapSnapshot, summarizeLatency } from "../../src/lib/configurations/live";
import type { LiveTransport } from "../../src/lib/configurations/live";
import { onSessionExpired } from "../../src/lib/configurations/session-events";
import { CONFIGURED_DEVICE } from "../../src/lib/configurations/types";
import type { PortalSnapshot, PortalTelemetry } from "../../src/lib/portal/types";

const T0 = Date.parse("2026-10-01T09:41:20Z");
const iso = (offsetS: number) => new Date(T0 + offsetS * 1000).toISOString();
function sample(offsetS: number, temp: number | null = 46): PortalTelemetry {
  return { ts: iso(offsetS), cpu_pct: 27, gpu_pct: null, mem_total_mb: 7620, mem_available_mb: 3533, power_w: 8.1, temp_max_c: temp, disk_free_mb: 100, runtime_state: "running", clock_confidence: "unknown" };
}
function snapshot(overrides: Partial<PortalSnapshot> = {}): PortalSnapshot {
  return {
    fetched_at: iso(0), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_lab", name: "Lab Jetson", status: "online", live_at: iso(-3), observed_at: iso(-3), observed_health: "ok", observed_stage: "ready", agent_version: "0.9.3", observed_active_release_id: "rel_1", gateway_mode: "production", runtime_state: "running" },
    release: { id: "rel_1", name: "Qwen", version: "1", digest: "d", model_repo: "repo", model_file: "model.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry: [sample(0), sample(-30, null), sample(-15)], latest_telemetry: sample(0),
    usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [
      { trace_id: "tr_c", start_ts: iso(-1), status: "ok", latency_ms: 650, ttft_ms: 80, queue_ms: 0, tokens_in: 120, tokens_out: 30, tok_s: 59 },
      { trace_id: "tr_b", start_ts: iso(-20), status: "ok", latency_ms: 210, ttft_ms: 60, queue_ms: 0, tokens_in: 110, tokens_out: 8, tok_s: 57 },
      { trace_id: "tr_a", start_ts: iso(-40), status: "ok", latency_ms: 180, ttft_ms: null, queue_ms: 0, tokens_in: 110, tokens_out: 7, tok_s: null },
    ],
    chat: { eligible: true, online: true, reason: null, release_id: "rel_1", max_tokens: 128, context_window: 2048 },
    ...overrides,
  };
}

test("snapshot mapping keeps missing values null, sorts series and summarizes the latency sample", () => {
  const data = mapSnapshot(snapshot());
  assert.equal(data.source, "portal-snapshot");
  assert.equal(data.online, true);
  assert.deepEqual(data.series.socTempC.map(point => point.value), [null, 46, 46]);
  assert.deepEqual(data.series.gpuPct.map(point => point.value), [null, null, null]);
  assert.equal(data.latest?.socTempC, 46);
  assert.equal(data.latest?.gpuPct, null);
  assert.deepEqual(data.provenance, { kind: "measured", at: iso(0), n: 3, source: "Lab Jetson" });
  assert.deepEqual({ n: data.edgeLatency?.n, p50: data.edgeLatency?.p50Ms, p95: data.edgeLatency?.p95Ms, ttft: data.edgeLatency?.ttftP50Ms }, { n: 3, p50: 210, p95: 650, ttft: 70 });
  assert.equal(data.edgeLatency?.from, iso(-40));
  assert.equal(data.release?.modelFile, "model.gguf");
  assert.equal(summarizeLatency([]), null);
  const retired = mapSnapshot(snapshot({ device: { ...snapshot().device, status: "credential_revoked" } }));
  assert.equal(retired.identityState, "credential revoked");
  assert.equal(retired.online, false, "a terminal identity state overrides a live report");
});

test("platform device mapping grows the series from new samples only", () => {
  const body = (at: string, temp: number) => ({ id: "dev_other", name: "Other Jetson", status: "online", live_at: at, last_telemetry: { at, temp_max_c: temp, cpu_pct: 10, power_w: 5, mem_available_mb: 4000, mem_total_mb: 7620 }, hardware: { jetson_model: "Orin Nano", l4t_release: "36.4.7", cuda_version: 12.6 } });
  const first = mapPlatformDevice(body(iso(-15), 50), null, iso(0));
  const same = mapPlatformDevice(body(iso(-15), 50), first, iso(15));
  const next = mapPlatformDevice(body(iso(0), 51), same, iso(30));
  assert.equal(first.source, "platform-device");
  assert.deepEqual(next.series.socTempC.map(point => point.value), [50, 51]);
  assert.equal(next.hardware?.cudaVersion, "12.6");
  assert.equal(next.edgeLatency, null, "no inference spans from this endpoint");
  const empty = mapPlatformDevice({ id: "dev_quiet", status: "never_seen", last_telemetry: {} }, null, iso(0));
  assert.equal(empty.latest, null);
  assert.equal(empty.identityState, "never seen");
});

/* ---------- poller ---------- */

class Clock {
  now = 0;
  private timers: Array<{ id: number; at: number; callback: () => void }> = [];
  private nextId = 1;
  set = (callback: () => void, ms: number) => { const id = this.nextId++; this.timers.push({ id, at: this.now + ms, callback }); return id; };
  clear = (handle: unknown) => { this.timers = this.timers.filter(timer => timer.id !== handle); };
  pending() { return this.timers.map(timer => timer.at - this.now); }
  async advance(ms: number) {
    const end = this.now + ms;
    for (;;) {
      const due = this.timers.filter(timer => timer.at <= end).toSorted((a, b) => a.at - b.at)[0];
      if (!due) break;
      this.timers = this.timers.filter(timer => timer !== due);
      this.now = due.at;
      due.callback();
      await flush();
    }
    this.now = end;
    await flush();
  }
}
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve)); };

function setup(responses: { snapshot?: () => Promise<PortalSnapshot>; device?: (id: string) => Promise<{ body: unknown; date: string | null }> } = {}) {
  const clock = new Clock();
  const calls = { snapshot: 0, device: [] as string[] };
  let visible = true;
  const listeners = new Set<() => void>();
  const transport: LiveTransport = {
    snapshot: () => { calls.snapshot++; return responses.snapshot ? responses.snapshot() : Promise.resolve(snapshot()); },
    device: id => { calls.device.push(id); return responses.device ? responses.device(id) : Promise.resolve({ body: { id, name: id, status: "online", last_telemetry: { at: iso(clock.now / 1000), temp_max_c: 40 }, hardware: { jetson_model: "Orin Nano", l4t_release: "36.4.7" } }, date: null }); },
  };
  const poller = new LiveDevicePoller({
    transport, clock: () => clock.now, wallClock: () => T0 + clock.now, timers: { set: clock.set, clear: clock.clear },
    visibility: { visible: () => visible, subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); } },
  });
  poller.start();
  return { poller, clock, calls, setVisible(value: boolean) { visible = value; for (const listener of listeners) listener(); } };
}

test("a retained configured device is polled at once, then every 15 s, one request cycle at a time", async () => {
  const { poller, clock, calls } = setup();
  const release = poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  assert.equal(calls.snapshot, 1);
  assert.equal(poller.getSnapshot().configuredDeviceId, "dev_lab");
  const entry = poller.getSnapshot().devices[CONFIGURED_DEVICE];
  assert.equal(entry.status, "fresh");
  assert.equal(entry.data?.hardware?.l4tRelease, "36.4.7", "hardware inventory comes from the device record");
  assert.deepEqual(calls.device, ["dev_lab"]);
  assert.equal(bindingFor({ deviceId: CONFIGURED_DEVICE }, poller.getSnapshot()).data?.name, "Lab Jetson");
  await clock.advance(14_999);
  assert.equal(calls.snapshot, 1);
  await clock.advance(1);
  assert.equal(calls.snapshot, 2);
  assert.deepEqual(calls.device, ["dev_lab"], "hardware is refreshed every few minutes, not every poll");
  release();
  await clock.advance(60_000);
  assert.equal(calls.snapshot, 2, "no polling without a retained device");
});

test("only one poll is in flight", async () => {
  let resolve: (value: PortalSnapshot) => void = () => undefined;
  const { poller, clock, calls } = setup({ snapshot: () => new Promise(done => { resolve = done; }) });
  poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  void poller.refresh();
  void poller.refresh();
  await clock.advance(60_000);
  assert.equal(calls.snapshot, 1);
  assert.equal(poller.getSnapshot().polling, true);
  resolve(snapshot());
  await flush();
  assert.equal(poller.getSnapshot().polling, false);
  assert.deepEqual(clock.pending(), [15_000]);
});

test("a device added during a poll is read right after it", async () => {
  let resolve: (value: PortalSnapshot) => void = () => undefined;
  const { poller, clock, calls } = setup({ snapshot: () => new Promise(done => { resolve = done; }) });
  poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  poller.retain(["dev_other"]);
  resolve(snapshot());
  await flush();
  assert.deepEqual(clock.pending(), [0]);
  resolve = () => undefined;
  await clock.advance(0);
  assert.ok(calls.device.includes("dev_other"));
});

test("429, 5xx and network errors back off exponentially to 120 s and keep the last data as stale", async () => {
  let failing: number | null = null;
  const { poller, clock, calls } = setup({ snapshot: () => failing === null ? Promise.resolve(snapshot()) : Promise.reject(failing === 0 ? new TypeError("offline") : new LiveRequestError(failing, "busy", "Busy")) });
  poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  failing = 429;
  await clock.advance(15_000);
  assert.equal(poller.getSnapshot().devices[CONFIGURED_DEVICE].status, "stale");
  assert.equal(poller.getSnapshot().devices[CONFIGURED_DEVICE].data?.deviceId, "dev_lab");
  assert.deepEqual(clock.pending(), [30_000]);
  failing = 503;
  await clock.advance(30_000);
  assert.deepEqual(clock.pending(), [60_000]);
  failing = 0;
  await clock.advance(60_000);
  assert.deepEqual(clock.pending(), [120_000]);
  await clock.advance(120_000);
  assert.deepEqual(clock.pending(), [120_000], "capped");
  failing = null;
  await clock.advance(120_000);
  assert.equal(poller.getSnapshot().devices[CONFIGURED_DEVICE].status, "fresh");
  assert.deepEqual(clock.pending(), [15_000]);
  assert.equal(calls.snapshot, 6);
});

test("polling pauses while the page is hidden and resumes when it is visible again", async () => {
  const { poller, clock, calls, setVisible } = setup();
  poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  setVisible(false);
  await clock.advance(60_000);
  assert.equal(calls.snapshot, 1);
  setVisible(true);
  await clock.advance(0);
  assert.equal(calls.snapshot, 2, "an overdue poll runs as soon as the page is visible");
});

test("401 stops polling, marks entries unauthorized and ends the session; a new subscriber resumes", async () => {
  let unauthorized = true;
  const { poller, clock, calls } = setup({ snapshot: () => unauthorized ? Promise.reject(new LiveRequestError(401, "authentication_required", "Sign in")) : Promise.resolve(snapshot()) });
  let ended = 0;
  const stop = onSessionExpired(() => { ended++; });
  const release = poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  assert.equal(ended, 1);
  assert.equal(poller.getSnapshot().devices[CONFIGURED_DEVICE].status, "unauthorized");
  await clock.advance(120_000);
  assert.equal(calls.snapshot, 1);
  release();
  unauthorized = false;
  poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  assert.equal(poller.getSnapshot().devices[CONFIGURED_DEVICE].status, "fresh");
  stop();
});

test("explicit device ids use the device record unless they are the configured device", async () => {
  const { poller, clock, calls } = setup();
  poller.retain(["dev_other"]);
  await clock.advance(0);
  assert.equal(calls.snapshot, 1, "the first poll learns which device is configured");
  assert.deepEqual(calls.device, ["dev_other"], "no hardware read for a configured device nobody shows");
  assert.equal(poller.getSnapshot().devices.dev_other.data?.source, "platform-device");
  await clock.advance(15_000);
  assert.equal(calls.snapshot, 1, "no snapshot for a device that is not the configured one");
  assert.equal(poller.getSnapshot().devices.dev_other.data?.series.socTempC.length, 2);
  poller.retain(["dev_lab"]);
  await clock.advance(0);
  assert.equal(poller.getSnapshot().devices.dev_lab.data?.source, "portal-snapshot");
  assert.equal(poller.getSnapshot().devices.dev_lab.data?.hardware?.model, "Orin Nano");
});

test("an unconfigured device connection is unavailable, not an error loop", async () => {
  const { poller, clock } = setup({ snapshot: () => Promise.reject(new LiveRequestError(503, "unavailable", "No device connection is configured for this workspace.")) });
  poller.retain([CONFIGURED_DEVICE]);
  await clock.advance(0);
  const entry = poller.getSnapshot().devices[CONFIGURED_DEVICE];
  assert.equal(entry.status, "unavailable");
  assert.equal(entry.error, "No device connection is configured for this workspace.");
  assert.equal(poller.getSnapshot().configuredDeviceId, null);
  assert.deepEqual(clock.pending(), [30_000]);
});
