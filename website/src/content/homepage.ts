/**
 * The public copy of the landing page, verbatim from the approved brief
 * (Part XIII). Layout lives in the components; words live here so a copy
 * change never touches markup. Everything describes a product in
 * development: nothing on this page claims shipped functionality.
 */

export const SITE = {
  name: "Convoy",
  domain: "deployconvoy.com",
  url: "https://deployconvoy.com",
  title: "Convoy | AI Model Deployment for Robots",
  description:
    "Convoy is building deployment infrastructure for learned robot models. Connect model execution, target configuration, and release evaluation. Talk with us.",
  ogTitle: "Deploy AI models to real robots.",
  ogDescription:
    "Convoy is building the runtime and release workflow for learned robot models. Starting with manipulation.",
  category: "Deployment infrastructure for physical AI",
  tagline: "Precision Release",
  stage: "In development",
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
    "Convoy is building the runtime and release workflow that connects trained models to robot sensors, compute, and controllers—starting with learned manipulation.",
  primary: { href: "#contact", label: "Discuss your deployment" },
  secondary: { href: "#workflow", label: "Explore the workflow" },
  stage:
    "In development. We’re inviting robotics teams to help shape a focused deployment workflow.",
  caption:
    "A model is one part of the release. Convoy’s proposed workflow keeps its execution pipeline and target configuration together.",
  diagramLabel: "Illustrative architecture",
  endpoints: {
    model: { title: "Trained model", note: "weights and required assets", edge: "into the release" },
    robot: { title: "Robot controller", note: "your existing control and safety", edge: "deployed to" },
  },
} as const;

/** The release envelope: five field groups and one persistent identifier. */
export const ENVELOPE = {
  title: "Convoy release",
  /* An illustrative version tag. It is labeled as an example everywhere it
   * appears and never changes across the page, which is the point being made. */
  version: "release v0.3",
  versionNote: "example",
  fields: [
    { key: "model", label: "Model assets", note: "what was trained", items: ["policy weights", "preprocessing spec", "action space"] },
    { key: "processing", label: "Input / action processing", note: "how signals are shaped", items: ["sensor sync", "normalisation", "command limits"] },
    { key: "runtime", label: "Runtime", note: "what executes it", items: ["inference loop", "timing budget", "health checks"] },
    { key: "target", label: "Target configuration", note: "where it runs", items: ["robot config", "compute placement", "controller interface"] },
    { key: "evidence", label: "Qualification evidence", note: "why it may be released", items: ["test runs", "checks passed", "sign-off"] },
  ],
  caption: "Labels are conceptual examples. Robot controller and safety remain outside Convoy ownership.",
} as const;

export type EnvelopeFieldKey = (typeof ENVELOPE.fields)[number]["key"];

export const PROBLEM = {
  id: "problem",
  eyebrow: "The gap",
  heading: "A trained model is not a robot deployment.",
  lead: "A model can learn a task without carrying everything a robot needs to perform it.",
  body: [
    "The camera inputs must match. The actions must make sense to the controller. The runtime must fit the compute. And the resulting system needs to be evaluated for the task.",
    "When the model changes, those assumptions can change with it.",
  ],
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
  heading: "Package the system. Qualify the release.",
  lead: "We’re building a workflow around the complete robot-policy release—not the model weights alone.",
  stages: [
    {
      number: "01",
      title: "Package",
      body: "Bring the model, input and action processing, runtime dependencies, and target configuration together.",
      detail: {
        in: ["Model revision and assets", "Input and action processing definitions", "Runtime dependencies", "Target configuration"],
        out: ["One identified release"],
      },
    },
    {
      number: "02",
      title: "Qualify",
      body: "Check that release against defined task and runtime criteria. Keep the conditions and results connected to the configuration that was tested.",
      detail: {
        in: ["The identified release", "Defined task and runtime criteria", "Recorded test conditions"],
        out: ["Qualification evidence attached to that release"],
      },
    },
    {
      number: "03",
      title: "Release",
      body: "Carry the identified configuration into deployment, with a clear basis for observing behavior and managing subsequent changes.",
      detail: {
        in: ["The qualified release", "Activation preconditions for the target"],
        out: ["A deployed, identified configuration", "A basis for observation and the next revision"],
      },
    },
  ],
  stageTag: "proposed",
  badge: "Proposed workflow",
  detailLabel: "What goes in and what comes out",
  disclaimer:
    "This is the workflow we are building. Availability and support will be defined for each design partnership.",
  identityNote: "The same release identity carries through every stage.",
} as const;

