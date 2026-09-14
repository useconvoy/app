export type NullableNumber = number | null;
export interface PortalTelemetry {
  ts: string | null;
  cpu_pct: NullableNumber;
  gpu_pct: NullableNumber;
  mem_total_mb: NullableNumber;
  mem_available_mb: NullableNumber;
  power_w: NullableNumber;
  temp_max_c: NullableNumber;
  disk_free_mb: NullableNumber;
  runtime_state: string | null;
  clock_confidence: string | null;
}
export interface PortalInference {
  trace_id: string;
  start_ts: string | null;
  status: string | null;
  latency_ms: NullableNumber;
  ttft_ms: NullableNumber;
  queue_ms: NullableNumber;
  tokens_in: NullableNumber;
  tokens_out: NullableNumber;
  tok_s: NullableNumber;
}
export interface PortalSnapshot {
  fetched_at: string;
  device: {
    id: string; name: string; status: string;
    live_at: string | null; observed_at: string | null;
    observed_health: string | null; observed_stage: string | null;
    agent_version: string | null; observed_active_release_id: string | null;
    gateway_mode: string | null; runtime_state: string | null;
  };
  release: null | {
    id: string; name: string; version: string; digest: string;
    model_repo: string | null; model_file: string | null;
    runtime_name: string | null; runtime_backend: string | null;
    context_window: NullableNumber; output_limit: NullableNumber;
  };
  telemetry: PortalTelemetry[];
  latest_telemetry: PortalTelemetry | null;
  telemetry_stale_after_s: number | null;
  heartbeat_interval_s: number | null;
  usage: {
    from: string; to: string;
    metrics: Record<string, NullableNumber> | null;
  };
  recent_inference: PortalInference[];
  chat: {
    eligible: boolean; online: boolean; reason: string | null;
    release_id: string | null; max_tokens: number; context_window: NullableNumber;
  };
}
export interface PortalChatInput {
  request_id: string;
  expected_release_id: string;
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  max_tokens: number;
}
export interface PortalChatRequest {
  id: string;
  device_id: string;
  release_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "expired";
  created_at: string | null;
  expires_at: string | null;
  content: string | null;
  finish_reason: "stop" | "length" | null;
  usage: null | { prompt_tokens: number | null; completion_tokens: number | null; total_tokens: number | null };
  metrics: null | { latency_ms: number | null; ttft_ms: number | null; queue_ms: number | null };
  trace_id: string | null;
  error: null | { code: string; message: string };
}
export interface PortalSession { authenticated: boolean; expires_at?: string }
export interface PortalError { error: { code: string; message: string } }
