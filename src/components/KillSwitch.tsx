"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ModalDialog } from "./ModalDialog";

export function KillSwitch({ agentId, agentName, paused }: { agentId: string; agentName: string; paused: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const apply = async () => {
    setBusy(true);
    await fetch(`/api/agents/${agentId}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused: !paused }),
    });
    setBusy(false);
    setConfirming(false);
    router.refresh();
  };

  return (
    <>
      <button
        className={`btn btn-sm ${paused ? "btn-success" : "btn-danger"}`}
        disabled={busy}
        onClick={() => (paused ? apply() : setConfirming(true))}
      >
        {paused ? "Resume" : "Pause fleet-wide"}
      </button>
      {confirming && (
        <ModalDialog labelledBy={`pause-title-${agentId}`} onClose={() => setConfirming(false)} className="modal-sm">
            <h2 id={`pause-title-${agentId}`}>Pause {agentName} fleet-wide?</h2>
            <p className="muted small" style={{ marginTop: 8 }}>
              This is the kill switch. No new runs will start in any environment, and in-flight runs stop at their next
              action. The pause is recorded in the audit log and is reversible at any time.
            </p>
            <div className="approval-actions" style={{ marginTop: 18 }}>
              <button className="btn btn-danger" disabled={busy} onClick={apply}>
                {busy ? "Pausing…" : "Pause agent"}
              </button>
              <button className="btn" onClick={() => setConfirming(false)}>Cancel</button>
            </div>
        </ModalDialog>
      )}
    </>
  );
}
