import type { Metadata } from "next";

import { EmptyState } from "@/components/EmptyState";
import { FeedbackStream } from "@/components/FeedbackStream";
import { ImprovementCard } from "@/components/ImprovementCard";
import { learningClient } from "@/lib/api/learning";
import { listFeedbackForOrg } from "@/lib/feedback/queries";
import { friendlyDate } from "@/lib/format";
import { routines } from "@/lib/fixtures/world";
import { approveImprovement, shipImprovement } from "@/lib/learning/actions";
import { can } from "@/lib/permissions";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { improveCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Learning" };

/**
 * Learning (DESIGN §5): the improvements queue with diffs and test-score
 * evidence, the routine changelog, and a strip of recent feedback. The
 * queue's Approve / Ship actions are gated server-side by
 * ship_improvements; everyone else reads. Viewers see the changelog only
 * (DESIGN §2: queue review excludes viewers). Improvements come through
 * the typed learning client's fixture adapter; feedback is real.
 * TODO(learning).
 */
export default async function LearningPage() {
  const { session, membership } = await requireRoutinesPage();
  const canShip = can("ship_improvements", membership.role, membership.capabilities);
  const showQueue = membership.role !== "viewer";

  const client = learningClient();
  const [improvements, recentFeedback, changelogs] = await Promise.all([
    showQueue ? client.listImprovements(session.orgId) : Promise.resolve([]),
    showQueue ? listFeedbackForOrg(session.orgId, 10) : Promise.resolve([]),
    Promise.all(
      routines.map(async (routine) => ({
        routine,
        entries: await client.listChangelog(routine.id),
      })),
    ),
  ]);
  const pending = improvements.filter((improvement) => improvement.status !== "shipped");
  const shipped = improvements.filter((improvement) => improvement.status === "shipped");

  async function approveAction(formData: FormData) {
    "use server";
    await approveImprovement(String(formData.get("improvementId") ?? ""));
  }

  async function shipAction(formData: FormData) {
    "use server";
    await shipImprovement(String(formData.get("improvementId") ?? ""));
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <h1 className="font-display text-3xl text-ink">{improveCopy.learningTitle}</h1>
        <p className="mt-2 text-sm text-muted">{improveCopy.learningIntro}</p>
      </header>

      {showQueue && (
        <section aria-label={improveCopy.queueTitle}>
          <h2 className="font-display text-lg text-ink">{improveCopy.queueTitle}</h2>
          {!canShip && <p className="mt-1 text-sm text-muted">{improveCopy.readOnlyQueueNote}</p>}
          <div className="mt-3 space-y-4">
            {pending.length > 0 ? (
              [...pending, ...shipped].map((improvement) => (
                <ImprovementCard
                  key={improvement.id}
                  improvement={improvement}
                  canShip={canShip}
                  approveAction={approveAction}
                  shipAction={shipAction}
                />
              ))
            ) : (
              <EmptyState title={improveCopy.queueEmpty} body={improveCopy.queueEmptyBody} />
            )}
          </div>
        </section>
      )}

      <section aria-label={improveCopy.changelogTitle}>
        <h2 className="font-display text-lg text-ink">{improveCopy.changelogTitle}</h2>
        <div className="mt-3 space-y-4">
          {changelogs.map(({ routine, entries }) => (
            <div key={routine.id} className="rounded-lg border border-line bg-card p-5">
              <h3 className="text-sm font-medium text-ink">{routine.name}</h3>
              {entries.length > 0 ? (
                <ul className="m-0 mt-2 list-none space-y-1.5 p-0">
                  {entries.map((entry) => (
                    <li key={`${entry.at}:${entry.note}`} className="flex flex-wrap gap-3 text-sm">
                      <span className="font-mono text-xs uppercase text-muted">
                        {friendlyDate(entry.at)}
                      </span>
                      <span className="text-ink">{entry.note}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-muted">{improveCopy.changelogEmptyBody}</p>
              )}
            </div>
          ))}
        </div>
      </section>

      {showQueue && (
        <section aria-label={improveCopy.feedbackReviewTitle}>
          <h2 className="font-display text-lg text-ink">{improveCopy.feedbackReviewTitle}</h2>
          <div className="mt-3">
            <FeedbackStream
              items={recentFeedback}
              emptyBody="Feedback from this organization's runs lands here for review."
            />
          </div>
        </section>
      )}
    </div>
  );
}
