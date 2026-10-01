import test from "node:test";
import assert from "node:assert/strict";
import { actionLabels, frameMediaType } from "../../src/lib/platform/replay";

test("action labels: the recording's own, X/Y/Z/Grip for four values, else a1…aN", () => {
  assert.deepEqual(actionLabels(undefined, 4), ["X", "Y", "Z", "Grip"]);
  assert.deepEqual(actionLabels(null, 4), ["X", "Y", "Z", "Grip"], "hosted replays are unchanged");
  const bimanual = ["l_x", "l_y", "l_z", "l_rx", "l_ry", "l_rz", "l_grip", "r_x", "r_y", "r_z", "r_rx", "r_ry", "r_rz", "r_grip"];
  assert.deepEqual(actionLabels(bimanual, 14), bimanual);
  assert.deepEqual(actionLabels(["j1", "j2"], 2), ["j1", "j2"]);
  assert.deepEqual(actionLabels(null, 7), ["a1", "a2", "a3", "a4", "a5", "a6", "a7"]);
  assert.deepEqual(actionLabels(["only", "two"], 3), ["a1", "a2", "a3"], "labels must name every value");
  assert.deepEqual(actionLabels(["x", "", "z", "w"], 4), ["X", "Y", "Z", "Grip"], "an empty label is not a label");
  assert.deepEqual(actionLabels([1, 2, 3, 4], 4), ["X", "Y", "Z", "Grip"]);
  assert.deepEqual(actionLabels(null, 1), ["a1"]);
});

test("frame images are PNG unless the frame says JPEG; nothing else reaches a data URL type", () => {
  assert.equal(frameMediaType(undefined), "image/png");
  assert.equal(frameMediaType("image/png"), "image/png");
  assert.equal(frameMediaType("image/jpeg"), "image/jpeg");
  for (const other of ["image/svg+xml", "text/html", "image/jpeg;base64,", 1]) assert.equal(frameMediaType(other), "image/png");
});
