/**
 * One improvement on the learning queue: title, plain summary, the diff in
 * mono lines with +/-/~ markers (marker plus color, never color alone),
 * test-score evidence, and the Approve / Ship actions when the viewer
 * holds ship_improvements. The gate result arrives as a prop; enforcement
 * lives in the server actions.
 */
import { Chip, type ChipTone } from "@/components/Chip";
import { Button } from "@/components/Button";
import type { Improvement, ImprovementDiffLine } from "@/lib/api/learning";
import { improveCopy, improvementStatusLabels, testScoreEvidence } from "@/lexicon";

type FormAction = (formData: FormData) => void | Promise<void>;

const statusTone: Record<Improvement["status"], ChipTone> = {
  proposed: "neutral",
  approved: "hold",
  shipped: "pass",
};

const diffMarker: Record<ImprovementDiffLine["kind"], string> = {
  add: "+",
  remove: "-",
  change: "~",
};

const diffClass: Record<ImprovementDiffLine["kind"], string> = {
  add: "text-pass",
  remove: "text-fail",
  change: "text-ink",
};

export interface ImprovementCardProps {
  improvement: Improvement;
  /** Server-derived can("ship_improvements") result; hides both actions. */
  canShip: boolean;
  approveAction?: FormAction;
  shipAction?: FormAction;
}

export function ImprovementCard({
  improvement,
  canShip,
  approveAction,
  shipAction,
}: ImprovementCardProps) {
  const { evidence } = improvement;
  return (
    <article className="space-y-3 rounded-lg border border-line bg-card p-5" aria-label={improvement.title}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-ink">{improvement.title}</h3>
        <Chip tone={statusTone[improvement.status]}>
          {improvementStatusLabels[improvement.status]}
        </Chip>
      </div>
      <p className="text-sm text-muted">{improvement.summary}</p>
      <ul className="m-0 list-none space-y-1 rounded-md border border-line-soft bg-field p-3">
        {improvement.diff.map((line) => (
          <li key={`${line.kind}:${line.text}`} className={["font-mono text-xs", diffClass[line.kind]].join(" ")}>
            <span aria-hidden="true">{diffMarker[line.kind]} </span>
            <span className="sr-only">{line.kind === "add" ? "Added:" : line.kind === "remove" ? "Removed:" : "Changed:"} </span>
            {line.text}
          </li>
        ))}
      </ul>
      <p className="font-mono text-xs text-muted">
        {testScoreEvidence(evidence.testScoreBefore, evidence.testScoreAfter, evidence.runIds.length)}
      </p>
      {canShip && improvement.status === "proposed" && approveAction && (
        <form action={approveAction}>
          <input type="hidden" name="improvementId" value={improvement.id} />
          <Button type="submit" variant="secondary">
            {improveCopy.approveAction}
          </Button>
        </form>
      )}
      {canShip && improvement.status === "approved" && shipAction && (
        <form action={shipAction}>
          <input type="hidden" name="improvementId" value={improvement.id} />
          <Button type="submit">{improveCopy.shipAction}</Button>
        </form>
      )}
    </article>
  );
}
