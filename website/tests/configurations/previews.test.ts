import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { buildRevision, EMPTY_INPUT } from "../../src/lib/configurations/create";
import { ROBOT_PREVIEWS, robotPreview } from "../../src/lib/configurations/previews";
import { createSampleWorkspace } from "../../src/lib/configurations/sample";
import { ROBOT_PREVIEW_IDS } from "../../src/lib/configurations/types";
import type { RobotSpec } from "../../src/lib/configurations/types";

// The suite runs from website/ (scripts/test-configurations.sh), where public/ is.
const PUBLIC = join(process.cwd(), "public");
const MAX_BYTES = 1.5 * 1024 * 1024;

/** Width and height of a WebP still: lossy (VP8), lossless (VP8L) or extended (VP8X). */
function webpSize(data: Buffer): [number, number] {
  assert.equal(data.toString("ascii", 0, 4), "RIFF");
  assert.equal(data.toString("ascii", 8, 12), "WEBP");
  const chunk = data.toString("ascii", 12, 16);
  if (chunk === "VP8 ") return [data.readUInt16LE(26) & 0x3fff, data.readUInt16LE(28) & 0x3fff];
  if (chunk === "VP8L") { const bits = data.readUInt32LE(21); return [(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1]; }
  if (chunk === "VP8X") return [data.readUIntLE(24, 3) + 1, data.readUIntLE(27, 3) + 1];
  throw new Error(`unexpected WebP chunk "${chunk}"`);
}

test("a robot names a preview by a known id; anything else shows none", () => {
  assert.equal(robotPreview({ preview: "bimanual-station" }), ROBOT_PREVIEWS["bimanual-station"]);
  const others = [undefined, null, "", "bimanual", "Bimanual-station", "__proto__", "constructor", "toString", "hasOwnProperty",
    "/sim/bimanual-station/poster.webp", "https://example.test/robot.mp4", "javascript:alert(1)", "../bimanual-station", 7, {}];
  for (const preview of others) assert.equal(robotPreview({ preview: preview as RobotSpec["preview"] }), null, String(preview));
});

test("every preview the website names is committed under public/sim/<id>/ and stays light", () => {
  assert.deepEqual(Object.keys(ROBOT_PREVIEWS).toSorted(), [...ROBOT_PREVIEW_IDS].toSorted(), "one asset set per id");
  for (const id of ROBOT_PREVIEW_IDS) {
    const preview = ROBOT_PREVIEWS[id];
    assert.equal(preview.id, id);
    assert.ok(preview.label.trim(), "a text alternative");
    assert.deepEqual(preview.sources.map(source => source.type.split(";")[0]), ["video/webm", "video/mp4"], "VP9 first, H.264 for the rest");
    let total = 0;
    for (const file of [preview.poster, ...preview.sources.map(source => source.src)]) {
      assert.match(file, new RegExp(`^/sim/${id}/[a-z0-9-]+\\.(webp|webm|mp4)$`), "a same-origin file in the preview's folder");
      assert.ok(existsSync(join(PUBLIC, file)), `${file} is committed`);
      total += statSync(join(PUBLIC, file)).size;
    }
    assert.ok(total <= MAX_BYTES, `${id}: ${total} bytes for the still and both loops`);
    assert.deepEqual(webpSize(readFileSync(join(PUBLIC, preview.poster))), [preview.width, preview.height], "the stage's aspect ratio is the poster's");
  }
});

test("the sample's robot shows the bimanual station; a configuration from the form shows none", () => {
  const ws = createSampleWorkspace(Date.parse("2026-10-01T09:41:20Z"));
  for (const config of ws.configurations) {
    const robot = config.revisions[0].robot;
    assert.equal(robot.preview, "bimanual-station", config.id);
    assert.equal(robotPreview(robot)?.poster, "/sim/bimanual-station/poster.webp");
  }
  const created = buildRevision({ ...EMPTY_INPUT, name: "Arm", robot: "Arm", edgeModel: "Qwen2.5-1.5B-Instruct Q4_K_M" }, 0);
  assert.equal(created.robot.preview, undefined);
  assert.equal(robotPreview(created.robot), null);
});
