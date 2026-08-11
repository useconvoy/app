/** Pure mapping from an agent's runtime setup to runtime-owned tools. */
import type { Agent } from "@/lib/api/environments";

type AgentRuntimeFacts = Pick<Agent, "sandboxTemplate" | "browserPolicy">;

export function agentRuntimeToolIds(agent: AgentRuntimeFacts | null | undefined): string[] {
  if (!agent?.sandboxTemplate.trim()) return [];
  const tools = ["sandbox_exec"];
  if ((agent.browserPolicy?.allowedDomains.length ?? 0) > 0) {
    tools.push("sandbox_browser");
  }
  return tools;
}
