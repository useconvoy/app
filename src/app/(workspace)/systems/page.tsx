"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Badge, fmtTime } from "@/components/bits";
import type { ChatMessage, CrmDeal, CrmLead, Doc, Email, Environment } from "@/server/types";

interface SystemsData {
  environment?: Environment;
  deals: CrmDeal[];
  leads: CrmLead[];
  docs: Doc[];
  emails: Email[];
  chatMessages: ChatMessage[];
}

const TABS = ["CRM — HubSpot", "Email — Gmail", "Docs — Google Docs", "Chat — Slack"] as const;
const TAB_KEYS: Record<string, (typeof TABS)[number]> = {
  crm: TABS[0],
  email: TABS[1],
  docs: TABS[2],
  chat: TABS[3],
};
const KEY_BY_TAB = Object.fromEntries(Object.entries(TAB_KEYS).map(([key, value]) => [value, key]));

export default function SystemsPage() {
  return (
    <Suspense fallback={<div className="faint" role="status">Loading connected systems…</div>}>
      <Systems />
    </Suspense>
  );
}

function Systems() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedEnv = searchParams.get("env");
  const requestedTab = searchParams.get("tab");
  const [env, setEnv] = useState(requestedEnv === "env_production" ? "env_production" : "env_sandbox");
  const [tab, setTab] = useState<(typeof TABS)[number]>(TAB_KEYS[requestedTab ?? ""] ?? TABS[0]);
  const [data, setData] = useState<SystemsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<Doc | null>(null);
  const [openEmail, setOpenEmail] = useState<Email | null>(null);
  const [closing, setClosing] = useState<string | null>(null);

  useEffect(() => {
    const nextEnv = requestedEnv === "env_production" ? "env_production" : "env_sandbox";
    const nextTab = TAB_KEYS[requestedTab ?? ""] ?? TABS[0];
    setEnv(nextEnv);
    setTab(nextTab);
    setOpenDoc(null);
    setOpenEmail(null);
  }, [requestedEnv, requestedTab]);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/systems?env=${env}`);
      if (!res.ok) throw new Error(`Request failed with status ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch {
      setError("Connected systems could not be loaded. Check your connection and try again.");
    }
  }, [env]);

  useEffect(() => {
    void load();
    const es = new EventSource("/api/stream");
    es.onmessage = (e) => {
      const evt = JSON.parse(e.data);
      if (evt.type === "system_updated") void load();
    };
    return () => es.close();
  }, [load]);

  // Selections belong to one environment/tab — clear them on switch.
  const switchEnv = (e: string) => {
    setEnv(e);
    setData(null);
    setError(null);
    setOpenDoc(null);
    setOpenEmail(null);
    window.history.pushState(null, "", `/systems?env=${e}&tab=${KEY_BY_TAB[tab]}`);
  };
  const switchTab = (t: (typeof TABS)[number]) => {
    setTab(t);
    setOpenDoc(null);
    setOpenEmail(null);
    window.history.pushState(null, "", `/systems?env=${env}&tab=${KEY_BY_TAB[t]}`);
  };
  const moveTabFocus = (event: React.KeyboardEvent<HTMLButtonElement>, current: (typeof TABS)[number]) => {
    const index = TABS.indexOf(current);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % TABS.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + TABS.length) % TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = TABS[nextIndex];
    switchTab(nextTab);
    requestAnimationFrame(() => document.getElementById(`system-tab-${KEY_BY_TAB[nextTab]}`)?.focus());
  };

  const closeWon = async (dealId: string) => {
    setClosing(dealId);
    const res = await fetch("/api/webhooks/crm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dealId, toStage: "closedwon" }),
    });
    const body = await res.json();
    setClosing(null);
    if (body.runIds?.[0]) router.push(`/runs/${body.runIds[0]}`);
    else void load();
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Connected systems</h1>
          <p className="page-sub">
            The systems of record the convoy works against — instantiated per environment with environment-scoped
            credentials. Marking a deal Closed-Won here fires the CRM webhook into Convoy Labs.
          </p>
        </div>
        <div className="page-actions">
          <div className="segment" role="group" aria-label="Environment">
            <button aria-pressed={env === "env_sandbox"} className={env === "env_sandbox" ? "active" : ""} onClick={() => switchEnv("env_sandbox")}>Sandbox</button>
            <button aria-pressed={env === "env_production"} className={env === "env_production" ? "active" : ""} onClick={() => switchEnv("env_production")}>Production</button>
          </div>
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Connected system">
        {TABS.map((t) => (
          <button
            key={t}
            id={`system-tab-${KEY_BY_TAB[t]}`}
            role="tab"
            aria-selected={tab === t}
            aria-controls="system-panel"
            tabIndex={tab === t ? 0 : -1}
            className={`tab ${tab === t ? "active" : ""}`}
            onClick={() => switchTab(t)}
            onKeyDown={(event) => moveTabFocus(event, t)}
          >
            {t}
          </button>
        ))}
      </div>

      <div
        id="system-panel"
        role="tabpanel"
        aria-labelledby={`system-tab-${KEY_BY_TAB[tab]}`}
      >
      {error && !data ? (
        <div className="error-state" role="alert">
          <strong>Connected systems are unavailable.</strong>
          <span>{error}</span>
          <button className="btn btn-sm" type="button" onClick={() => void load()}>Try again</button>
        </div>
      ) : !data ? (
        <div className="faint" role="status">Loading connected systems…</div>
      ) : tab === "CRM — HubSpot" ? (
        <>
        <div className="card card-flush systems-deal-table">
          <table className="table">
            <thead>
              <tr>
                <th>Deal</th><th className="num">Amount</th><th>Stage</th><th>Contact</th><th>PO</th>
                <th className="num">Discount</th><th>Agent-written fields</th><th></th>
              </tr>
            </thead>
            <tbody>
              {data.deals.map((deal) => (
                <tr key={deal.id}>
                  <td style={{ fontWeight: 600 }}>{deal.name}</td>
                  <td className="num mono">{deal.currency} {deal.amount.toLocaleString("en-US")}</td>
                  <td>
                    <Badge tone={deal.stage === "closedwon" ? "success" : "neutral"}>
                      {deal.stage === "closedwon" ? "Closed-Won" : deal.stage}
                    </Badge>
                  </td>
                  <td className="small">
                    {deal.contactName}
                    <br />
                    <span className="faint mono">{deal.contactEmail || "(none)"}</span>
                  </td>
                  <td className="mono small">{deal.poNumber ?? <span className="faint">—</span>}</td>
                  <td className="num mono small" style={deal.discountPct > 15 ? { color: "var(--warning-text)", fontWeight: 600 } : undefined}>
                    {deal.discountPct}%
                  </td>
                  <td className="small muted">
                    {Object.entries(deal.fields).map(([k, v]) => (
                      <div key={k}><span className="mono">{k}</span>: {v}</div>
                    ))}
                  </td>
                  <td>
                    {deal.stage !== "closedwon" && (
                      <button className="btn btn-sm btn-primary" disabled={closing === deal.id} onClick={() => closeWon(deal.id)}>
                        {closing === deal.id ? "Firing webhook…" : "Mark Closed-Won"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="systems-deal-cards">
          {data.deals.map((deal) => (
            <article className="systems-deal-card" key={deal.id}>
              <div className="flex-between">
                <strong>{deal.name}</strong>
                <Badge tone={deal.stage === "closedwon" ? "success" : "neutral"}>
                  {deal.stage === "closedwon" ? "Closed-Won" : deal.stage}
                </Badge>
              </div>
              <dl>
                <dt>Amount</dt><dd className="mono">{deal.currency} {deal.amount.toLocaleString("en-US")}</dd>
                <dt>Contact</dt><dd>{deal.contactName}<br /><span className="faint mono">{deal.contactEmail || "(none)"}</span></dd>
                <dt>PO / discount</dt><dd className="mono">{deal.poNumber ?? "—"} · {deal.discountPct}%</dd>
              </dl>
              {deal.stage !== "closedwon" && (
                <button className="btn btn-primary" disabled={closing === deal.id} onClick={() => closeWon(deal.id)}>
                  {closing === deal.id ? "Firing webhook…" : "Mark deal Closed-Won"}
                </button>
              )}
            </article>
          ))}
        </div>
        </>
      ) : tab === "Email — Gmail" ? (
        <div className="grid grid-2">
          <div className="card card-flush">
            <table className="table">
              <thead><tr><th>To</th><th>Subject</th><th>Sent</th></tr></thead>
              <tbody>
                {data.emails.map((em) => (
                  <tr key={em.id}>
                    <td className="mono small">{em.to}</td>
                    <td className="small">
                      <button className="table-row-button" onClick={() => setOpenEmail(em)}>
                        {em.subject}
                      </button>
                    </td>
                    <td className="faint small" style={{ whiteSpace: "nowrap" }}>{fmtTime(em.ts)}</td>
                  </tr>
                ))}
                {data.emails.length === 0 && <tr><td colSpan={3} className="faint">The outbox is empty.</td></tr>}
              </tbody>
            </table>
          </div>
          {openEmail && (
            <div className="card">
              <div className="card-title">{openEmail.subject}</div>
              <div className="faint small" style={{ marginTop: 2 }}>
                From {openEmail.from} · to {openEmail.to} · {fmtTime(openEmail.ts)}
              </div>
              {openEmail.attachmentDocId && <div className="tag" style={{ marginTop: 8 }}>Attachment: order form</div>}
              <div className="doc-view" style={{ marginTop: 12 }}>{openEmail.body}</div>
            </div>
          )}
        </div>
      ) : tab === "Docs — Google Docs" ? (
        <div className="grid grid-2">
          <div className="card card-flush">
            <table className="table">
              <thead><tr><th>Title</th><th>Status</th><th>Created</th></tr></thead>
              <tbody>
                {data.docs.map((doc) => (
                  <tr key={doc.id}>
                    <td className="small" style={{ fontWeight: 600 }}>
                      <button className="table-row-button" onClick={() => setOpenDoc(doc)}>
                        {doc.title}
                      </button>
                    </td>
                    <td><Badge tone={doc.status === "published" ? "success" : "neutral"}>{doc.status}</Badge></td>
                    <td className="faint small" style={{ whiteSpace: "nowrap" }}>{fmtTime(doc.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {openDoc && (
            <div className="card">
              <div className="card-title">{openDoc.title}</div>
              <div className="doc-view" style={{ marginTop: 12 }}>{openDoc.content}</div>
            </div>
          )}
        </div>
      ) : (
        <div className="card" style={{ maxWidth: 760 }}>
          <div className="card-title"># {env === "env_sandbox" ? "revops-sandbox" : "revops"}</div>
          <div className="stack" style={{ marginTop: 14 }}>
            {data.chatMessages.map((m) => (
              <div key={m.id} style={{ fontSize: 13 }}>
                <span className="faint small mono">{fmtTime(m.ts)}</span>{" "}
                <b>{m.byRunId ? "Convoy Labs Agent" : "Convoy Labs"}</b>
                <div className="muted" style={{ marginTop: 2 }}>{m.text}</div>
              </div>
            ))}
            {data.chatMessages.length === 0 && <div className="faint">No messages yet.</div>}
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
