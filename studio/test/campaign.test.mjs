import { test } from "node:test";
import assert from "node:assert/strict";
import {
  boundarySubmission,
  initialBoundaries,
  motifProgress,
} from "../src/campaign.js";
test("N+1 boundary assets map to N existing scenes with their exact revision", () => {
  const project = {
    revision: 7,
    scenes: [
      { start_asset_id: "a", end_asset_id: "b" },
      { start_asset_id: "b", end_asset_id: "c" },
    ],
  };
  assert.deepEqual(initialBoundaries(project), ["a", "b", "c"]);
  assert.deepEqual(
    boundarySubmission({
      project,
      assetIds: ["a", "b", "c"],
      prompts: ["move1", "move2"],
      durations: [5, 10],
    }),
    {
      expected_revision: 7,
      asset_ids: ["a", "b", "c"],
      prompts: ["move1", "move2"],
      durations: [5, 10],
    },
  );
});
test("missing assets cannot be confused with imported filename labels", () => {
  assert.throws(
    () =>
      boundarySubmission({
        project: { revision: 1, scenes: [{}] },
        assetIds: ["", ""],
        prompts: [""],
        durations: [5],
      }),
    /echte Bilddatei/,
  );
});
test("mapping cannot silently delete an existing scene", () => {
  assert.throws(
    () =>
      boundarySubmission({
        project: { revision: 1, scenes: [{}, {}] },
        assetIds: ["a", "b"],
        prompts: [""],
        durations: [5],
      }),
    /Anzahl/,
  );
});
test("existing fractional duration is preserved and nonfinite durations rejected", () => {
  const input = {
    project: { revision: 2, scenes: [{}] },
    assetIds: ["a", "b"],
    prompts: [""],
    durations: [6.2],
  };
  assert.equal(boundarySubmission(input).durations[0], 6.2);
  assert.throws(
    () => boundarySubmission({ ...input, durations: [NaN] }),
    /Dauer/,
  );
});
test("stale takes do not imply readiness for review", () => {
  const project = {
    scenes: [{ selected_take_id: "t", stale: true }],
    gates: { storyboard: "approved" },
  };
  assert.equal(motifProgress(project).phase, "production");
  assert.equal(
    motifProgress({
      ...project,
      scenes: [{ selected_take_id: "t", stale: false }],
    }).phase,
    "review",
  );
});
