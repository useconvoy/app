/**
 * The typed shapes for the learning service (the improvements queue and
 * the evidence it carries). The service is not built yet and nothing
 * stands in for it: the learning surfaces render an honest empty queue
 * until real improvements exist, and the components that will render them
 * keep their types here.
 *
 * TODO(learning): real calls behind the single authenticated edge.
 */

export interface ImprovementDiffLine {
  kind: "add" | "remove" | "change";
  text: string;
}

export interface Improvement {
  id: string;
  routineId: string;
  title: string;
  summary: string;
  diff: ImprovementDiffLine[];
  evidence: {
    testScoreBefore: number;
    testScoreAfter: number;
    runIds: string[];
  };
  status: "proposed" | "approved" | "shipped";
  /** Set when the improvement ships; feeds the routine changelog. */
  shippedAt?: string;
}

export interface ChangelogEntry {
  routineId: string;
  at: string;
  note: string;
}
