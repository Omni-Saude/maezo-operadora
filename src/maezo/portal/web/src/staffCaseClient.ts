import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";
import { isCurrent, timestampMicroseconds } from "./taskReadTime";

type Schemas = components["schemas"];

export type StaffDetail = Schemas["StaffDetail"];
export type StaffPage = Schemas["StaffPage"];
export type StaffCaseFailure = Schemas["PortalIntakeError"]["code"] | "invalid-response";
export type StaffCaseResult =
  | Readonly<{ kind: "success"; value: StaffDetail }>
  | Readonly<{ kind: "failure"; failure: StaffCaseFailure }>;
export type StaffCasePageResult =
  | Readonly<{ kind: "success"; value: StaffPage }>
  | Readonly<{ kind: "failure"; failure: StaffCaseFailure }>;

export interface StaffCaseClient {
  listCases(cursor: string | null, signal: AbortSignal): Promise<StaffCasePageResult>;
  readCase(caseRef: string, signal: AbortSignal): Promise<StaffCaseResult>;
}

type StaffCaseFetch = typeof fetch;

const errorStatus: Readonly<Record<number, readonly Schemas["PortalIntakeError"]["code"][]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  404: ["resource_unavailable"],
  409: ["conflict"],
  503: ["dependency_unavailable"],
};

function failure(value: StaffCaseFailure): Readonly<{
  kind: "failure";
  failure: StaffCaseFailure;
}> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function exactInstant(value: unknown): value is string {
  return typeof value === "string" && /\.\d{6}Z$/.test(value) &&
    timestampMicroseconds(value) !== null;
}

function validRevision(value: unknown) {
  return typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value) &&
    value.length <= 19 && BigInt(value) <= 9_223_372_036_854_775_807n;
}

function validCaseRef(value: unknown) {
  return typeof value === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(value);
}

function validRef(value: unknown) {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$/.test(value);
}

export function validateStaffPage(value: unknown): StaffPage | null {
  if (!validWire(value, wireSchema("StaffPage"))) return null;
  const page = value as StaffPage;
  const observed = timestampMicroseconds(page.freshness.observed_at);
  const sourceObserved = timestampMicroseconds(page.freshness.source_observed_at);
  const validUntil = timestampMicroseconds(page.freshness.valid_until);
  const refs = page.items.map((item) => item.case_ref);
  if (
    page.schema !== "portal-staff-case-page.v1" ||
    (page.next_cursor !== null && !validRef(page.next_cursor)) ||
    refs.some((caseRef, index) => !validCaseRef(caseRef) || (index > 0 && refs[index - 1] >= caseRef)) ||
    page.items.some((item) =>
      item.kind !== "authorization" || !validRevision(item.record_revision) ||
      !exactInstant(item.state_observed_at)) ||
    !exactInstant(page.freshness.observed_at) ||
    !exactInstant(page.freshness.source_observed_at) ||
    !exactInstant(page.freshness.valid_until) ||
    sourceObserved! > observed! ||
    observed! >= validUntil! ||
    !isCurrent(page.freshness.valid_until) ||
    page.freshness.refresh_after_seconds !== 10
  ) return null;
  return page;
}

export function validateStaffDetail(value: unknown, caseRef: string): StaffDetail | null {
  if (!validWire(value, wireSchema("StaffDetail"))) return null;
  const detail = value as StaffDetail;
  const observed = timestampMicroseconds(detail.freshness.observed_at);
  const validUntil = timestampMicroseconds(detail.freshness.valid_until);
  const taskIds = detail.active_tasks.map((task) => task.task_id);
  if (
    detail.schema !== "portal-staff-case-detail.v1" ||
    detail.case.case_ref !== caseRef ||
    detail.identity.case_ref !== caseRef ||
    detail.case.kind !== "authorization" ||
    detail.identity.kind !== "authorization" ||
    !validRevision(detail.case.record_revision) ||
    !exactInstant(detail.case.state_observed_at) ||
    !exactInstant(detail.freshness.observed_at) ||
    !exactInstant(detail.freshness.source_observed_at) ||
    !exactInstant(detail.freshness.valid_until) ||
    observed! >= validUntil! ||
    !isCurrent(detail.freshness.valid_until) ||
    detail.freshness.refresh_after_seconds !== 10 ||
    detail.tasks_complete !== (detail.next_task_cursor === null) ||
    !detail.tasks_complete ||
    detail.next_task_cursor !== null ||
    taskIds.some((taskId, index) => index > 0 && taskIds[index - 1] >= taskId) ||
    detail.active_tasks.some((task) =>
      !validRevision(task.task_revision) || !exactInstant(task.created_at) ||
      (task.due_at !== null && !exactInstant(task.due_at))) ||
    (detail.case.state === "ended" && detail.active_tasks.length > 0)
  ) return null;
  return detail;
}

async function parseError(response: Response, signal: AbortSignal): Promise<StaffCaseFailure | null> {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return null;
  }
  if (!validWire(value, wireSchema("PortalIntakeError"))) return null;
  const code = (value as Schemas["PortalIntakeError"]).code;
  return errorStatus[response.status]?.includes(code) ? code : null;
}

export function createStaffCaseClient(options: Readonly<{
  fetcher?: StaffCaseFetch;
}> = {}): StaffCaseClient {
  const fetcher = options.fetcher ?? fetch;
  return {
    async listCases(cursor, signal) {
      if (cursor !== null && !validRef(cursor)) return failure("invalid_request");
      const query = new URLSearchParams({ kind: "authorization", limit: "25" });
      if (cursor !== null) query.set("cursor", cursor);
      let response: Response;
      try {
        response = await fetcher(`/api/v1/portal/cases?${query}`, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          headers: { Accept: "application/json" },
          signal,
        });
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("dependency_unavailable");
      }
      if (response.status !== 200) {
        return failure(await parseError(response, signal) ?? "invalid-response");
      }
      let value: unknown;
      try {
        value = await response.json();
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("invalid-response");
      }
      const parsed = validateStaffPage(value);
      return parsed === null ? failure("invalid-response") : { kind: "success", value: parsed };
    },
    async readCase(caseRef, signal) {
      if (!validCaseRef(caseRef)) return failure("invalid_request");
      let response: Response;
      try {
        response = await fetcher(`/api/v1/portal/cases/${encodeURIComponent(caseRef)}`, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          headers: { Accept: "application/json" },
          signal,
        });
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("dependency_unavailable");
      }
      if (response.status !== 200) {
        return failure(await parseError(response, signal) ?? "invalid-response");
      }
      let value: unknown;
      try {
        value = await response.json();
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("invalid-response");
      }
      const parsed = validateStaffDetail(value, caseRef);
      return parsed === null ? failure("invalid-response") : { kind: "success", value: parsed };
    },
  };
}
