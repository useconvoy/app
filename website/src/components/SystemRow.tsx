import { copy, grantLabels } from "@/lexicon";

import { Chip } from "./Chip";

export interface SystemRowProps {
  /** Display name of the connected system, e.g. "Okta". */
  name: string;
  grant: "read" | "write";
  /** When this row is a stand-in, the system it stands in for, e.g. "Email". */
  standInFor?: string;
}

export function SystemRow({ name, grant, standInFor }: SystemRowProps) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft py-2 last:border-b-0">
      <span className="flex min-w-0 flex-col">
        <span className="text-sm text-ink">{name}</span>
        {standInFor && <span className="text-xs text-muted">{copy.standInFor(standInFor)}</span>}
      </span>
      <Chip>{grantLabels[grant] ?? grant}</Chip>
    </li>
  );
}
