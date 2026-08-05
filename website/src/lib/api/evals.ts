/**
 * The typed client interface for the agent-evals service (scenario suites,
 * test-score trends, per-run scorecards, scored-run trajectories). The
 * service is not built yet, so the only implementation is a fixture adapter
 * over `lib/fixtures/evals`, mirroring the environments seam.
 *
 * TODO(evals): replace the fixture adapter with real calls behind the
 * single authenticated edge and delete `lib/fixtures/evals`.
 */
import "server-only";

import {
  fixtureScorecards,
  fixtureSuites,
  fixtureTrajectories,
  fixtureTrends,
} from "@/lib/fixtures/evals";

/** A scenario suite attached to one routine. */
export interface EvalSuite {
  id: string;
  routineId: string;
  name: string;
  scenarioCount: number;
}

/** One scored run on a routine's trend line; score is 0..100. */
export interface TestScorePoint {
  at: string;
  score: number;
}

/** Per-run scorecard: the criteria behind one run's test score. */
export interface RunScorecard {
  runId: string;
  score: number;
  criteria: Array<{ name: string; pass: boolean; note: string }>;
}

/** One scored run in a routine's history; rehearsals carry sandbox: true. */
export interface Trajectory {
  runId: string;
  at: string;
  sandbox: boolean;
  headline: string;
}

export interface EvalsClient {
  listSuites(routineId: string): Promise<EvalSuite[]>;
  scoreTrend(routineId: string): Promise<TestScorePoint[]>;
  scorecard(runId: string): Promise<RunScorecard | null>;
  trajectories(routineId: string): Promise<Trajectory[]>;
}

class FixtureEvalsClient implements EvalsClient {
  async listSuites(routineId: string): Promise<EvalSuite[]> {
    return fixtureSuites.filter((suite) => suite.routineId === routineId);
  }

  async scoreTrend(routineId: string): Promise<TestScorePoint[]> {
    return fixtureTrends[routineId] ?? [];
  }

  async scorecard(runId: string): Promise<RunScorecard | null> {
    return fixtureScorecards[runId] ?? null;
  }

  async trajectories(routineId: string): Promise<Trajectory[]> {
    return fixtureTrajectories[routineId] ?? [];
  }
}

/** The one evals client. TODO(evals): real adapter. */
export function evalsClient(): EvalsClient {
  return new FixtureEvalsClient();
}
