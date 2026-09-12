import { afterEach, expect, it, vi } from "vitest";
import { expiryDelay, isCurrent, retainedCeiling, timestampMicroseconds } from "./taskReadTime";

afterEach(() => vi.useRealTimers());
it("preserves exact microseconds and equivalent aware offsets without touching decimal business values", () => {
  const first = "2026-09-09T15:00:00.000001Z", next = "2026-09-09T15:00:00.000002Z";
  expect(timestampMicroseconds(next)! - timestampMicroseconds(first)!).toBe(1n);
  expect(timestampMicroseconds(first)).toBe(timestampMicroseconds("2026-09-09T12:00:00.000001-03:00"));
  expect(retainedCeiling(first, next)).toBe(first);
  expect(retainedCeiling(next, first)).toBe(first);
});

it("rounds retained timers toward refusal and treats arrival exactly at expiry as expired", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-09-09T15:00:00Z"));
  expect(expiryDelay("2026-09-09T15:00:00.001999Z")).toBe(1);
  expect(expiryDelay("2026-09-09T15:00:00.000001Z")).toBe(0);
  expect(isCurrent("2026-09-09T15:00:00Z")).toBe(false);
  expect(isCurrent("2026-09-09T15:00:00.000001Z")).toBe(true);
});

it.each(["0000-01-01T00:00:00Z", "2026-02-30T00:00:00Z", "2026-09-09T24:00:00Z",
  "2026-09-09T00:00:60Z", "2026-09-09T00:00:00+24:00", "2026-09-09T00:00:00.0000001Z",
  "2026-09-09T00:00:00", "2026-09-09T00:00:00Z\n", "0001-01-01T00:00:00+01:00",
  "9999-12-31T23:59:59-01:00"])("refuses a timestamp outside the actual Q1 serialized grammar: %s", (value) => {
  expect(timestampMicroseconds(value)).toBeNull();
});

it("retains valid leap days and years below 100 without Date's 1900-year substitution", () => {
  expect(timestampMicroseconds("0020-02-29T00:00:00Z")).not.toBeNull();
  expect(timestampMicroseconds("2000-02-29T00:00:00Z")).not.toBeNull();
  expect(timestampMicroseconds("1900-02-29T00:00:00Z")).toBeNull();
});
