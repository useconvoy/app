/**
 * Feedback stream: what people said about a run or a routine, newest
 * first. Each item shows the author, the kind, the words, a friendly date,
 * and a quiet learning-status chip so the D12 lifecycle is visible without
 * shouting ("Noted" -> "Queued for learning" -> "Applied").
 */
import { Chip } from "@/components/Chip";
import { EmptyState } from "@/components/EmptyState";
import { friendlyDateTime } from "@/lib/format";
import {
  feedbackCopy,
  feedbackKindLabels,
  learningStatusLabels,
  type FeedbackKind,
  type LearningStatus,
} from "@/lexicon";

/** Presentational shape; the server queries map their rows onto it. */
export interface FeedbackStreamItem {
  id: string;
  kind: FeedbackKind;
  rating: number | null;
  body: string | null;
  authorName: string;
  learningStatus: LearningStatus;
  createdAt: Date | string;
}

export interface FeedbackStreamProps {
  items: FeedbackStreamItem[];
  /** Body shown under the empty-state title when there is nothing yet. */
  emptyBody?: string;
}

export function FeedbackStream({ items, emptyBody }: FeedbackStreamProps) {
  if (items.length === 0) {
    return <EmptyState title={feedbackCopy.emptyStream} body={emptyBody} />;
  }
  return (
    <ul className="m-0 list-none space-y-0 rounded-lg border border-line bg-card px-4 py-1">
      {items.map((item) => (
        <li key={item.id} className="border-b border-line-soft py-3 last:border-b-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-ink">{item.authorName}</span>
            <Chip>{feedbackKindLabels[item.kind]}</Chip>
            {item.kind === "rating" && item.rating !== null && (
              <span className="font-mono text-xs text-muted">
                {feedbackCopy.starsOutOfFive(item.rating)}
              </span>
            )}
            <span className="font-mono text-xs uppercase text-muted">
              {friendlyDateTime(item.createdAt)}
            </span>
          </div>
          {item.body && <p className="mt-1 text-sm text-ink">{item.body}</p>}
          <div className="mt-2">
            <Chip tone="graphite">{learningStatusLabels[item.learningStatus]}</Chip>
          </div>
        </li>
      ))}
    </ul>
  );
}
