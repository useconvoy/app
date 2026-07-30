"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { IconArrowUpRight, IconCheck } from "./icons";

const STORAGE_KEY = "convoy-start-progress-v1";

const STEPS = [
  {
    id: "fleet",
    number: "01",
    title: "Read the fleet at a glance",
    time: "30 sec",
    href: "/dashboard",
    action: "Open the fleet overview",
    mutates: false,
    text: "Four company-owned agents and one night of seeded history. The dashboard is built around output, approval load and interruptibility — not around chat sessions, because nobody is chatting with these.",
    notice:
      "The counts — 47 leads, 6 deals, 12 doc drafts, 31 records — are seeded sample data, not measurements.",
  },
  {
    id: "environments",
    number: "02",
    title: "Compare the rules of the road",
    time: "45 sec",
    href: "/environments",
    action: "Compare the environments",
    mutates: false,
    text: "Sandbox beside Production. The agent version is identical in both. What differs is the connector instances, the credential handles bound to them, and the permission rules.",
    notice:
      "Find email.send in each list: refused outright in Sandbox, held for a named approver in Production.",
  },
  {
    id: "agent",
    number: "03",
    title: "Inspect a company agent",
    time: "60 sec",
    href: "/agents/agent_closed_won_paperwork",
    action: "Inspect the agent",
    mutates: false,
    text: "The Closed-Won Paperwork agent: its trigger, the tools its version declared, its parameters, its deployment in each environment, and the scenarios it has to survive.",
    notice:
      "Everything here belongs to the workspace and carries a version number, including the tool grants.",
  },
  {
    id: "sandbox",
    number: "04",
    title: "Run it where mistakes are cheap",
    time: "90 sec",
    href: "/systems?env=env_sandbox&tab=crm",
    action: "Open the Sandbox CRM",
    mutates: true,
    text: "Mark the Latch Robotics deal Closed-Won. That fires the simulated CRM webhook, which starts a real run through the real gateway and streams its trace as it happens.",
    notice:
      "Each row shows the call, its arguments, its result, and the policy verdict that let it through.",
  },
  {
    id: "tests",
    number: "05",
    title: "Earn the right to promote",
    time: "90 sec",
    href: "/agents/agent_closed_won_paperwork#scenario-suite",
    action: "Run the scenario suite",
    mutates: true,
    text: "Run the whole suite, then open Promotion. Every assertion reads sandbox system state rather than the agent's summary, and promotion stays blocked until the suite is green on this exact version.",
    notice:
      "The pre-flight diff puts the credential swap, the policy changes and the test status in one place.",
  },
  {
    id: "production",
    number: "06",
    title: "See two independent stops",
    time: "2 min",
    href: "/systems?env=env_production&tab=crm",
    action: "Open the Production CRM",
    mutates: true,
    text: "Mark the Northwind Systems deal Closed-Won. Its 22% discount makes the agent escalate on its own; later, Production policy independently holds the external invoice email.",
    notice:
      "The agent knows when to ask. The environment does not care whether it knows.",
  },
  {
    id: "audit",
    number: "07",
    title: "Close the loop",
    time: "45 sec",
    href: "/audit",
    action: "Open the audit log",
    mutates: false,
    text: "Follow any action back through its run, its tool call, its approval and the decision recorded against it. Then return to the fleet and look at the per-agent kill switch.",
    notice:
      "Every consequential branch is attributable, and every agent is interruptible mid-run.",
  },
] as const;

export function StartGuide() {
  const [completed, setCompleted] = useState<string[]>([]);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) setCompleted(JSON.parse(stored) as string[]);
    } catch {
      // Progress is an optional, device-local convenience.
    }
  }, []);

  const setStep = (id: string, done: boolean) => {
    const next = done ? Array.from(new Set([...completed, id])) : completed.filter((s) => s !== id);
    setCompleted(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // The walkthrough stays fully usable without storage.
    }
  };

  const progress = Math.round((completed.length / STEPS.length) * 100);

  return (
    <>
      <div className="start-progress">
        <div className="start-progress-meta">
          <span>Your progress</span>
          <strong>
            {completed.length}/{STEPS.length} visited
          </strong>
        </div>
        {completed.length > 0 ? (
          <button
            type="button"
            onClick={() => {
              setCompleted([]);
              try {
                window.localStorage.removeItem(STORAGE_KEY);
              } catch {
                // Nothing to clear.
              }
            }}
          >
            Clear progress
          </button>
        ) : (
          <span className="micro">Stored on this device only</span>
        )}
        <div
          className="start-progress-track"
          role="progressbar"
          aria-label="Walkthrough progress"
          aria-valuenow={completed.length}
          aria-valuemin={0}
          aria-valuemax={STEPS.length}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
      </div>

      <ol className="start-steps">
        {STEPS.map((step) => {
          const isDone = completed.includes(step.id);
          return (
            <li className={isDone ? "is-done" : ""} key={step.id}>
              <div className="start-marker" aria-hidden="true">
                {isDone ? <IconCheck size={16} /> : step.number}
              </div>
              <article>
                <div className="start-step-head">
                  <div>
                    <span className="start-step-time">
                      Step {step.number} · {step.time}
                    </span>
                    <h3>{step.title}</h3>
                  </div>
                  <span className={`start-flag${step.mutates ? " is-mutating" : ""}`}>
                    {step.mutates ? "Changes sample state" : "View only"}
                  </span>
                </div>
                <p>{step.text}</p>
                <div className="start-notice">
                  <span>What to notice</span>
                  {step.notice}
                </div>
                <div className="start-actions">
                  <Link href={step.href} className="btn btn-primary" onClick={() => setStep(step.id, true)}>
                    {step.action} <IconArrowUpRight size={15} />
                  </Link>
                  <label>
                    <input
                      type="checkbox"
                      checked={isDone}
                      aria-label={`Mark step ${step.number}, ${step.title}, as visited`}
                      onChange={(event) => setStep(step.id, event.target.checked)}
                    />
                    Mark visited
                  </label>
                </div>
              </article>
            </li>
          );
        })}
      </ol>

      <aside className="start-fallback">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-rule" aria-hidden="true" />
            If a step is already done
          </p>
          <h3>The sample workspace is shared state.</h3>
          <p>
            Anyone can drive it, so a deal may already be Closed-Won by the time you arrive. The
            mechanism is identical either way — read a completed run instead, or reseed the
            workspace from the dashboard.
          </p>
        </div>
        <Link href="/runs/run_hist_1" className="btn">
          Open a completed run <IconArrowUpRight size={15} />
        </Link>
      </aside>
    </>
  );
}
