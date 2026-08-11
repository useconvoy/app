import { access, mkdtemp, readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createControlPlane } from "../apps/sandbox-control-plane/src/server.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const convoyRoot = path.resolve(projectRoot, "..");
const python = path.join(convoyRoot, ".venv", "bin", "python");
try {
  await access(python);
} catch {
  console.log("Skipping real Convoy connector integration: ../.venv is unavailable");
  process.exit(0);
}

const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "convoy-connectors-e2e-"));
const { server, runtime } = await createControlPlane({ runtimeRoot });
const fixture = JSON.parse(await readFile(new URL("../fixtures/connector-development.json", import.meta.url), "utf8"));
await runtime.provision({ sandboxId: "python-connectors", fixture });
await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});
const origin = `http://127.0.0.1:${server.address().port}`;

try {
  const exitCode = await new Promise((resolve, reject) => {
    const child = spawn(python, [path.join(projectRoot, "tests", "convoy_connectors_client.py")], {
      cwd: convoyRoot,
      stdio: "inherit",
      env: { ...process.env, CONVOY_SANDBOX_ORIGIN: origin, CONVOY_SANDBOX_ID: "python-connectors" },
    });
    child.once("error", reject);
    child.once("exit", resolve);
  });
  if (exitCode !== 0) throw new Error(`Convoy connector integration exited with ${exitCode}`);
} finally {
  await new Promise((resolve) => server.close(resolve));
}
