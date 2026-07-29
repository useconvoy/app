"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function KillSwitch({ agentId, paused }: { agentId: string; paused: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      className={`btn btn-sm ${paused ? "btn-green" : "btn-red"}`}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await fetch(`/api/agents/${agentId}/pause`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paused: !paused }),
        });
        setBusy(false);
        router.refresh();
      }}
    >
      {paused ? "Resume fleet" : "⏻ Kill switch"}
    </button>
  );
}
