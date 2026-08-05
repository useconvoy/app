/**
 * The routines list, presentational: plain
 * descriptors, health from the latest run, the how-it-fits strip, and no
 * "New routine" button anywhere. Phase 1 routines are set up with the
 * Convoy team; the catalog install flow lives behind the Operator lens.
 */
import Link from "next/link";

import { Chip } from "@/components/Chip";
import { HowItFitsStrip } from "@/components/HowItFitsStrip";
import { StatusChip } from "@/components/StatusChip";
import { copy } from "@/lexicon";

export interface RoutineCard {
  id: string;
  name: string;
  descriptor: string;
  systemCount: number;
  latestRunStatus: string | null;
  latestRunRehearsal: boolean;
}

export interface RoutinesListProps {
  routines: RoutineCard[];
  showHowItFits: boolean;
}

export function RoutinesList({ routines, showHowItFits }: RoutinesListProps) {
  return (
    <div className="space-y-6">
      <HowItFitsStrip showHowItFits={showHowItFits} />
      <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2">
        {routines.map((routine) => (
          <li key={routine.id}>
            <Link
              href={`/app/routines/${routine.id}`}
              className="block h-full rounded-lg border border-line bg-card p-6 hover:border-pine"
            >
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-base font-medium text-ink">{routine.name}</h2>
                {routine.latestRunStatus ? (
                  <StatusChip
                    status={routine.latestRunStatus}
                    rehearsal={routine.latestRunRehearsal}
                  />
                ) : (
                  <Chip mono>{copy.noRunsYet}</Chip>
                )}
              </div>
              <p className="mt-2 text-sm text-muted">{routine.descriptor}</p>
              <p className="mt-4 font-mono text-xs uppercase text-muted">
                {routine.systemCount} {routine.systemCount === 1 ? "system" : "systems"}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
