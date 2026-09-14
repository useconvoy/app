/**
 * The public copy of the landing page. Layout lives in the components; words
 * live here so a copy change never touches markup. Everything describes a
 * product in development: nothing on this page claims shipped functionality,
 * pass states, timing, compatibility, or customers.
 */

/** The inbox inquiries go to. Override at build time with NEXT_PUBLIC_CONTACT_EMAIL. */
export const CONTACT_EMAIL = process.env.NEXT_PUBLIC_CONTACT_EMAIL?.trim() || "founders@deployconvoy.com";

export const SITE = {
  name: "Convoy",
  domain: "deployconvoy.com",
  url: "https://deployconvoy.com",
  /** The one canonical form of the home page: apex host, https, trailing slash. Sitemap, canonical, og:url and JSON-LD all use it. */
  canonical: "https://deployconvoy.com/",
  title: "Convoy | AI Model Deployment for Robots",
  description:
    "Convoy is building deployment infrastructure for physical AI, connecting trained models to robot sensors, compute, and controllers.",
  ogTitle: "Convoy — Deploy AI models to real robots",
  ogDescription:
    "Convoy is building deployment infrastructure for physical AI, connecting trained models to robot sensors, compute, and controllers.",
  category: "Deployment infrastructure for physical AI",
  stage: "In development",
} as const;

/**
 * Static brand assets under public/. Every path carries a content version in
 * its file name, so replacing the artwork means a new URL and link-preview
 * caches (Facebook, LinkedIn, Slack, X) pick up the new card instead of
 * serving the one they stored. Bump the version when the artwork changes.
 */
export const BRAND_VERSION = "2026-09";
/** The link-preview card has its own version: replacing the artwork must not move the stable favicon and icon URLs. */
export const SHARE_VERSION = "2026-09-2";
export const BRAND = {
  /** Vector favicon for browsers that take one: the open release frame, a paper C on oxide. */
  iconSvg: `/icons/convoy-mark-${BRAND_VERSION}.svg`,
  /** Classic favicon container with 16, 32 and 48 px renderings inside. Served at the root, where browsers and crawlers look first. */
  favicon: "/favicon.ico",
  /** Stable square PNG for search-result favicons (Google asks for a multiple of 48 px, at least 48). */
  icon96: `/icons/convoy-mark-${BRAND_VERSION}-96.png`,
  icon192: `/icons/convoy-mark-${BRAND_VERSION}-192.png`,
  icon512: `/icons/convoy-mark-${BRAND_VERSION}-512.png`,
  appleTouch: `/icons/convoy-mark-${BRAND_VERSION}-180.png`,
  /** The link-preview card, 1200 × 630. Earlier versions stay under public/share/ for pages that cached them. */
  shareCard: `/share/convoy-card-${SHARE_VERSION}.png`,
  shareCardAlt:
    "Convoy mark and wordmark in paper on an oxide field, with the line “Deploy AI models to real robots.” and the domain deployconvoy.com.",
} as const;

export const NAV = {
  links: [
    { href: "#workflow", label: "How it works" },
    { href: "#partnership", label: "Design partnership" },
    { href: "#contact", label: "Contact" },
  ],
  cta: { href: "#contact", label: "Discuss your deployment" },
} as const;

export const HERO = {
  eyebrow: SITE.category,
  headline: "Deploy AI models to real robots.",
  lede:
    "Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers, starting with learned manipulation.",
  primary: { href: "#contact", label: "Discuss your deployment" },
  secondary: { href: "#workflow", label: "Explore the workflow" },
  diagramHeading: "The robot-policy release",
  caption:
    "Conceptual architecture: a robot-policy release brings together the model, input and action processing, runtime, target configuration, and evaluation evidence.",
  endpoints: {
    model: { title: "Trained model", note: "weights and required assets", ownership: "your team", edge: "into the release" },
    robot: { title: "Robot controller", note: "your existing control and safety", ownership: "outside Convoy", edge: "deployed to" },
  },
  description:
    "A trained model, delivered by your team, enters the robot-policy release. The release is the dashed boundary Convoy is building: release identity, model assets, input and action processing, runtime, target configuration, and evaluation evidence. The release hands processed actions to the robot and its existing controller and safety system, which remain outside Convoy.",
} as const;

/** The release envelope: five field groups under one release identity. */
export const ENVELOPE = {
  label: "Robot-policy release",
  identityLabel: "Release identity",
  identityNote: "name · configuration · evidence reference",
  detailsLabel: "See release details",
  detailsIntro: "Examples of what each part of a release covers. Names are conceptual, not a schema.",
  fields: [
    { key: "model", label: "Model assets", note: "the trained policy", items: ["policy weights", "preprocessing spec", "action space"] },
    { key: "processing", label: "Input / action processing", note: "how signals are shaped", items: ["sensor alignment", "normalisation", "command translation"] },
    { key: "runtime", label: "Runtime", note: "what executes it", items: ["inference loop", "dependencies", "timing budget"] },
    { key: "target", label: "Target configuration", note: "where it runs", items: ["robot configuration", "compute placement", "controller interface"] },
    { key: "evidence", label: "Evaluation evidence", note: "why it may be released", items: ["task criteria", "runtime criteria", "tested configuration", "conditions and results"] },
  ],
} as const;

