/**
 * The claims ledger. Every product statement on the page is recorded here
 * with its stage and approval state, separately from layout, so a copy
 * review can check the page against a list rather than a rendering.
 *
 * Stage vocabulary:
 *   building   — what Convoy is building; the page may say "is building".
 *   intended   — an intended design or boundary, not shipped behavior.
 *   planned    — roadmap; must be labeled as such wherever it appears.
 *   verified   — backed by scoped evidence at `evidence`.
 */
export type ClaimStage = "building" | "intended" | "planned" | "verified";

export interface Claim {
  id: string;
  text: string;
  stage: ClaimStage;
  /** Where on the page the claim appears. */
  surface: string;
  /** Evidence URL for a verified claim, or null until evidence exists. */
  evidence: string | null;
  approved: boolean;
}

export const CLAIMS: readonly Claim[] = [
  {
    id: "hero-building",
    text: "Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers, starting with learned manipulation.",
    stage: "building",
    surface: "hero",
    evidence: null,
    approved: true,
  },
  {
    id: "workflow-building",
    text: "Package, Qualify, Release is the workflow being built around the complete robot-policy release.",
    stage: "building",
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
    text: "Execution placement depends on the task’s compute, timing, and failure requirements; workloads do not migrate on their own.",
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
    id: "availability",
    text: "The Jetson demo exposes live device telemetry and inference traces. The broader robot deployment workflow remains in development.",
    stage: "building",
    surface: "faq",
    evidence: null,
    approved: true,
  },
  {
    id: "fleet-planned",
    text: "Physical fleet rollout and general robot-policy qualification are outside the public demo and are not represented as verified capabilities.",
    stage: "planned",
    surface: "omitted from page",
    evidence: null,
    approved: true,
  },
  {
    id: "physical-text-inference",
    text: "Qwen2.5-1.5B-Instruct Q4_K_M has been verified for two-turn text Chat on the project's Jetson Orin Nano Developer Kit Super 8 GB, using llama.cpp CUDA offload on 29/29 layers, a 2,048-token context, and a 128-token output cap, with matching trace and usage evidence.",
    stage: "verified",
    surface: "faq, supported-configuration matrix",
    evidence: "https://github.com/useconvoy/app/blob/main/control-plane/docs/VERIFICATION.md#browser-chat-on-the-physical-nano--source-4df467b",
    approved: true,
  },
];
