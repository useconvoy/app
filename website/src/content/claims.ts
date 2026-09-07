/**
 * The claims ledger. Every product statement on the page is recorded here
 * with its stage and approval state, separately from layout, so a copy
 * review can check the page against a list rather than a rendering.
 *
 * Stage vocabulary:
 *   building   — what Convoy is building; the page may say "is building".
 *   intended   — an intended design or boundary, not shipped behavior.
 *   planned    — roadmap; must be labeled Planned wherever it appears.
 *   verified   — backed by evidence at `evidence`; none exist yet.
 */
export type ClaimStage = "building" | "intended" | "planned" | "verified";

export interface Claim {
  id: string;
  text: string;
  stage: ClaimStage;
  /** Where on the page the claim appears. */
  surface: string;
  /** URL of the evidence that would move the claim to `verified`. */
  evidence: string | null;
  approved: boolean;
}

export const CLAIMS: readonly Claim[] = [
  {
    id: "hero-building",
    text: "Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers—starting with learned manipulation.",
    stage: "building",
    surface: "hero",
    evidence: null,
    approved: true,
  },
  {
    id: "workflow-intended",
    text: "Package, Qualify, Release is the workflow being built; availability and support are defined per design partnership.",
    stage: "intended",
    surface: "workflow",
    evidence: null,
    approved: true,
  },
  {
    id: "execution-boundary",
    text: "Convoy’s intended runtime sits around model execution and its interfaces; the robot’s controller and safety systems remain outside it.",
    stage: "intended",
    surface: "execution",
    evidence: null,
    approved: true,
  },
  {
    id: "placement-conditional",
    text: "Robot-local, site-local, and cloud placement are defined per deployment configuration, never assumed or automatically migrated.",
    stage: "intended",
    surface: "execution",
    evidence: null,
    approved: true,
  },
  {
    id: "no-universal-compat",
    text: "No universal compatibility is assumed; support must be defined per model, processing pipeline, runtime, hardware, and controller configuration.",
    stage: "intended",
    surface: "faq, partnership",
    evidence: null,
    approved: true,
  },
  {
    id: "fleet-planned",
    text: "Fleet rollout, monitoring, and evaluation of updates are roadmap and do not appear as current capability.",
    stage: "planned",
    surface: "omitted from page",
    evidence: null,
    approved: true,
  },
];
