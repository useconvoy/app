import { runAgentEpisode } from "./agent";
import { runCoordinator, runWorkerService } from "./coordinator";

async function main(): Promise<void> {
  const mode = process.env.MODE;
  if (mode === "agent") {
    await runAgentEpisode();
    return;
  }
  if (mode === "coordinator") {
    await runCoordinator();
    return;
  }
  if (mode === "worker") {
    await runWorkerService();
    return;
  }
  throw new Error(`Unsupported MODE ${JSON.stringify(mode)}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