export const EXECUTION = {
  id: "execution",
  eyebrow: "Runtime",
  heading: "Designed around your robot’s execution path.",
  lead: "From an observation to a controller command, the details matter.",
  path: "Sensors → Input processing → Model → Action processing → Robot controller",
  boundary:
    "Convoy’s intended runtime sits around model execution and its interfaces. Your robot’s controller and safety systems remain explicit parts of the system.",
  nodes: [
    { key: "sensors", label: "Sensors", note: "cameras · joints · force", site: "robot", owner: "robot", optional: false },
    { key: "input", label: "Input processing", note: "sync · normalise", site: "local · site · cloud", owner: "convoy", optional: true },
    { key: "model", label: "Model", note: "learned policy", site: "local · site · cloud", owner: "convoy", optional: true, model: true },
    { key: "action", label: "Action processing", note: "limits · rates", site: "local", owner: "convoy", optional: false },
    { key: "controller", label: "Robot controller", note: "outside Convoy", site: "robot · safety", owner: "robot", optional: false },
  ],
  edges: ["observations", "tensors", "model outputs", "commands"],
  feedback: "new observations / robot state",
  scope: "Convoy runtime scope · placement is configuration-dependent",
  legend: [
    { kind: "execution", label: "Execution" },
    { kind: "release", label: "Release · configuration · evidence" },
    { kind: "optional", label: "Optional, configuration-dependent placement" },
  ],
  diagramLabel:
    "Conceptual execution path. Placement of processing and model differs per configuration; repeatability of the release, not identical physical behaviour, is the goal. Not a hard real-time guarantee.",
  placement: {
    question: "Where does inference run?",
    answer:
      "That depends on the task. Robot-local, site-local, and cloud execution have different compute, timing, and failure requirements. Placement should be part of a defined deployment configuration—not an assumption.",
    options: [
      { label: "Robot-local compute", note: "onboard the machine" },
      { label: "Site-local compute", note: "on the same network as the robot" },
      { label: "Cloud compute", note: "remote, with the network in the loop" },
    ],
    rule: "Choose a defined path against the task’s measured requirements. These are not automatically migrating workloads or interchangeable targets.",
  },
  repeatable: {
    question: "What makes a release repeatable?",
    answer:
      "The model, processing, dependencies, and target settings need an identifiable configuration. Reproducing that configuration does not guarantee identical behavior in a changing physical environment.",
  },
  boundaryDisclosure: {
    label: "Explore the release boundary",
    intro:
      "What one identified release keeps together. Field groups are shown as an illustrative example; this is not a product schema or an API.",
    panelLabel: "Release record · conceptual",
    panelStatus: "not an API",
    panelFooter: "Field names are illustrative. They show what a release must carry, not a schema or command.",
    fields: [
      { key: "release", value: `${ENVELOPE.version.replace("release ", "")} · ${ENVELOPE.versionNote}`, note: "illustrative identifier" },
      { key: "model", value: "policy weights and required assets, by revision" },
      { key: "inputs", value: "which sensors, how frames and state are prepared" },
      { key: "actions", value: "output meaning: units, frame, and ordering for the controller" },
      { key: "runtime", value: "inference runtime and pinned dependencies" },
      { key: "target", value: "compute, drivers, and the controller interface" },
      { key: "evidence", value: "criteria, conditions, and results for this release" },
    ],
    note: "A policy here means a trained model that maps observations to actions. The record ties each of these to one release so a change in any of them is visible.",
  },
} as const;

