import { buildImages, runLocal } from "./executor.mjs";

const [command, argument] = process.argv.slice(2);

try {
  if (command === "build") {
    await buildImages();
    console.log("Built convoy/world-runtime:dev and convoy/agent-runtime:dev.");
  } else if (command === "run" && argument) {
    const result = await runLocal(argument);
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.error("Usage: cli.mjs build | run <run-spec.json>");
    process.exitCode = 2;
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
