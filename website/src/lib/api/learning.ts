/**
 * The typed client interface for the learning service (the improvements
 * queue and the routine changelog it feeds). The service is not built yet,
 * so the only implementation is a fixture adapter whose in-process store is
 * seeded from `lib/fixtures/learning`; approve and ship mutate the store so
 * state changes render across requests, mirroring the environments seam.
 *
 * TODO(learning): replace the fixture adapter with real calls behind the
 * single authenticated edge and delete the in-process store.
 */
import "server-only";

import { fixtureChangelog, fixtureImprovements } from "@/lib/fixtures/learning";

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

export interface LearningClient {
  listImprovements(orgId: string): Promise<Improvement[]>;
  getImprovement(id: string): Promise<Improvement | null>;
  approveImprovement(id: string): Promise<Improvement>;
  shipImprovement(id: string): Promise<Improvement>;
  /** Baseline changelog plus shipped improvements, newest first. */
  listChangelog(routineId: string): Promise<ChangelogEntry[]>;
}

declare global {
  var __convoyImprovementStore: Map<string, Improvement> | undefined;
}

/** Improvement state, kept in process until the service exists. */
function store(): Map<string, Improvement> {
  if (!globalThis.__convoyImprovementStore) {
    globalThis.__convoyImprovementStore = new Map(
      fixtureImprovements.map((improvement) => [improvement.id, { ...improvement }]),
    );
  }
  return globalThis.__convoyImprovementStore;
}

class FixtureLearningClient implements LearningClient {
  async listImprovements(): Promise<Improvement[]> {
    // The fixture world is single-org; the real service scopes by org.
    return [...store().values()];
  }

  async getImprovement(id: string): Promise<Improvement | null> {
    return store().get(id) ?? null;
  }

  async approveImprovement(id: string): Promise<Improvement> {
    const improvement = store().get(id);
    if (!improvement) throw new Error("That improvement no longer exists");
    if (improvement.status !== "proposed") {
      throw new Error("Only a proposed improvement can be approved");
    }
    improvement.status = "approved";
    return improvement;
  }

  async shipImprovement(id: string): Promise<Improvement> {
    const improvement = store().get(id);
    if (!improvement) throw new Error("That improvement no longer exists");
    if (improvement.status !== "approved") {
      throw new Error("Only an approved improvement can be shipped");
    }
    improvement.status = "shipped";
    improvement.shippedAt = new Date().toISOString();
    return improvement;
  }

  async listChangelog(routineId: string): Promise<ChangelogEntry[]> {
    const baseline: ChangelogEntry[] = fixtureChangelog
      .filter((entry) => entry.routineId === routineId)
      .map(({ routineId: id, at, note }) => ({ routineId: id, at, note }));
    const shipped: ChangelogEntry[] = [...store().values()]
      .filter((improvement) => improvement.routineId === routineId && improvement.status === "shipped")
      .map((improvement) => ({
        routineId: improvement.routineId,
        at: improvement.shippedAt ?? new Date().toISOString(),
        note: improvement.title,
      }));
    return [...baseline, ...shipped].sort((a, b) => (a.at < b.at ? 1 : -1));
  }
}

/** The one learning client. TODO(learning): real adapter. */
export function learningClient(): LearningClient {
  return new FixtureLearningClient();
}
