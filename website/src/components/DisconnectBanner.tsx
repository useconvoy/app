import { copy } from "@/lexicon";
import { friendlyDateTime } from "@/lib/format";

export interface DisconnectBannerProps {
  /** When the last event arrived; a stale surface must say so, never sit silent. */
  lastEventAt?: string | Date;
}

export function DisconnectBanner({ lastEventAt }: DisconnectBannerProps) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-md border border-hold-soft bg-hold-soft px-4 py-2 text-sm text-hold"
    >
      <span>{copy.disconnected}</span>
      {lastEventAt && (
        <span className="font-mono text-xs">{copy.lastEventAt(friendlyDateTime(lastEventAt))}</span>
      )}
    </div>
  );
}
