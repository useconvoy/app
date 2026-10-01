import { expect, type Page } from "@playwright/test";
import { buildRevision, EMPTY_INPUT } from "../../../src/lib/configurations/create";
import { addRobot, createConfiguration, emptyWorkspace } from "../../../src/lib/configurations/mutations";
import { CONFIGURED_DEVICE } from "../../../src/lib/configurations/types";
import type { ConvoyWorkspace } from "../../../src/lib/configurations/types";
import type { PortalSnapshot } from "../../../src/lib/portal/types";
import { mockDocuments, type DocumentServer } from "./documents";

/*
 * Contract fixtures for the Configurations e2e suites: a contract account and
 * device, contract control-plane ids (project, evaluations, missions, episodes)
 * and a 1 × 1 PNG frame. No real account data.
 */
export const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
export const PROJECT = "prj_contract01";
export const PLATFORM_ROBOT = "rob_contract01";

export function snapshot(options: { online?: boolean } = {}): PortalSnapshot {
  const online = options.online ?? true;
  const now = Date.now();
  const at = (offset = 0) => new Date(now + offset).toISOString();
  const telemetry = Array.from({ length: 8 }, (_, i) => ({ ts: at((i - 7) * 15000), cpu_pct: 20 + i, gpu_pct: 8, mem_total_mb: 7620, mem_available_mb: 3500 + i, power_w: 7.5 + i * 0.1, temp_max_c: 45 + i * 0.3, disk_free_mb: 20000, runtime_state: "running", clock_confidence: "unknown" }));
  return {
    fetched_at: at(), telemetry_stale_after_s: 90, heartbeat_interval_s: 15,
    device: { id: "dev_contract", name: "Contract Jetson", status: online ? "online" : "offline", live_at: at(online ? -2000 : -600_000), observed_at: at(-2000), observed_health: "ok", observed_stage: "ready", agent_version: "contract-version", observed_active_release_id: "rel_contract", gateway_mode: "production", runtime_state: "running" },
    release: { id: "rel_contract", name: "Contract model", version: "1", digest: "contract-digest", model_repo: "contract/model", model_file: "contract.gguf", runtime_name: "llama.cpp", runtime_backend: "cuda", context_window: 2048, output_limit: 128 },
    telemetry, latest_telemetry: telemetry[7],
    usage: { from: "2026-09-01", to: "2026-09-30", metrics: null },
    recent_inference: [180.5, 210.25, 650].map((latency, i) => ({ trace_id: `tr_contract_${i}`, start_ts: at(-i * 20000), status: "ok", latency_ms: latency, ttft_ms: 60, queue_ms: 0, tokens_in: 110, tokens_out: 8, tok_s: 57.5 })),
    chat: { eligible: online, online, reason: online ? null : "Device is offline.", release_id: "rel_contract", max_tokens: 128, context_window: 2048 },
  };
}

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
interface Case { seed: number; episode: string; passed: boolean }
export function evaluation(id: string, minutes: number, cases: Case[], options: { robot?: string; project?: string } = {}) {
  return {
    id, project_id: options.project ?? PROJECT, suite_id: "esu_contract01", release_id: "apr_contract01", robot_id: options.robot ?? PLATFORM_ROBOT, deployment_id: "dep_contract01",
    state: "completed", detail: "all allocated cases have terminal outcomes", created_at: minutesAgo(minutes), updated_at: minutesAgo(minutes - 1),
    cases: cases.map((item, position) => ({ position, seed: item.seed, mission_id: `mis_${item.episode}`, episode_id: item.episode })),
    report: {
      schema_version: 1, suite_digest: "suite-digest", release_digest: "release-digest", scorer: "final-success-v1", case_count: cases.length, successes: cases.filter(item => item.passed).length,
      min_successes: 1, passed: cases.some(item => item.passed), median_wall_s: 41.21, scope: "lockstep_simulation; no hardware or real-time qualification",
      cases: cases.map(item => ({ seed: item.seed, mission_id: `mis_${item.episode}`, episode_id: item.episode, state: "completed", passed: item.passed, evidence_valid: true, steps: 54, wall_duration_s: 41.21 })),
    },
  };
}
export function mission(id: string, episode: string | null, minutes: number, options: { robot?: string } = {}) {
  return { id, project_id: PROJECT, robot_id: options.robot ?? PLATFORM_ROBOT, deployment_id: "dep_contract01", release_id: "apr_contract01", state: "completed", detail: "benchmark success reached", episode_id: episode, seed: 0, expires_at: 0, created_at: minutesAgo(minutes), updated_at: minutesAgo(minutes - 1) };
}
/** Two evaluations (one case each) and two missions run outside them: four replayable episodes. */
export const EVALUATIONS = [
  evaluation("eva_contract02", 98, [{ seed: 0, episode: "epi_contract02", passed: true }]),
  evaluation("eva_contract01", 99, [{ seed: 0, episode: "epi_contract01", passed: true }]),
  evaluation("eva_elsewhere", 97, [{ seed: 3, episode: "epi_elsewhere", passed: false }], { project: "prj_elsewhere" }),
];
export const MISSIONS = [
  mission("mis_epi_contract02", "epi_contract02", 98), mission("mis_epi_contract01", "epi_contract01", 99),
  mission("mis_contract03", "epi_contract03", 101), mission("mis_contract04", "epi_contract04", 95),
];

export interface PlatformLog { frames: number[]; paths: string[] }
export interface MockOptions { signedIn?: boolean; document?: unknown | null; revision?: number; online?: boolean; device?: "online" | "unavailable"; missingRecording?: string }

