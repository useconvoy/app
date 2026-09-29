/** Translate only the known diagnosis contract; remote messages are not display labels. */
const COMPONENTS: Record<string, string> = {
  management: "Management service", planner: "Planner", action_policy: "Action policy",
  robot_adapter: "Robot adapter", coordinator: "Robot coordinator",
};
const PHASES: Record<string, string> = {
  mission_claim: "Claiming the mission", running_acknowledgement: "Confirming execution",
  planner_session: "Starting the planner session", planner_proposal: "Requesting a plan",
  policy_session: "Starting the action policy", adapter_initialization: "Preparing the robot or simulator",
  policy_inference: "Requesting the next action", action_admission: "Checking whether the action can run",
  adapter_step: "Applying the action", adapter_cleanup: "Closing the robot or simulator",
};
const CATEGORIES: Record<string, string> = {
  authorization: "Authorization check failed", compatibility: "Endpoint, release or session did not match",
  capacity: "Service capacity reached", deadline: "Time limit reached",
  transport: "Service connection failed or service was unavailable", protocol: "Request or response did not match the expected format",
  runtime: "Service or runtime reported a failure", cancelled: "Cancellation requested", uncertain: "Execution outcome is uncertain",
  internal: "Internal error", declined: "Planner declined the task",
};

function label(values: Record<string, string>, value: unknown) {
  return typeof value === "string" && Object.hasOwn(values, value) ? values[value] : null;
}

export function executionDiagnosis(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const report = value as Record<string, unknown>;
  if (report.schema_version !== 1 || typeof report.authorization_elapsed !== "boolean") return null;
  const component = label(COMPONENTS, report.component);
  const stage = label(PHASES, report.phase);
  const category = label(CATEGORIES, report.category);
  if (!component || !stage || !category) return null;
  return { component, stage, category,
    authorizationNotice: report.authorization_elapsed
      ? "The original mission authorization had elapsed on the robot." : null };
}
