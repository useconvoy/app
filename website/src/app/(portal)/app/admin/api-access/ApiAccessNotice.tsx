/**
 * The honest phase-1 API access screen: access to the platform edge is
 * managed by Convoy through the bridge identity, so there is nothing to
 * create, reveal, or rotate here. The only fact shown is the platform
 * address; secrets never touch website code, so no key material exists
 * anywhere on this site and this component must never gain any.
 *
 * TODO(website): self-serve API keys land here when the platform edge
 * gains OIDC clients; until then this stays a notice, not a manager.
 */
import { adminCopy } from "@/lexicon";

export interface ApiAccessNoticeProps {
  controlPlaneUrl: string;
}

export function ApiAccessNotice({ controlPlaneUrl }: ApiAccessNoticeProps) {
  return (
    <div className="space-y-6">
      <section className="rounded-md border border-line bg-card p-6">
        <p className="text-sm text-ink">{adminCopy.apiAccessManaged}</p>
        <p className="mt-3 text-sm text-muted">{adminCopy.apiAccessNoKeys}</p>
        <p className="mt-1 text-sm text-muted">{adminCopy.apiAccessSelfServe}</p>
      </section>
      <section className="rounded-md border border-line bg-card p-6">
        <p className="text-xs uppercase text-muted">{adminCopy.controlPlaneUrlLabel}</p>
        <p className="mt-1 font-mono text-sm text-ink">{controlPlaneUrl}</p>
      </section>
    </div>
  );
}
