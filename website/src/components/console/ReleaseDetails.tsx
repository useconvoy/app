import type { Release } from "@/lib/platform/client";
import { CONTROLLED_PLANNER_RUNTIME, releaseComponents } from "@/lib/platform/manifest";

const PLACEMENTS: Record<string, string> = {
  "development-local-cpu": "local development CPU",
  "development-local": "local development runtime",
  "development-jetson-lan": "development Jetson on the local network",
  "development-local-controlled": "local controlled planner fixture",
};

export function ReleaseDetails({ release }: { release: Release }) {
  const { profile, action, pairing } = releaseComponents(release.manifest);
  return <div className="console-release-details">
    <p className="console-note">Profile <code>{profile}</code><br />
      Action policy runtime <code>{action.policy.runtime}</code><br />
      {pairing && <>Planner runtime <code>{pairing.planner.runtime}</code><br /></>}
      Pinned release digest <code>{release.digest}</code>
    </p>
    {pairing && <>
      {pairing.planner.runtime === CONTROLLED_PLANNER_RUNTIME && <p className="console-note">Controlled planner fixture: deterministic fixed-skill admission, with no text-model inference.</p>}
      <p className="console-note">Configured action placement: {PLACEMENTS[pairing.placement.policy] ?? pairing.placement.policy}.<br />
        Configured planner placement: {PLACEMENTS[pairing.placement.planner] ?? pairing.placement.planner}.</p>
      <p className="console-note">The planner selects the fixed <code>{pairing.task.skill_id}</code> skill before the visual action policy runs. Task: {pairing.task.instruction}</p>
      <details><summary>Pinned component identities</summary>
        <p className="console-note">Action observation profile <code>{action.profile}</code><br />
          Action artifact <code>{action.policy.artifact_sha256}</code><br />
          Planner artifact <code>{pairing.planner.artifact_sha256}</code><br />
          Planner protocol <code>{pairing.planner.protocol_sha256}</code><br />
          Skill catalog <code>{pairing.catalog_sha256}</code><br />
          Planning timeout {pairing.planning.timeout_ms} ms.</p>
      </details>
      <p className="console-note">Placement is declared by this release. Both components must be configured separately; registration does not acknowledge readiness.</p>
    </>}
  </div>;
}
