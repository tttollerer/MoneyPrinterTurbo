import { test } from "node:test";
import assert from "node:assert/strict";
import { reorderedSceneIds } from "../src/workflow.js";
test("independent scenes move without mutating project state", () => {
  const scenes = [{ id: "a" }, { id: "b" }, { id: "c" }];
  assert.deepEqual(reorderedSceneIds(scenes, "b", -1), ["b", "a", "c"]);
  assert.deepEqual(
    scenes.map((s) => s.id),
    ["a", "b", "c"],
  );
});
test("a scene cannot be moved in front of its frame predecessor", () => {
  assert.equal(
    reorderedSceneIds(
      [{ id: "a" }, { id: "b", predecessor_scene_id: "a" }],
      "b",
      -1,
    ),
    null,
  );
});
test("a predecessor cannot move behind its dependent scene", () => {
  assert.equal(
    reorderedSceneIds(
      [{ id: "a" }, { id: "b", predecessor_scene_id: "a" }],
      "a",
      1,
    ),
    null,
  );
});
test("chain-preserving moves are allowed and boundary moves denied", () => {
  const scenes = [
    { id: "a" },
    { id: "x" },
    { id: "b", predecessor_scene_id: "a" },
  ];
  assert.deepEqual(reorderedSceneIds(scenes, "b", -1), ["a", "b", "x"]);
  assert.equal(reorderedSceneIds(scenes, "a", -1), null);
  assert.equal(reorderedSceneIds(scenes, "missing", 1), null);
});
