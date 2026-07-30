import { readFile } from "node:fs/promises";
import {
  evaluateWorld,
  listWorlds,
  replayActions,
  resetWorld,
} from "./engine.mjs";

const [command, worldId, inputPath] = process.argv.slice(2);

switch (command) {
  case "catalog":
    console.log(JSON.stringify(await listWorlds(), null, 2));
    break;
  case "reset":
    console.log(JSON.stringify(await resetWorld(worldId), null, 2));
    break;
  case "evaluate":
    console.log(JSON.stringify(await evaluateWorld(worldId), null, 2));
    break;
  case "replay": {
    const actions = JSON.parse(await readFile(inputPath, "utf8"));
    await resetWorld(worldId);
    await replayActions(worldId, actions);
    console.log(JSON.stringify(await evaluateWorld(worldId), null, 2));
    break;
  }
  default:
    console.error("Usage: cli.mjs catalog|reset|evaluate|replay [world-id] [actions.json]");
    process.exitCode = 2;
}
