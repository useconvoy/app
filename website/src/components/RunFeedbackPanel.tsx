/**
 * Feedback panel for one run: the stream of what people already said plus
 * the composer, wired to the real feedback tables. This is a server
 * component; mount it from a server page (the run detail page's server
 * shell), never from a client component.
 *
 * Built in W5 but not mounted on the run detail page here, because that
 * page is in flight in another packet; the integrator mounts
 * `<RunFeedbackPanel runId={runId} />` under the run detail once both land.
 */
import "server-only";

import { submitFeedbackForm } from "@/lib/feedback/actions";
import { listFeedbackForRun } from "@/lib/feedback/queries";
import { requireOrgSession } from "@/lib/auth/session";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { FeedbackComposer } from "./FeedbackComposer";
import { FeedbackStream } from "./FeedbackStream";

export interface RunFeedbackPanelProps {
  runId: string;
  /** Attach feedback to one step of the run instead of the whole run. */
  stepId?: string;
}

export async function RunFeedbackPanel({ runId, stepId }: RunFeedbackPanelProps) {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") return null;

  const items = await listFeedbackForRun(session.orgId, runId);
  const canGive = can("give_feedback", membership.role, membership.capabilities);
  const action = submitFeedbackForm.bind(null, runId, stepId ?? null);

  return (
    <section aria-label="Feedback" className="space-y-4">
      <h2 className="font-display text-lg text-ink">Feedback</h2>
      <FeedbackStream items={items} emptyBody="Feedback left on this run will appear here." />
      {canGive && <FeedbackComposer action={action} />}
    </section>
  );
}