export const PARTNERSHIP = {
  id: "partnership",
  eyebrow: "Design partnership",
  heading: "Start with one model. One robot configuration. One clear deployment goal.",
  body: [
    "We’re looking for robotics teams working on learned manipulation: picking, packing, sorting, assembly, and related tasks.",
    "A useful starting point is a trained policy, a defined robot setup, and a concrete deployment problem—such as changing model interfaces, target-runtime constraints, or a release process that is difficult to reproduce.",
    "Together, we can define what the deployment includes, how it should be evaluated, and what a successful integration would need to demonstrate.",
  ],
  fitTitle: "A good fit usually has",
  fit: [
    { label: "A trained policy", note: "a model that already performs the task in some setting" },
    { label: "A defined robot setup", note: "the arm, sensors, compute, and controller you deploy to" },
    { label: "A concrete deployment problem", note: "the part that is getting in the way today" },
  ],
  cta: { href: "#contact", label: "Discuss your deployment" },
  note: "We do not assume that every model or robot configuration is supported. Compatibility and scope come first.",
} as const;

export const FAQ = {
  id: "faq",
  eyebrow: "Boundaries",
  heading: "A few important boundaries.",
  items: [
    {
      question: "Is Convoy a robot manufacturer?",
      answer: "No. Convoy is building software infrastructure for deploying learned models onto robots.",
    },
    {
      question: "Does Convoy train the model?",
      answer: "Our initial focus is deployment: the execution pipeline and release workflow around a trained policy.",
    },
    {
      question: "Will it work with any model or robot?",
      answer:
        "No universal compatibility is assumed. Support must be defined for the model, processing pipeline, runtime, hardware, and controller configuration.",
    },
    {
      question: "Does inference have to run in the cloud?",
      answer:
        "No. The appropriate placement depends on the target system and task. We are not promising automatic movement between edge and cloud.",
    },
    {
      question: "Does Convoy replace the robot’s controller or safety system?",
      answer:
        "No. Those responsibilities need explicit interfaces and boundaries. Release evaluation is not a substitute for the robot’s safety system or a safety certification.",
    },
    {
      question: "What can I use today?",
      answer:
        "Convoy is in development. The next step is a technical conversation about your deployment needs and whether a focused design partnership is a fit.",
    },
  ],
} as const;

export const CONTACT = {
  id: "contact",
  eyebrow: "Contact",
  heading: "What does your next robot deployment need?",
  lead: "Tell us about the model, the robot, and the part that is getting in the way.",
  formTitle: "Discuss your deployment",
  fields: {
    email: { label: "Work email", required: true },
    company: { label: "Company or team", required: true },
    blocker: { label: "What are you trying to deploy, and what is blocking you?", required: true },
    name: { label: "Name", required: false },
    hardware: { label: "Model and hardware", required: false },
  },
  optionalLabel: "Optional details",
  privacyNote:
    "Please do not include proprietary model files, credentials, or sensitive operational data.",
  submit: "Send deployment details",
  states: {
    success: "Your deployment details have been received.",
    error: "Your details could not be sent. Please try again. Your message is still here.",
    /* The preview state: no verified delivery destination exists yet, so the
     * form never claims receipt and never stores what was typed. */
    unavailableTitle: "Sending is not available yet",
    unavailable:
      "This form is in preview while a receiving inbox is being set up. Nothing you enter here is sent or stored. Your details stay in the form.",
    unavailableAfterSubmit:
      "Your details were not sent, because sending is not available yet. Nothing was stored. Your message is still here.",
    invalidSummary: "Please check the highlighted fields.",
  },
} as const;

export const FOOTER = {
  wordmark: SITE.name,
  category: "Deployment infrastructure for physical AI.",
  links: NAV.links,
  domain: SITE.domain,
  copyright: (year: number) => `© ${year} Convoy`,
} as const;
