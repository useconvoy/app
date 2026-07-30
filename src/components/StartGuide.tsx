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
    action: "Open fleet overview",
    notice: "View only",
    text: "See four company-owned agents and the sample overnight history. Notice that the dashboard emphasizes output, approval load, and interruptibility—not chat sessions.",
    look: "47 leads, 6 deals, 12 doc drafts, and 31 CRM records are seeded demo data.",
  },
  {
    id: "environments",
    number: "02",
    title: "Compare the rules of the road",
    time: "45 sec",
    href: "/environments",
    action: "Compare environments",
    notice: "View only",
    text: "Open Sandbox beside Production. The agent version stays the same; connector credentials and permission policies belong to the environment.",
    look: "External email is refused in Sandbox and requires approval in Production.",
  },
  {
    id: "agent",
    number: "03",
    title: "Inspect a company agent",
    time: "60 sec",
    href: "/agents/agent_closed_won_paperwork",
    action: "Inspect the agent",
    notice: "View only",
    text: "Review the Closed-Won Paperwork agent’s trigger, declared tool grants, parameters, deployments, and eight edge-case scenarios.",
    look: "It is a versioned workspace asset, not a prompt tied to Maya’s account.",
  },
  {
    id: "sandbox",
    number: "04",
    title: "Run it where mistakes are cheap",
    time: "90 sec",
    href: "/systems?env=env_sandbox&tab=crm",
    action: "Open Sandbox CRM",
    notice: "Changes sample state",
    text: "Mark the Latch Robotics deal Closed-Won. The simulated CRM webhook starts a real run through the gateway and streams its trace.",
    look: "Every tool call shows its input, output, policy verdict, and environment.",
  },
  {
    id: "tests",
    number: "05",
    title: "Earn the right to promote",
    time: "90 sec",
    href: "/agents/agent_closed_won_paperwork#scenario-suite",
    action: "Run the scenario suite",
    notice: "Changes sample state",
    text: "Run all eight scenarios, then open Promotion. Convoy checks system state and blocks promotion unless this exact version is green.",
    look: "The pre-flight diff shows credential changes, policy changes, and test status together.",
  },
  {
    id: "production",
    number: "06",
    title: "See two independent human gates",
    time: "2 min",
    href: "/systems?env=env_production&tab=crm",
    action: "Open Production CRM",
    notice: "Changes sample state",
    text: "Mark the Northwind Systems deal Closed-Won. Its 22% discount makes the agent self-escalate; later, Production policy independently stops the external invoice email.",
    look: "The agent knows when to ask. The environment does not care whether it knows.",
  },
  {
    id: "audit",
    number: "07",
    title: "Close the loop",
    time: "45 sec",
    href: "/audit",
    action: "Open the audit log",
    notice: "View only",
    text: "Trace an action back to its run and reviewer. Then return to the fleet to see the per-agent kill switch.",
    look: "Every consequential branch is attributable and the fleet is interruptible.",
  },
] as const;

export function StartGuide() {
  const [completed, setCompleted] = useState<string[]>([]);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) setCompleted(JSON.parse(stored) as string[]);
    } catch {
      // Progress is an optional device-local enhancement.
    }
  }, []);

  const setStep = (id: string, done: boolean) => {
    const next = done ? Array.from(new Set([...completed, id])) : completed.filter((step) => step !== id);
    setCompleted(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Links and the walkthrough remain fully usable without storage.
    }
  };

  const progress = Math.round((completed.length / STEPS.length) * 100);

  return (
    <>
      <div className="start-progress" aria-label={`Walkthrough progress: ${completed.length} of ${STEPS.length} steps`}>
        <div>
          <span>Your walkthrough</span>
          <strong>{completed.length}/{STEPS.length} visited</strong>
        </div>
        <div className="start-progress-track" aria-hidden="true">
          <span style={{ width: `${progress}%` }} />
        </div>
        {completed.length > 0 && (
          <button type="button" onClick={() => {
            setCompleted([]);
            try { window.localStorage.removeItem(STORAGE_KEY); } catch {}
          }}>
            Clear local progress
          </button>
        )}
      </div>

      <ol className="start-steps">
        {STEPS.map((step) => {
          const isDone = completed.includes(step.id);
          return (
            <li className={isDone ? "complete" : ""} key={step.id}>
              <div className="start-step-marker" aria-hidden="true">
                {isDone ? <IconCheck size={16} /> : step.number}
              </div>
              <article>
                <div className="start-step-head">
                  <div>
                    <span className="start-step-time">{step.time}</span>
                    <h3>{step.title}</h3>
                  </div>
                  <span className={step.notice === "View only" ? "start-notice" : "start-notice mutates"}>
                    {step.notice}
                  </span>
                </div>
                <p>{step.text}</p>
                <div className="start-look">
                  <span>What to notice</span>
                  {step.look}
                </div>
                <div className="start-step-actions">
                  <Link href={step.href} className="btn btn-primary" onClick={() => setStep(step.id, true)}>
                    {step.action} <IconArrowUpRight size={15} />
                  </Link>
                  <label>
                    <input
                      type="checkbox"
                      checked={isDone}
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
          <span className="preview-eyebrow">Already changed?</span>
          <h3>The sample workspace is shared state.</h3>
          <p>
            If either sample deal is already Closed-Won, review a separate seeded trace instead. The mechanism is the
            same and its audit evidence remains visible.
          </p>
        </div>
        <Link href="/runs/run_hist_1" className="btn">
          Open a completed run <IconArrowUpRight size={15} />
        </Link>
      </aside>
    </>
  );
}
