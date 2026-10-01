/** Every Configurations URL in one place. Ids are already route-safe (`ID_PATTERN`). */

const BASE = "/app/configurations";
const query = (params: Record<string, string | null | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const routes = {
  index: () => BASE,
  /** `from` preselects an existing configuration to start a new revision from. */
  newConfiguration: (from?: string | null) => `${BASE}/new${query({ from })}`,
  configuration: (configId: string, tab?: string | null) => `${BASE}/${configId}${query({ tab })}`,
  robot: (configId: string, robotId: string, params: { tab?: string | null; trace?: string | null } = {}) =>
    `${BASE}/${configId}/robots/${robotId}${query(params)}`,
  run: (configId: string, robotId: string, runId: string, params: { rollout?: string | null; tab?: string | null } = {}) =>
    `${BASE}/${configId}/robots/${robotId}/evals/${runId}${query(params)}`,
  /** Existing product areas the sidebar links to. */
  applications: () => "/app/applications",
  suites: () => "/app/applications?section=applications#evaluations-heading",
  devices: () => "/app/applications?section=device&view=device",
  chat: () => "/app/applications?section=device&view=chat",
  website: () => "/",
} as const;

/**
 * A link target inside the workspace app: `/app` or a path below it, with an
 * optional query and fragment, no scheme, host, backslash, `..` segment or
 * whitespace. Document-supplied links (compatibility actions) render only when
 * this holds.
 */
export function isInternalHref(value: unknown): value is string {
  return typeof value === "string" && value.length <= 512 && /^\/app(?:[/?#]|$)/.test(value)
    && !/[\s\\]|\/\/|(?:^|\/)\.\.(?:[/?#]|$)/.test(value) && !/[\u0000-\u001f\u007f]/.test(value);
}

/** Query parameters the pages read: tab selection and the trace / rollout drawers. */
export const QUERY = { tab: "tab", trace: "trace", rollout: "rollout", step: "step", from: "from" } as const;
