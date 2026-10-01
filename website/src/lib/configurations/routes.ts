/** Every Configurations URL in one place. Ids are route-safe (`ID_PATTERN`) or control-plane ids. */

const BASE = "/app/configurations";
const segment = (id: string) => encodeURIComponent(id);
const query = (params: Record<string, string | null | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const routes = {
  index: () => BASE,
  newConfiguration: () => `${BASE}/new`,
  configuration: (configId: string, tab?: string | null) => `${BASE}/${segment(configId)}${query({ tab })}`,
  robot: (configId: string, robotId: string, tab?: string | null) => `${BASE}/${segment(configId)}/robots/${segment(robotId)}${query({ tab })}`,
  /** `runId`: a stored run, or a control-plane evaluation (`eva_…`) or mission (`mis_…`); `rollout` opens its replay. */
  run: (configId: string, robotId: string, runId: string, rollout?: string | null) => `${BASE}/${segment(configId)}/robots/${segment(robotId)}/evals/${segment(runId)}${query({ rollout })}`,
} as const;

/**
 * A link target inside the workspace app: `/app` or a path below it, with an
 * optional query and fragment, no scheme, host, backslash, `..` segment or
 * whitespace. Document-supplied links render only when this holds.
 */
export function isInternalHref(value: unknown): value is string {
  return typeof value === "string" && value.length <= 512 && /^\/app(?:[/?#]|$)/.test(value)
    && !/[\s\\]|\/\/|(?:^|\/)\.\.(?:[/?#]|$)/.test(value) && !/[\u0000-\u001f\u007f]/.test(value);
}
