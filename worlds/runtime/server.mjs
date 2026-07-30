import { createServer } from "node:http";
import {
  applyAction,
  evaluateWorld,
  getManifest,
  getState,
  listWorlds,
  resetWorld,
} from "./engine.mjs";

const port = Number(process.env.PORT ?? 8788);

function send(response, status, value) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET,POST,OPTIONS",
  });
  response.end(`${JSON.stringify(value, null, 2)}\n`);
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const server = createServer(async (request, response) => {
  if (request.method === "OPTIONS") {
    send(response, 204, {});
    return;
  }
  const url = new URL(request.url, `http://${request.headers.host ?? "localhost"}`);
  const parts = url.pathname.split("/").filter(Boolean);

  try {
    if (request.method === "GET" && url.pathname === "/health") {
      send(response, 200, { ok: true, service: "convoy-world-runtime" });
      return;
    }
    if (request.method === "GET" && url.pathname === "/v1/worlds") {
      send(response, 200, { worlds: await listWorlds() });
      return;
    }
    if (parts[0] === "v1" && parts[1] === "worlds" && parts[2]) {
      const worldId = parts[2];
      const operation = parts[3];
      if (request.method === "GET" && !operation) {
        send(response, 200, {
          manifest: await getManifest(worldId),
          state: await getState(worldId),
        });
        return;
      }
      if (request.method === "GET" && operation === "state") {
        send(response, 200, await getState(worldId));
        return;
      }
      if (request.method === "POST" && operation === "reset") {
        send(response, 200, await resetWorld(worldId));
        return;
      }
      if (request.method === "POST" && operation === "actions") {
        const result = await applyAction(worldId, await readBody(request));
        send(response, result.denied ? 409 : 200, result);
        return;
      }
      if (request.method === "POST" && operation === "evaluate") {
        send(response, 200, await evaluateWorld(worldId));
        return;
      }
    }
    send(response, 404, { error: "Not found" });
  } catch (error) {
    send(response, 400, { error: error.message });
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`Convoy World API listening on http://0.0.0.0:${port}`);
});
