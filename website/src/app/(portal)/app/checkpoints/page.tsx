import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { copy } from "@/lexicon";
import { gatherCheckpointItems } from "@/lib/checkpoints/data";
import { approvePlan, respondToGate, resumeRun } from "@/lib/runs/commands";
import { requireRunPage } from "@/lib/runs/context";
import { CheckpointsInbox } from "./checkpoints-inbox";

export const metadata: Metadata = { title: "Checkpoints" };
export const dynamic = "force-dynamic";

/**
 * The Checkpoints page (DESIGN §5 law 1): one inbox, three typed verbs,
 * plus promotion reviews. Viewers have nothing actionable here and are
 * redirected; the lens system hides the nav item, this is the server-side
 * check behind it. Items are gathered fresh from the edge on every
 * request; the session-bound actions ride in as props so every mutation
 * carries the acting human.
 */
export default async function CheckpointsPage() {
  const { session, membership, actor } = await requireRunPage();
  if (membership.role === "viewer") redirect("/app");

  const items = await gatherCheckpointItems(session.orgId, session.userId, actor);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <header>
        <h1 className="font-display text-3xl text-ink">Checkpoints</h1>
        <p className="mt-2 text-muted">{copy.checkpointsIntro}</p>
      </header>
      <CheckpointsInbox items={items} commands={{ resumeRun, approvePlan, respondToGate }} />
    </div>
  );
}
