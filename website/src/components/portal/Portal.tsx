"use client";

import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { BRAND } from "@/content/homepage";
import type { PortalError, PortalSession, PortalSnapshot } from "@/lib/portal/types";
import { Chat } from "./Chat";
import { Device, Traces, Usage } from "./Views";
import { timestamp } from "./TelemetryChart";

export class RequestError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/portal/${path}`, { ...options, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", ...options?.headers } });
  const data = await response.json().catch(() => null) as T | PortalError | null;
  if (!response.ok) {
    const error = data && typeof data === "object" && "error" in data ? (data as PortalError).error : null;
    throw new RequestError(response.status, error?.code ?? "unavailable", error?.message ?? "Convoy could not complete this request. Try again.");
  }
  if (data === null) throw new RequestError(502, "invalid_response", "Convoy returned an unreadable response. Try again.");
  return data as T;
}
export function errorText(error: unknown): string { return error instanceof Error ? error.message : "The connection could not be completed. Try again."; }
export function Mark() { return <Link href="/app" className="convoy-wordmark portal-wordmark" aria-label="Convoy workspace"><Image src={BRAND.iconSvg} alt="" width={30} height={30} />Convoy</Link>; }
export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "success" | "warning" | "error" }) { return <span className={`portal-badge portal-badge-${tone}`}>{children}</span>; }
export function Notice({ children, error = false }: { children: React.ReactNode; error?: boolean }) { return <div className={`portal-notice${error ? " portal-notice-error" : ""}`} role={error ? "alert" : "status"}>{children}</div>; }

const views = ["device", "chat", "usage", "traces"] as const;
type View = typeof views[number];
function readView(): View { const view = new URLSearchParams(window.location.search).get("view"); return views.includes(view as View) ? view as View : "device"; }

export function Portal() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const check = useCallback(async () => {
    try { const session = await request<PortalSession>("session"); setAuthenticated(session.authenticated); }
    catch (error) { setSessionError(errorText(error)); setAuthenticated(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void check(), 0); return () => window.clearTimeout(timer); }, [check]);
  if (authenticated === null) return <div className="portal-auth"><Mark /><div className="portal-auth-card"><p role="status">Opening your demo session…</p></div></div>;
  if (!authenticated) return <Login onLogin={() => { setSessionError(null); setAuthenticated(true); }} initialError={sessionError} />;
  return <Workspace onSessionEnd={() => setAuthenticated(false)} />;
}

function Login({ onLogin, initialError }: { onLogin: () => void; initialError: string | null }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  const lock = useRef(false);
  async function login(event: React.FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    try {
      const result = await request<PortalSession>("session", { method: "POST", body: JSON.stringify({ email: email.trim(), password }) });
      if (!result.authenticated) throw new Error("Sign-in was not completed. Check your credentials and try again.");
      setPassword(""); onLogin();
    } catch (cause) { setError(errorText(cause)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <main className="portal-auth">
    <Mark />
    <div className="portal-auth-layout">
      <div className="portal-auth-copy"><p className="portal-eyebrow">Convoy / Jetson demo</p><h1>A real model.<br />A real device.</h1><p>Talk to the model on our Jetson. Follow each request through its response, measured usage, and device telemetry.</p><div className="portal-auth-foot"><span className="portal-signal" />On-device inference</div></div>
      <section className="portal-auth-card" aria-labelledby="sign-in-heading"><p className="portal-eyebrow">Device access</p><h2 id="sign-in-heading">Open device tools</h2><p>Sign in with your device demo credentials. Project access is managed separately.</p>
        <form onSubmit={(event) => void login(event)}>
          <label htmlFor="portal-email">Email</label><input id="portal-email" type="email" autoComplete="username" className="field-input" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <label htmlFor="portal-password">Password</label><input id="portal-password" type="password" autoComplete="current-password" className="field-input" required value={password} onChange={(e) => setPassword(e.target.value)} />
          {error && <Notice error>{error}</Notice>}
          <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}<span aria-hidden="true">→</span></button>
        </form>
        <Link href="/" className="text-link portal-return">Back to Convoy</Link>
      </section>
    </div>
  </main>;
}

function Workspace({ onSessionEnd }: { onSessionEnd: () => void }) {
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
  useEffect(() => { const initial = window.setTimeout(() => void refresh(), 0); const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 10000); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [refresh]);
  useEffect(() => { const timer = window.setInterval(() => { setNow(Date.now()); setMonotonicNow(performance.now()); }, 1000); return () => window.clearInterval(timer); }, []);
  function navigate(next: View) { setView(next); window.history.pushState(null, "", `/app/device${next === "device" ? "" : `?view=${next}`}`); }
  async function logout() { try { await request<PortalSession>("session", { method: "DELETE" }); onSessionEnd(); } catch (cause) { setError(errorText(cause)); } }
  function openTrace(id: string) { setTraceId(id); navigate("traces"); }
  const stale = !!error || (receivedAt !== null && monotonicNow - receivedAt > 30000);
  // Only a nonce-bound live report establishes liveness. Generic device.status
  // can be online after history ingestion, and terminal identity states win.
  const identityStatus = snapshot && ["retired", "credential_revoked", "never_seen"].includes(snapshot.device.status) ? snapshot.device.status.replaceAll("_", " ") : null;
  const live = !!snapshot?.chat.online && !identityStatus && !stale;
  const statusLabel = identityStatus ?? (stale ? "Refresh needed" : live ? "Online" : "No recent live contact");
  return <div className="portal-shell">
    <a href="#portal-main" className="portal-skip">Skip to content</a>
    <aside className="portal-sidebar"><Mark /><div className="portal-workspace-label">Device & inference</div>
      <nav aria-label="Portal">{views.map((item, index) => <a key={item} href={`/app/device${item === "device" ? "" : `?view=${item}`}`} aria-current={view === item ? "page" : undefined} onClick={(event) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) { event.preventDefault(); navigate(item); } }}><span className="portal-nav-number" aria-hidden="true">0{index + 1}</span>{item[0].toUpperCase() + item.slice(1)}<span className="portal-nav-arrow" aria-hidden="true">↗</span></a>)}</nav>
      <div className="portal-sidebar-bottom"><p>Inference on a physical Jetson.<br />Measurements from the device.</p><Link className="text-link" href="/">Convoy website ↗</Link></div>
    </aside>
    <div className="portal-workarea">
      <header className="portal-topbar"><div><span className="portal-topbar-label">Connected device</span><strong>{snapshot?.device.name ?? "Loading device…"}</strong>{snapshot && <Badge tone={live ? "success" : "warning"}>{statusLabel}</Badge>}</div><button className="portal-text-button" onClick={() => void logout()}>Sign out</button></header>
      <main id="portal-main" className="portal-main" tabIndex={-1}>
        <div className="portal-page-heading"><div><p className="portal-eyebrow">Physical AI / {view === "chat" ? "On-device inference" : "Device evidence"}</p><h1>{view === "device" ? "Your Jetson, observed." : view === "chat" ? "Talk to your model." : view === "usage" ? "Measured on the device." : "Follow the inference."}</h1></div><button className="btn btn-secondary portal-refresh" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? "Refreshing…" : "Refresh"}<span aria-hidden="true">↻</span></button></div>
        {error && <Notice error>{error}{snapshot ? " Showing the last successful update. Refresh before sending another message." : " Use Refresh to reconnect."}</Notice>}
        {!snapshot ? !error && <div className="portal-loading" role="status">Reading device state and measurements…</div> : <>
          <p className="portal-updated">Updated {timestamp(snapshot.fetched_at)} · Refreshes every 10 seconds{stale ? " · Update is stale" : ""}</p>
          <div hidden={view !== "device"}><Device snapshot={snapshot} now={now} onChat={() => navigate("chat")} /></div>
          <div hidden={view !== "chat"}><Chat snapshot={snapshot} stale={stale} onRefresh={refresh} onSessionEnd={onSessionEnd} onTrace={openTrace} /></div>
          <div hidden={view !== "usage"}><Usage snapshot={snapshot} /></div>
          <div hidden={view !== "traces"}><Traces snapshot={snapshot} selectedId={traceId} onSelect={setTraceId} /></div>
        </>}
      </main>
    </div>
  </div>;
}
