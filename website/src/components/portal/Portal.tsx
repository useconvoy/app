"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PortalError, PortalSnapshot } from "@/lib/portal/types";
import { Chat } from "./Chat";
import { Device, Traces, Usage } from "./Views";
import { timestamp } from "./TelemetryChart";

export class RequestError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/portal/${path}`, { ...options, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-Convoy-Client": "web", ...options?.headers } });
  const data = await response.json().catch(() => null) as T | PortalError | null;
  if (!response.ok) {
    const error = data && typeof data === "object" && "error" in data ? (data as PortalError).error : null;
    throw new RequestError(response.status, error?.code ?? "unavailable", error?.message ?? "Convoy could not complete this request. Try again.");
  }
  if (data === null) throw new RequestError(502, "invalid_response", "Convoy returned an unreadable response. Try again.");
  return data as T;
}
export function errorText(error: unknown): string { return error instanceof Error ? error.message : "The connection could not be completed. Try again."; }
export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "success" | "warning" | "error" }) { return <span className={`portal-badge portal-badge-${tone}`}>{children}</span>; }
export function Notice({ children, error = false }: { children: React.ReactNode; error?: boolean }) { return <div className={`portal-notice${error ? " portal-notice-error" : ""}`} role={error ? "alert" : "status"}>{children}</div>; }

const views = ["device", "chat", "usage", "traces"] as const;
type View = typeof views[number];
function readView(): View { const view = new URLSearchParams(window.location.search).get("view"); return views.includes(view as View) ? view as View : "device"; }

/** Device tools share the enclosing application's account and sign-out. */
export function Portal({ onSessionEnd, active = true, canChat = true }: { onSessionEnd: () => void; active?: boolean; canChat?: boolean }) {
  const [view, setView] = useState<View>("device");
  const [snapshot, setSnapshot] = useState<PortalSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(0);
  const [monotonicNow, setMonotonicNow] = useState(0);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const loading = useRef(false);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    if (loading.current) return;
    loading.current = true; setRefreshing(true);
    try {
      const data = await request<PortalSnapshot>("snapshot");
      if (alive.current) { const received = performance.now(); setReceivedAt(received); setMonotonicNow(received); setSnapshot(data); setError(null); setNow(Date.now()); }
    } catch (cause) {
      if (alive.current) {
        if (cause instanceof RequestError && cause.status === 401) onSessionEnd();
        else setError(errorText(cause));
      }
    } finally { loading.current = false; if (alive.current) setRefreshing(false); }
  }, [onSessionEnd]);
  useEffect(() => {
    alive.current = true;
    const syncView = () => setView(readView());
    syncView(); window.addEventListener("popstate", syncView);
    return () => { alive.current = false; window.removeEventListener("popstate", syncView); };
  }, []);
  useEffect(() => { if (!active) return; const initial = window.setTimeout(() => void refresh(), 0); const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 10000); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [refresh, active]);
  useEffect(() => { const timer = window.setInterval(() => { setNow(Date.now()); setMonotonicNow(performance.now()); }, 1000); return () => window.clearInterval(timer); }, []);
  function navigate(next: View) { setView(next); window.history.pushState(null, "", `/app/applications?section=device&view=${next}`); }
  function openTrace(id: string) { setTraceId(id); navigate("traces"); }
  const stale = !!error || (receivedAt !== null && monotonicNow - receivedAt > 30000);
  // Only a nonce-bound live report establishes liveness. Generic device.status
  // can be online after history ingestion, and terminal identity states win.
  const identityStatus = snapshot && ["retired", "credential_revoked", "never_seen"].includes(snapshot.device.status) ? snapshot.device.status.replaceAll("_", " ") : null;
  const live = !!snapshot?.chat.online && !identityStatus && !stale;
  const statusLabel = identityStatus ?? (stale ? "Refresh needed" : live ? "Online" : "No recent live contact");
  return <section className="portal-shell portal-embedded" aria-label="Device connection">
      <div className="portal-topbar"><div><span className="portal-topbar-label">Configured device</span><strong>{snapshot?.device.name ?? "Device connection"}</strong>{snapshot && <Badge tone={live ? "success" : "warning"}>{statusLabel}</Badge>}</div><button className="btn btn-secondary portal-refresh" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? "Checking…" : "Test connection"}<span aria-hidden="true">↻</span></button></div>
      <p className="portal-context-note">Check live contact, inspect the running model, and send a test message. This device connection is shared across the workspace; robot deployments are configured in Applications.</p>
      <nav className="device-nav" aria-label="Device tools">{views.map(item => <a key={item} href={`/app/applications?section=device&view=${item}`} aria-current={view === item ? "page" : undefined} onClick={event => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); navigate(item); } }}>{item === "device" ? "Connection" : item === "chat" ? "Chat" : item === "usage" ? "Usage" : "Traces"}</a>)}</nav>
      <div className="portal-main">
        <div className="portal-page-heading"><div><p className="portal-eyebrow">{view === "chat" ? "On-device inference" : "Device evidence"}</p><h2>{view === "device" ? "Your device, observed." : view === "chat" ? "Talk to your model." : view === "usage" ? "Measured on the device." : "Follow the inference."}</h2></div></div>
        {error && <Notice error>{error}{snapshot ? " Showing the last successful update. Test the connection before sending another message." : " Use Test connection to reconnect."}</Notice>}
        {!snapshot ? !error && <div className="portal-loading" role="status">Reading device state and measurements…</div> : <>
          <p className="portal-updated">Updated {timestamp(snapshot.fetched_at)} · Refreshes every 10 seconds{stale ? " · Update is stale" : ""}</p>
          <div hidden={view !== "device"}><Device snapshot={snapshot} now={now} onChat={() => navigate("chat")} /></div>
          <div hidden={view !== "chat"}><Chat snapshot={snapshot} stale={stale} canChat={canChat} onRefresh={refresh} onSessionEnd={onSessionEnd} onTrace={openTrace} /></div>
          <div hidden={view !== "usage"}><Usage snapshot={snapshot} /></div>
          <div hidden={view !== "traces"}><Traces snapshot={snapshot} selectedId={traceId} onSelect={setTraceId} /></div>
        </>}
      </div>
  </section>;
}