export type EnvelopeFieldKey = (typeof ENVELOPE.fields)[number]["key"];

export const PROBLEM = {
  id: "problem",
  eyebrow: "The gap",
  heading: "A trained model is not a robot deployment.",
  lead: "A model can learn a task without carrying everything a robot needs to perform it. When the model changes, those assumptions change with it.",
  columnLabel: "What the deployment must carry",
  dependencies: [
    {
      label: "Inputs",
      body: "Images, sensor data, and robot state prepared the way the model expects.",
      field: "processing",
    },
    {
      label: "Actions",
      body: "Model outputs translated into the units, coordinates, and commands the controller understands.",
      field: "processing",
    },
    {
      label: "Execution",
      body: "The runtime, dependencies, hardware settings, and timing required by the deployment.",
      field: "runtime",
    },
    {
      label: "Release evidence",
      body: "A record of the configuration, conditions, and checks used to evaluate the system.",
      field: "evidence",
    },
  ] satisfies ReadonlyArray<{ label: string; body: string; field: EnvelopeFieldKey }>,
} as const;

export const WORKFLOW = {
  id: "workflow",
  eyebrow: "How it works",
  heading: "Package. Qualify. Release.",
  lead: "One release identity carries the model, processing, runtime, target configuration, and evidence through every step.",
  stages: [
    {
      number: "01",
      title: "Package",
      body: "Bring the model, input and action processing, runtime dependencies, and target configuration together as one identified release.",
      detail: {
        in: ["Model revision and assets", "Input and action processing definitions", "Runtime dependencies", "Target configuration"],
        out: ["One identified release"],
      },
    },
    {
      number: "02",
      title: "Qualify",
      body: "Check that release against defined task and runtime criteria, and keep the conditions and results attached to it.",
      detail: {
        in: ["The identified release", "Defined task and runtime criteria", "Recorded test conditions"],
        out: ["Evaluation evidence attached to that release"],
      },
    },
    {
      number: "03",
      title: "Release",
      body: "Carry the identified configuration into deployment, with a clear basis for observing behavior and managing the next change.",
      detail: {
        in: ["The qualified release", "Activation preconditions for the target"],
        out: ["A deployed, identified configuration", "A basis for observation and the next revision"],
      },
    },
  ],
  detailLabel: (title: string) => `What goes in and out of ${title}`,
} as const;

export const EXECUTION = {
  id: "execution",
  eyebrow: "Runtime",
  heading: "Designed around your robot’s execution path.",
  lead: "From an observation to a controller command, the details matter.",
  path: "Sensors → Input processing → Model → Action processing → Robot controller",
  boundary:
    "Convoy’s intended runtime covers input processing, the model, and action processing. Your controller and safety systems stay outside it.",
  nodes: [
    { key: "sensors", label: "Sensors", note: "cameras · joints · force", site: "on the robot", owner: "robot", optional: false },
    { key: "input", label: "Input processing", note: "align · normalise", site: "placement varies", owner: "convoy", optional: true },
    { key: "model", label: "Model", note: "learned policy", site: "placement varies", owner: "convoy", optional: true, model: true },
    { key: "action", label: "Action processing", note: "translate · limit", site: "near the controller", owner: "convoy", optional: false },
    { key: "controller", label: "Robot controller", note: "controller · safety system", site: "on the robot", owner: "robot", optional: false },
  ],
  edges: ["observations", "tensors", "model outputs", "commands"],
  feedback: "new observations / robot state",
  scope: "Convoy runtime boundary",
  description:
    "Sensors, then Input processing, then Model, then Action processing, then Robot controller. Convoy runtime boundary: Input processing, Model, Action processing. Outside Convoy: Sensors and Robot controller.",
  legend: [
    { kind: "execution", label: "Execution" },
    { kind: "release", label: "Release · configuration · evidence" },
    { kind: "optional", label: "Configuration-dependent placement" },
  ],
  caption:
    "Convoy’s intended runtime covers input processing, the model, and action processing; your controller and safety systems stay outside it. Where each step runs depends on the task’s compute, timing, and failure requirements.",
  placement: {
    question: "Where does inference run?",
    answer:
      "Robot-local, site-local, and cloud execution differ in compute, timing, and failure behavior, so placement is part of a defined deployment configuration rather than an assumption.",
    options: [
      { label: "Robot-local compute", note: "onboard the machine" },
      { label: "Site-local compute", note: "on the same network as the robot" },
      { label: "Cloud compute", note: "remote, with the network in the loop" },
    ],
    rule: "Each path is chosen against the task’s measured requirements. Workloads do not migrate between them on their own.",
  },
  repeatable: {
    question: "What makes a release repeatable?",
    answer:
      "The model, processing, dependencies, and target settings need an identifiable configuration. Reproducing that configuration does not guarantee identical behavior in a changing physical environment.",
  },
  boundaryDisclosure: {
    label: "Explore the release boundary",
    intro: "What one identified release keeps together.",
    panelLabel: "Release contents · conceptual categories",
    panelStatus: "not a schema",
    panelFooter: "Category names are illustrative. They show what a release carries, not an API, schema, or command.",
    groups: [
      { title: "release identity", rows: [{ key: "release", value: "name and configuration reference" }] },
      {
        title: "model assets",
        rows: [
          { key: "policy", value: "artifact and preprocessing specification" },
          { key: "action_space", value: "units, coordinates, and command form" },
        ],
      },
      {
        title: "input and action processing",
        rows: [
          { key: "inputs", value: "sensor set and alignment" },
          { key: "limits", value: "command bounds and translation" },
        ],
      },
      {
        title: "runtime and target",
        rows: [
          { key: "runtime", value: "dependencies and timing budget" },
          { key: "target", value: "robot configuration and compute placement" },
        ],
      },
      {
        title: "evaluation evidence",
        rows: [
          { key: "criteria", value: "task and runtime criteria" },
          { key: "results", value: "conditions and results for the tested configuration" },
        ],
      },
    ],
    note: "A policy here means a trained model that maps observations to actions. One release ties each of these together so a change in any of them is visible.",
  },
} as const;

