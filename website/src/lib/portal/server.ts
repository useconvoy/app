import { PortalFailure, UUID } from "./auth";
import { assertDevice, list, num, record, str, upstream, upstreamConfig } from "./upstream";
import type { PortalChatInput, PortalChatRequest, PortalSnapshot, PortalTelemetry } from "./types";

const metricKeys = ["inference_requests", "tokens_in", "tokens_out", "inference_minutes", "runtime_up_minutes", "agent_up_minutes", "contact_minutes", "unknown_coverage_s", "eval_results_received"];
const telemetryKeys = ["cpu_pct", "gpu_pct", "mem_total_mb", "mem_available_mb", "power_w", "temp_max_c", "disk_free_mb"] as const;
export function curateTelemetry(value: unknown): PortalTelemetry {
  const t = record(value);
  return {
    ts: str(t.ts) ?? str(t.source_ts) ?? str(t.at),
    ...Object.fromEntries(telemetryKeys.map(k => [k, num(t[k])])) as Pick<PortalTelemetry, typeof telemetryKeys[number]>,
    runtime_state: str(t.runtime_state), clock_confidence: str(t.clock_confidence),
  };
}
export function curateUsage(value: unknown, deviceId: string) {
  const selected = list(record(value).devices).map(record).find(d => d.device_id === deviceId && d.simulated === false);
  return selected ? Object.fromEntries(metricKeys.map(k => [k, num(record(selected.metrics)[k])])) : null;
}
export async function snapshot(): Promise<PortalSnapshot> {
  const { deviceId } = upstreamConfig();
  const d = assertDevice(await upstream(`/api/v1/devices/${deviceId}`), deviceId);
  const releaseId = str(d.observed_active_release_id);
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
  const [availability, samples, spans, usage, releaseValue, settingsValue] = await Promise.all([
    upstream("/api/v1/chat/devices"),
    upstream(`/api/v1/devices/${deviceId}/telemetry?limit=120`),
    upstream(`/api/v1/devices/${deviceId}/spans?limit=100`),
    upstream(`/api/v1/usage?from=${from}&to=${to}`),
    releaseId ? upstream(`/api/v1/releases/${encodeURIComponent(releaseId)}`) : Promise.resolve(null),
    upstream("/api/v1/settings"),
  ]);
  const settings = record(settingsValue);
  const chat = list(record(availability).devices).map(record).find(v => v.id === deviceId);
  if (!chat || chat.simulated !== false) throw new PortalFailure(503, "device_unavailable", "The configured device is unavailable.");
  const r = record(releaseValue);
  if (releaseId && (r.id !== releaseId || r.simulated !== false)) throw new PortalFailure(503, "device_unavailable", "The active model is unavailable.");
  const telemetry = list(samples).map(curateTelemetry);
  const latest = telemetry.at(-1) ?? (d.last_telemetry ? curateTelemetry(d.last_telemetry) : null);
  const model = record(r.model), runtime = record(r.runtime), config = record(r.config);
  const observed = record(d.observed);
  return {
    fetched_at: new Date().toISOString(),
    device: {
      id: deviceId, name: str(d.name) ?? deviceId, status: str(d.status) ?? "unknown",
      live_at: str(d.live_at), observed_at: str(d.observed_at), observed_health: str(d.observed_health),
      observed_stage: str(d.observed_stage), agent_version: str(d.agent_version), observed_active_release_id: releaseId,
      gateway_mode: str(record(observed.gateway).mode), runtime_state: latest?.runtime_state ?? null,
    },
    release: releaseId ? {
      id: releaseId, name: str(r.name) ?? releaseId, version: str(r.version) ?? "", digest: str(r.digest) ?? "",
      model_repo: str(model.repo), model_file: str(record(model.file).path), runtime_name: str(runtime.name),
      runtime_backend: str(runtime.backend), context_window: num(config.ctx_size), output_limit: num(config.n_predict),
    } : null,
    telemetry, latest_telemetry: latest,
    telemetry_stale_after_s: num(settings.offline_after_s), heartbeat_interval_s: num(settings.heartbeat_interval_s),
    usage: { from, to, metrics: curateUsage(usage, deviceId) },
    recent_inference: list(spans).map(record).filter(s => s.name === "gateway.chat_completion" && s.kind === "inference" && typeof s.trace_id === "string").map(s => {
      const a = record(s.attrs);
      return { trace_id: s.trace_id as string, start_ts: str(s.start_ts), status: str(s.status), latency_ms: num(a.latency_ms), ttft_ms: num(a.ttft_ms), queue_ms: num(a.queue_ms), tokens_in: num(a.tokens_in), tokens_out: num(a.tokens_out), tok_s: num(a.tok_s) };
    }).reverse().slice(0, 20),
    chat: {
      eligible: chat.eligible === true && chat.release_id === releaseId,
      online: chat.online === true,
      reason: chat.eligible === true && chat.release_id === releaseId ? null : "The device is not ready for chat. Refresh its status before sending.",
      release_id: str(chat.release_id), max_tokens: Math.min(128, Math.max(1, num(chat.max_tokens) ?? 128)), context_window: num(chat.context_window),
    },
  };
}
export function chatInput(value: unknown): PortalChatInput {
  const v = record(value);
  const bad = () => { throw new PortalFailure(422, "invalid_request", "Use up to 16 messages, 8 KiB of text, and 1–128 output tokens."); };
  if (Object.keys(v).some(k => !["request_id", "expected_release_id", "messages", "max_tokens"].includes(k))) return bad();
  if (typeof v.request_id !== "string" || !UUID.test(v.request_id) || typeof v.expected_release_id !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(v.expected_release_id)) return bad();
  if (!Number.isInteger(v.max_tokens) || Number(v.max_tokens) < 1 || Number(v.max_tokens) > 128) return bad();
  if (!Array.isArray(v.messages) || v.messages.length < 1 || v.messages.length > 16) return bad();
  let bytes = 0;
  const messages: PortalChatInput["messages"] = v.messages.map(value => {
    const m = record(value);
    if (Object.keys(m).some(k => !["role", "content"].includes(k)) || (m.role !== "user" && m.role !== "assistant") || typeof m.content !== "string" || !m.content.length) return bad();
    bytes += Buffer.byteLength(m.content, "utf8");
    return { role: m.role as "user" | "assistant", content: m.content };
  });
  if (bytes > 8192 || messages.at(-1)?.role !== "user") return bad();
  return { request_id: v.request_id.toLowerCase(), expected_release_id: v.expected_release_id, messages, max_tokens: v.max_tokens as number };
}
const resultErrors: Record<string, string> = {
  request_expired: "The request expired. It will not be run again automatically.",
  device_changed: "The active device or model changed during this request.",
  context_length_exceeded: "The conversation exceeds the model context. Start a new conversation.",
  timeout: "The model did not finish within its deadline.",
};
export function curateChat(value: unknown, clientId: string, upstreamId: string, deviceId: string): PortalChatRequest {
  const v = record(value);
  if (v.id !== upstreamId || v.device_id !== deviceId || typeof v.release_id !== "string" || !["queued", "running", "succeeded", "failed", "expired"].includes(String(v.status))) {
    throw new PortalFailure(503, "upstream_unavailable", "The device response could not be verified.");
  }
  const usage = record(v.usage), metrics = record(v.metrics), error = record(v.error);
  const errorCode = str(error.code);
  return {
    id: clientId.toLowerCase(), device_id: deviceId, release_id: v.release_id,
    status: v.status as PortalChatRequest["status"], created_at: str(v.created_at), expires_at: str(v.expires_at),
    content: typeof v.content === "string" ? v.content.slice(0, 16384) : null,
    finish_reason: v.finish_reason === "stop" || v.finish_reason === "length" ? v.finish_reason : null,
    usage: v.usage ? { prompt_tokens: num(usage.prompt_tokens), completion_tokens: num(usage.completion_tokens), total_tokens: num(usage.total_tokens) } : null,
    metrics: v.metrics ? { latency_ms: num(metrics.latency_ms), ttft_ms: num(metrics.ttft_ms), queue_ms: num(metrics.queue_ms) } : null,
    trace_id: typeof v.trace_id === "string" && /^tr_[a-f0-9]+$/.test(v.trace_id) ? v.trace_id : null,
    error: errorCode ? { code: resultErrors[errorCode] ? errorCode : "request_failed", message: resultErrors[errorCode] ?? "The device could not complete this request." } : null,
  };
}
