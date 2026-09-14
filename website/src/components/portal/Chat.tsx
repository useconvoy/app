"use client";

import { useEffect, useRef, useState } from "react";
import type { PortalChatInput, PortalChatRequest, PortalSnapshot } from "@/lib/portal/types";
import { Badge, errorText, Notice, request, RequestError } from "./Portal";
import { number } from "./TelemetryChart";
import { Fact } from "./Views";

type Turn = { id: string; prompt: string; started: number; request: PortalChatRequest | null; rejected: boolean; error: string | null; elapsed: number | null };
const pending = (turn: Turn) => !turn.rejected && (!turn.request || turn.request.status === "queued" || turn.request.status === "running");
const duration = (ms: number | null | undefined) => ms == null ? "Not reported" : ms < 1000 ? `${number(ms)} ms` : `${number(ms / 1000, 2)} s`;

export function Chat({ snapshot: s, stale, onRefresh, onSessionEnd, onTrace }: { snapshot: PortalSnapshot; stale: boolean; onRefresh: () => Promise<void>; onSessionEnd: () => void; onTrace: (id: string) => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [paused, setPaused] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const lock = useRef(false);
  const mounted = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);
  const active = turns.find(pending);
  const activeId = active?.id;
  const started = active?.started;
  const messages: PortalChatInput["messages"] = [...turns.flatMap((turn): PortalChatInput["messages"] => turn.request?.status === "succeeded" && turn.request.content ? [{ role: "user", content: turn.prompt }, { role: "assistant", content: turn.request.content }] : []), { role: "user", content: draft.trim() }];
  const bytes = messages.reduce((total, message) => total + new TextEncoder().encode(message.content).length, 0);
  const firstRelease = turns.find((t) => t.request)?.request?.release_id;
  const reason = stale ? "Refresh device availability before sending a message."
    : !s.chat.eligible || !s.chat.release_id ? s.chat.reason ?? "The device is not ready to receive messages."
    : firstRelease && firstRelease !== s.chat.release_id ? "The running release changed. Start a new chat to use the current model."
    : messages.length > 16 ? "This conversation reached its message limit. Start a new chat to continue."
    : bytes > 8192 ? "This conversation exceeds 8 KiB. Shorten your message or start a new chat."
    : null;
  const outputLimit = Math.min(128, s.chat.max_tokens);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!activeId || paused || submitting) return;
    let disposed = false;
    let timer: number | undefined;
    const controller = new AbortController();
    async function poll() {
      try {
        const result = await request<PortalChatRequest>(`chat/${encodeURIComponent(activeId!)}`, { signal: controller.signal });
        if (disposed) return;
        const done = result.status !== "queued" && result.status !== "running";
        setTurns((previous) => previous.map((turn) => turn.id === activeId ? { ...turn, request: result, elapsed: done ? performance.now() - turn.started : null } : turn));
        setStatusError(null);
        if (done) void onRefresh();
        else if (started !== undefined && performance.now() - started > 180000) { setPaused(true); setStatusError("The device has not completed this request yet. Resume checking to retrieve its status."); }
        else timer = window.setTimeout(() => void poll(), 500);
      } catch (error) {
        if (disposed) return;
        if (error instanceof RequestError && error.status === 401) { onSessionEnd(); return; }
        setPaused(true);
        setStatusError(error instanceof RequestError && error.status === 404 ? "This request has not been found. Recheck the same request to recover a response; it will not be sent again." : `Could not check the request. ${errorText(error)} Resume checking to recover its result.`);
      }
    }
    void poll();
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [activeId, paused, submitting, started, onRefresh, onSessionEnd]);
  useEffect(() => {
    if (!activeId) return;
    const timer = window.setInterval(() => setTick(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, [activeId]);

  async function send(event?: React.FormEvent) {
    event?.preventDefault();
    if (lock.current || active || reason || !draft.trim() || !s.chat.release_id) return;
    lock.current = true; setSubmitting(true); setPaused(false); setStatusError(null);
    const id = crypto.randomUUID();
    const begin = performance.now();
    const turn: Turn = { id, prompt: draft.trim(), started: begin, request: null, rejected: false, error: null, elapsed: null };
    setTick(begin); setTurns((previous) => [...previous, turn]); setDraft("");
    const body: PortalChatInput = { request_id: id, expected_release_id: s.chat.release_id, messages, max_tokens: outputLimit };
    try {
      const result = await request<PortalChatRequest>("chat", { method: "POST", body: JSON.stringify(body) });
      if (!mounted.current) return;
      const done = result.status !== "queued" && result.status !== "running";
      setTurns((previous) => previous.map((item) => item.id === id ? { ...item, request: result, elapsed: done ? performance.now() - begin : null } : item));
      if (done) void onRefresh();
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof RequestError && error.status === 401) { onSessionEnd(); return; }
      if (error instanceof RequestError && error.status >= 400 && error.status < 500) setTurns((previous) => previous.map((item) => item.id === id ? { ...item, rejected: true, error: errorText(error), elapsed: performance.now() - begin } : item));
      else setStatusError("Submission could not be confirmed. Checking the same request ID before another message can be sent.");
    } finally { lock.current = false; if (mounted.current) setSubmitting(false); }
  }
  function clear() { setTurns([]); setDraft(""); setPaused(false); setStatusError(null); input.current?.focus(); }

  return <div className="portal-chat-layout">
    <section className="portal-chat-session" aria-label="Chat session"><div className="portal-chat-header"><div><span className="portal-signal" /><strong>{s.device.name}</strong><Badge tone={s.chat.online && !stale ? "success" : "warning"}>{stale ? "Availability stale" : s.chat.online ? "Online" : "Offline"}</Badge></div><button className="portal-text-button" onClick={clear} disabled={submitting || (!!active && !paused) || (!turns.length && !draft)}>New chat</button></div>
      <div className="portal-transcript" role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions text">
        {!turns.length && <div className="portal-chat-empty"><p className="portal-eyebrow">A conversation on your Jetson</p><h2>Send a message.<br />See what happens on-device.</h2><p>The active model responds on {s.device.name}. Its measured request timing and token usage appear with each completed response.</p><div className="portal-starters">{[{ label: "Explain a concept", prompt: "Explain how a neural network learns in three short sentences." }, { label: "Test conversation context", prompt: "My rover is named Cedar. Reply only with its name." }].map((starter) => <button key={starter.label} className="btn btn-secondary" disabled={!!reason} onClick={() => { setDraft(starter.prompt); input.current?.focus(); }}>{starter.label}<span aria-hidden="true">↗</span></button>)}</div></div>}
        {turns.map((turn) => <article className="portal-turn" key={turn.id}><div className="portal-chat-message portal-user-message"><div className="portal-message-label">You</div><p>{turn.prompt}</p></div><div className="portal-chat-message portal-model-message"><div className="portal-message-label">Jetson model <span>Device response</span></div>
          {turn.request?.status === "succeeded" ? <><p className="portal-answer">{turn.request.content || "The model returned an empty response."}</p>{turn.request.finish_reason === "length" && <p className="portal-response-note">The response reached its output limit and may be incomplete.</p>}<dl className="portal-result-metrics"><Fact label="Browser round trip" value={duration(turn.elapsed)} /><Fact label="On-device latency" value={duration(turn.request.metrics?.latency_ms)} /><Fact label="Input / output" value={`${number(turn.request.usage?.prompt_tokens, 0)} / ${number(turn.request.usage?.completion_tokens, 0)} tokens`} /></dl><div className="portal-response-footer"><Badge tone="success">Complete</Badge>{turn.request.trace_id && <button className="portal-table-link" onClick={() => onTrace(turn.request!.trace_id!)}>Inspect trace ↗</button>}</div></>
            : turn.rejected || (turn.request && !pending(turn)) ? <Notice error><strong>{turn.request?.status === "expired" ? "Request expired. " : "Request failed. "}</strong>{turn.error ?? turn.request?.error?.message ?? "No response was returned by the device."}<button className="portal-text-button" disabled={!!active} onClick={() => { setDraft(turn.prompt); input.current?.focus(); }}>Edit message</button></Notice>
            : <div className="portal-generation"><Badge tone="warning">{paused ? "Check paused" : turn.request?.status === "running" ? "Running on device" : turn.request?.status === "queued" ? "Queued for device" : submitting ? "Submitting request" : "Confirming request"}</Badge><p>{paused ? "The request may still complete on the device. Resume checking to retrieve its result." : turn.request?.status === "running" ? "The model is generating a response. The complete reply appears when it finishes." : "Waiting for the device to accept and complete this request."}</p><span className="portal-generation-clock" aria-live="off">{duration(Math.max(0, tick - turn.started))} elapsed in browser</span></div>}
        </div></article>)}
      </div>
      <form className="portal-composer" onSubmit={(event) => void send(event)}>
        {reason && <p className="portal-compose-notice" id="portal-chat-unavailable">{reason}</p>}
        {statusError && <Notice error>{statusError}</Notice>}
        {paused && active && <div className="portal-recovery"><button type="button" className="btn btn-secondary" onClick={() => { setPaused(false); setStatusError(null); }}>Resume checking</button><span>New chat discards this conversation. It does not cancel a request on the device.</span></div>}
        <label htmlFor="portal-message">Message</label><textarea ref={input} id="portal-message" className="field-input" rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={submitting || !!active} aria-describedby={reason ? "portal-chat-help portal-chat-unavailable" : "portal-chat-help"} onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); } }} />
        <div className="portal-composer-footer"><span id="portal-chat-help">⌘ / Ctrl + Enter · Up to {number(outputLimit, 0)} output tokens</span><button className="btn btn-primary" disabled={submitting || !!active || !!reason || !draft.trim()} type="submit">{submitting ? "Sending…" : active ? "Waiting…" : "Send message"}<span aria-hidden="true">↑</span></button></div>
      </form>
    </section>
    <aside className="portal-chat-context" aria-label="Chat context"><section><p className="portal-eyebrow">Active model</p><h2>{s.release?.name ?? "No model reported"}</h2><p>{s.release?.model_repo ?? "Model repository not reported"}</p><dl className="portal-facts"><Fact label="Context window" value={s.chat.context_window == null ? "Not reported" : `${number(s.chat.context_window, 0)} tokens`} /><Fact label="Output limit" value={`${number(outputLimit, 0)} tokens`} /></dl></section><section><h2>Two different clocks</h2><p><strong>Browser round trip</strong> runs from Send until this browser receives the final result, including relay and polling.</p><p><strong>On-device latency</strong> comes from the gateway measurement returned by the device.</p></section><section><h2>This conversation</h2><p>Completed replies are included with your next message, up to 16 messages and 8 KiB. The model’s context limit also applies.</p><p>History stays in this browser tab while the portal is open. Chat sends text to the model; it does not control the robot.</p></section></aside>
  </div>;
}
