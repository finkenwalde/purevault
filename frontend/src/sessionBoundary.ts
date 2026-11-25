export const MEANINGFUL_ACTIVITY_EVENTS = ["keydown", "pointerdown", "touchstart"] as const;

let generation = 0;

export function captureSessionGeneration() {
  return generation;
}

export function advanceSessionGeneration() {
  generation += 1;
}

export function responseBelongsToCurrentSession(capturedGeneration: number) {
  return capturedGeneration === generation;
}

export function shouldAcknowledgeActivity(
  lastAcknowledgedAt: number,
  now: number,
  acknowledgmentRunning: boolean,
  throttleMilliseconds = 60_000,
) {
  return !acknowledgmentRunning && now - lastAcknowledgedAt >= throttleMilliseconds;
}
