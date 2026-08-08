import type { Metadata } from "next";

import { EmptyState } from "@/components/EmptyState";
import { FeedbackStream } from "@/components/FeedbackStream";
import { listFeedbackForOrg } from "@/lib/feedback/queries";
import { friendlyDate } from "@/lib/format";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { listRoutines } from "@/lib/routines/queries";
import { improveCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Learning" };
export const dynamic = "force-dynamic";

/**
 * Learning: the improvements queue, the routine changelog, and a strip of
 * recent feedback. The learning service that proposes improvements is not
 * built yet, so the queue renders its honest empty state; the changelog
 * shows each routine's real arrival, and the feedback strip is real.
 * Viewers see the changelog only, since queue review excludes the Viewer
 * role. TODO(learning): the service fills the queue.
 */
export default async function LearningPage() {
  const { session, membership } = await requireRoutinesPage();
  const showQueue = membership.role !== "viewer";

  const [routines, recentFeedback] = await Promise.all([
    listRoutines(session.orgId),
    showQueue ? listFeedbackForOrg(session.orgId, 10) : Promise.resolve([]),
  ]);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <h1 className="font-display text-3xl text-ink">{improveCopy.learningTitle}</h1>
        <p className="mt-2 text-sm text-muted">{improveCopy.learningIntro}</p>
      </header>

      {showQueue && (
        <section aria-label={improveCopy.queueTitle}>
          <h2 className="font-display text-lg text-ink">{improveCopy.queueTitle}</h2>
          <div className="mt-3">
            <EmptyState title={improveCopy.queueEmpty} body={improveCopy.queueEmptyBody} />
          </div>
        </section>
      )}

      <section aria-label={improveCopy.changelogTitle}>
        <h2 className="font-display text-lg text-ink">{improveCopy.changelogTitle}</h2>
        <div className="mt-3 space-y-4">
          {routines.length > 0 ? (
            routines.map((routine) => (
              <div key={routine.id} className="rounded-lg border border-line bg-card p-5">
                <h3 className="text-sm font-medium text-ink">{routine.name}</h3>
                <ul className="m-0 mt-2 list-none space-y-1.5 p-0">
                  <li className="flex flex-wrap gap-3 text-sm">
                    <span className="font-mono text-xs uppercase text-muted">
                      {friendlyDate(routine.createdAt)}
                    </span>
                    <span className="text-ink">
                      {routine.sourceEntryId
                        ? improveCopy.changelogInstalledNote
                        : improveCopy.changelogCreatedNote}
                    </span>
                  </li>
                </ul>
              </div>
            ))
          ) : (
            <EmptyState title={improveCopy.changelogEmpty} body={improveCopy.changelogNoRoutines} />
          )}
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
