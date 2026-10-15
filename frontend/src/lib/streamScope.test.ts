import { expect, it } from "vitest";
import { StreamScope } from "./streamScope";

it("invalidates old callbacks and cancels transport when a workspace is replaced", () => {
  const scope = new StreamScope();
  const previous = scope.begin();
  const current = scope.begin();
  expect(previous.signal.aborted).toBe(true);
  expect(scope.isCurrent(previous.version)).toBe(false);
  expect(scope.isCurrent(current.version)).toBe(true);
  scope.invalidate();
  expect(current.signal.aborted).toBe(true);
  expect(scope.isCurrent(current.version)).toBe(false);
});

it("stopping keeps the current scope so its partial answer can remain visible", () => {
  const scope = new StreamScope();
  const current = scope.begin();
  scope.stop();
  expect(current.signal.aborted).toBe(true);
  expect(scope.isCurrent(current.version)).toBe(true);
});
