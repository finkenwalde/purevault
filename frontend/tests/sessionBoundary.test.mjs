import assert from "node:assert/strict";
import test from "node:test";

import {
  MEANINGFUL_ACTIVITY_EVENTS,
  advanceSessionGeneration,
  captureSessionGeneration,
  responseBelongsToCurrentSession,
  shouldAcknowledgeActivity,
} from "../src/sessionBoundary.ts";

test("a response captured before sign-out is rejected afterward", () => {
  const captured = captureSessionGeneration();
  assert.equal(responseBelongsToCurrentSession(captured), true);
  advanceSessionGeneration();
  assert.equal(responseBelongsToCurrentSession(captured), false);
});

test("only meaningful input events are registered, never polling", () => {
  assert.deepEqual(MEANINGFUL_ACTIVITY_EVENTS, ["keydown", "pointerdown", "touchstart"]);
  assert.equal(MEANINGFUL_ACTIVITY_EVENTS.includes("poll"), false);
});

test("activity acknowledgments are throttled while one is running", () => {
  assert.equal(shouldAcknowledgeActivity(0, 59_999, false), false);
  assert.equal(shouldAcknowledgeActivity(0, 60_000, false), true);
  assert.equal(shouldAcknowledgeActivity(0, 60_000, true), false);
});