export const PARTNERSHIP = {
  id: "partnership",
  eyebrow: "Design partnership",
  heading: "Start with one deployment.",
  lead: "We’re speaking with robotics teams working on learned manipulation: picking, packing, sorting, assembly, and related tasks. Each potential partnership starts with a defined model, robot configuration, and deployment goal.",
  fitTitle: "A good fit usually has",
  fit: [
    { label: "A trained policy", note: "a model that already performs the task in some setting" },
    { label: "A defined robot setup", note: "the arm, sensors, compute, and controller you deploy to" },
    { label: "A concrete deployment problem", note: "the part that is getting in the way today" },
  ],
  cta: { href: "#contact", label: "Discuss your deployment" },
} as const;

export const FAQ = {
  id: "faq",
  eyebrow: "FAQ",
  heading: "Questions about Convoy",
  items: [
    {
      question: "What can I use today?",
      answer:
        "Convoy is in development. We’re speaking with robotics teams about focused design partnerships, with compatibility and scope defined around each deployment.",
    },
    {
      question: "Will it work with any model or robot?",
      answer:
        "No universal compatibility is assumed. Support must be defined for the model, processing pipeline, runtime, hardware, and controller configuration.",
    },
    {
      question: "Does Convoy replace the robot’s controller or safety system?",
      answer:
        "No. Those responsibilities need explicit interfaces and boundaries. Release evaluation is not a substitute for the robot’s safety system or a safety certification.",
    },
    {
      question: "Does inference have to run in the cloud?",
      answer: "No. The appropriate placement depends on the target system and task. We are not promising automatic movement between edge and cloud.",
    },
    {
      question: "Does Convoy train the model?",
      answer: "Our initial focus is deployment: the execution pipeline and release workflow around a trained policy.",
    },
    {
      question: "Is Convoy a robot manufacturer?",
      answer: "No. Convoy is building software infrastructure for deploying learned models onto robots.",
    },
  ],
} as const;

export const CONTACT = {
  id: "contact",
  eyebrow: "Contact",
  heading: "Tell us about your next robot deployment.",
  lead: "Share your model, robot configuration, and the deployment challenge you’re working through.",
  email: CONTACT_EMAIL,
  subject: "Convoy deployment inquiry",
  body: "Model:\n\nRobot configuration:\n\nDeployment challenge:\n",
  button: "Email us about your deployment",
  opens: "Opens your email app with the subject filled in.",
  addressLabel: "Email",
  guidanceIntro: "Helpful to include",
  guidance: [
    { label: "Model", note: "what it does and its interfaces" },
    { label: "Robot configuration", note: "arm, sensors, compute, controller" },
    { label: "Deployment challenge", note: "what blocks the next release" },
  ],
  privacyNote: "Please do not include proprietary model files, credentials, or sensitive operational data.",
} as const;

/** The mailto link, assembled once so the subject and template stay in step with the copy. */
export const CONTACT_MAILTO = `mailto:${CONTACT.email}?subject=${encodeURIComponent(CONTACT.subject)}&body=${encodeURIComponent(CONTACT.body)}`;

export const FOOTER = {
  wordmark: SITE.name,
  category: "Deployment infrastructure for physical AI.",
  links: NAV.links,
  domain: SITE.domain,
  copyright: (year: number) => `© ${year} Convoy`,
  /** The visitor's switch for the decorative motion across the illustrated hero. */
  motion: { pause: "Pause motion", resume: "Resume motion" },
} as const;