/** The account, its device, the control plane's projects, evaluations, missions and episodes, and the documents API. */
export async function mockApi(page: Page, options: MockOptions = {}): Promise<{ documents: DocumentServer; platform: PlatformLog }> {
  let signedIn = options.signedIn ?? true;
  const platform: PlatformLog = { frames: [], paths: [] };
  const account = { user: { email: "fixture@example.test", role: "operator" }, installation: { simulator: true, dispatch_paused_at: null, quarantined_at: null } };
  const json = (value: unknown) => ({ json: value });
  const id = (url: string, back = 1) => new URL(url).pathname.split("/").at(-back) ?? "";
  await page.route("**/api/platform/auth/me", route => route.fulfill(signedIn ? json(account) : { status: 401, json: { error: "Sign in" } }));
  await page.route("**/api/platform/auth/login", async route => { signedIn = true; await route.fulfill(json({ user: account.user })); });
  await page.route("**/api/platform/auth/logout", async route => { signedIn = false; await route.fulfill(json({ ok: true })); });
  await page.route("**/api/portal/snapshot", route => options.device === "unavailable"
    ? route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "The device service is not configured." } } })
    : route.fulfill(json(snapshot({ online: options.online }))));
  await page.route(/\/api\/platform\/(?!workspace-documents|auth)/, route => {
    const url = route.request().url();
    const path = new URL(url).pathname.replace(/^\/api\/platform\//, "") + new URL(url).search;
    platform.paths.push(path);
    if (/^devices$/.test(path)) return route.fulfill(json([{ id: "dev_contract", name: "Contract Jetson", simulated: false, status: "online" }, { id: "dev_contract_sim", name: "Contract runner", simulated: true, status: "online" }]));
    if (/^devices\//.test(path)) return route.fulfill(json({ id: "dev_contract", name: "Contract Jetson", status: "online", hardware: { jetson_model: "Contract Jetson board", l4t_release: "36.4", cuda_version: "12.6" }, last_telemetry: {} }));
    if (path === "projects") return route.fulfill(json([{ id: PROJECT, name: "Contract project" }]));
    if (path.startsWith("robots?")) return route.fulfill(json([{ id: PLATFORM_ROBOT, project_id: PROJECT, device_id: "dev_contract_sim", name: "Contract simulator", profile: "contract", generation: 1, evaluation_id: null }]));
    if (path.startsWith("evaluations?")) return route.fulfill(json(EVALUATIONS.filter(item => path.includes(item.project_id))));
    if (path.startsWith("missions?")) return route.fulfill(json(MISSIONS));
    if (/^evaluations\/[^/]+$/.test(path)) { const found = EVALUATIONS.find(item => item.id === id(url)); return found ? route.fulfill(json(found)) : route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } }); }
    if (/^missions\/[^/]+$/.test(path)) { const found = MISSIONS.find(item => item.id === id(url)); return found ? route.fulfill(json(found)) : route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } }); }
    if (/^episodes\/[^/]+\/replay$/.test(path)) {
      const episode = id(url, 2);
      if (episode === options.missingRecording) return route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } });
      return route.fulfill(json({ episode_id: episode, mission_id: "mis_contract", release_digest: "contract-release-digest", steps: 54, skill: "pick_place_puck", planner_ms: 917.78, wall_seconds: 41.21, sim_seconds: 0.675, source: "Recorded coordinator camera observations and applied actions" }));
    }
    if (/^episodes\/[^/]+\/replay\/frames\/\d+$/.test(path)) {
      const index = Number(id(url));
      platform.frames.push(index);
      return route.fulfill(json({ index, image_png_base64: PNG, action: index ? [0.12, -0.4, 0.05, 1] : null, reward: index ? 0.25 : null, success: index ? false : null, policy_ms: index ? 512.3 : null }));
    }
    if (/^episodes\/[^/]+$/.test(path)) return route.fulfill(json({ id: id(url), mission_id: "mis_contract", state: "completed", detail: "", release_digest: "contract-release-digest", summary: { final_success: true, steps: 54 } }));
    return route.fulfill({ status: 404, json: { error: "This console action is unavailable." } });
  });
  const documents = await mockDocuments(page, { document: options.document ?? null, revision: options.revision });
  return { documents, platform };
}

/**
 * An account's own workspace as the UI builds it: three configurations; the first
 * two test robots on the workspace's device, the third a simulator whose evals come
 * from the contract project.
 */
export function demoDocument(): ConvoyWorkspace {
  const at = Date.now() - 3_600_000;
  let ws = emptyWorkspace(at);
  const make = (name: string, input: Partial<typeof EMPTY_INPUT>) => {
    const out = createConfiguration(ws, { name, revision: buildRevision({ ...EMPTY_INPUT, name, robot: "Arm", ...input }, at) }, at);
    ws = out.workspace;
    return out.configuration.id;
  };
  const planner = make("Arm · Edge planner", { edgeModel: "Qwen2.5-1.5B-Instruct Q4_K_M" });
  const cloud = make("Arm · Cloud", { cloudModel: "Hosted planner" });
  const vla = make("Arm · Edge VLA", { edgeModel: "SmolVLA (450M)", edgeRole: "policy" });
  ws = addRobot(ws, { configId: planner, name: "Bench 01", deviceId: CONFIGURED_DEVICE }, at).workspace;
  ws = addRobot(ws, { configId: cloud, name: "Bench 02", deviceId: CONFIGURED_DEVICE }, at).workspace;
  ws = addRobot(ws, { configId: vla, name: "Sim runner", projectId: PROJECT, platformRobotId: PLATFORM_ROBOT }, at).workspace;
  return ws;
}

export const h1 = (page: Page) => page.locator("h1:visible");
export const tile = (page: Page, label: string) => page.getByRole("group", { name: label, exact: true });
export async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), "no sideways scroll").toBe(true);
}
