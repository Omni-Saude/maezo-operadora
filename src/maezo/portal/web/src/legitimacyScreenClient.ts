import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";

// E-SF-20 (EP-CORE-15a): the staff client of the collective-legitimacy screen. It speaks only
// the four operations the BFF mounts (`legitimacy_screen.py`): GET checklist, GET latest by
// contratante, POST create, POST revalidate with the known revision. The checklist is the
// SERVER's manifest — this file never carries a criterion label, an anchor or a result label.
// The surface ships DARK: with `collective_legitimacy_screen` OFF the BFF answers 403
// `operation_forbidden` (flag) or 503 `dependency_unavailable` (unbound slot) before any
// effect, and the panel reads either as "not enabled here".

type Schemas = components["schemas"];

export type LegitimacyChecklist = Schemas["LegitimacyChecklistManifest"];
export type LegitimacyCriterion = Schemas["ChecklistCriterion"];
export type LegitimacyRecord = Schemas["LegitimacyScreenRecord"];
export type LegitimacySubmission = Schemas["LegitimacyScreenSubmission"];
export type LegitimacyRevalidation = Schemas["LegitimacyScreenRevalidation"];
export type LegitimacyFailure = Schemas["PortalLegitimacyScreenError"]["code"] | "invalid-response";

export type LegitimacyResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: LegitimacyFailure }>;

export interface LegitimacyScreenClient {
  readChecklist(signal: AbortSignal): Promise<LegitimacyResult<LegitimacyChecklist>>;
  readLatest(legalEntityRef: string, signal: AbortSignal): Promise<LegitimacyResult<LegitimacyRecord>>;
  create(
    submission: LegitimacySubmission,
    csrfToken: string,
    signal: AbortSignal,
  ): Promise<LegitimacyResult<LegitimacyRecord>>;
  revalidate(
    screenRef: string,
    revalidation: LegitimacyRevalidation,
    csrfToken: string,
    signal: AbortSignal,
  ): Promise<LegitimacyResult<LegitimacyRecord>>;
}

type LegitimacyFetch = typeof fetch;

const ROUTE = "/api/v1/portal/legitimacy-screens";

const errorStatus: Readonly<Record<number, readonly LegitimacyFailure[]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden", "field_not_client_owned"],
  404: ["resource_unavailable"],
  409: ["screen_conflict", "stale_revision"],
  503: ["dependency_unavailable"],
};

// The two answers that mean "this area is not enabled in this deployment" (flag OFF or the
// unbound slot). After either, the panel offers nothing that writes.
export const notEnabledFailures: readonly LegitimacyFailure[] = [
  "operation_forbidden",
  "dependency_unavailable",
];

function failure<T>(value: LegitimacyFailure): LegitimacyResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

async function parseError(response: Response, signal: AbortSignal): Promise<LegitimacyFailure | null> {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return null;
  }
  if (!validWire(value, wireSchema("PortalLegitimacyScreenError"))) return null;
  const code = (value as Schemas["PortalLegitimacyScreenError"]).code;
  return errorStatus[response.status]?.includes(code) ? code : null;
}

async function request<T>(
  fetcher: LegitimacyFetch,
  path: string,
  init: RequestInit,
  expectedStatus: number,
  schemaName: string,
  signal: AbortSignal,
): Promise<LegitimacyResult<T>> {
  let response: Response;
  try {
    response = await fetcher(path, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal,
    });
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return failure("dependency_unavailable");
  }
  if (response.status !== expectedStatus) {
    return failure((await parseError(response, signal)) ?? "invalid-response");
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return failure("invalid-response");
  }
  return validWire(value, wireSchema(schemaName))
    ? { kind: "success", value: value as T }
    : failure("invalid-response");
}

function post(body: unknown, csrfToken: string): RequestInit {
  return {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(body),
  };
}

const get: RequestInit = { method: "GET", headers: { Accept: "application/json" } };

export function createLegitimacyScreenClient(options: Readonly<{
  fetcher?: LegitimacyFetch;
}> = {}): LegitimacyScreenClient {
  const fetcher = options.fetcher ?? fetch;
  return {
    readChecklist(signal) {
      return request(fetcher, `${ROUTE}/checklist`, get, 200, "LegitimacyChecklistManifest", signal);
    },
    readLatest(legalEntityRef, signal) {
      return request(
        fetcher,
        `${ROUTE}/contratante/${encodeURIComponent(legalEntityRef)}`,
        get,
        200,
        "LegitimacyScreenRecord",
        signal,
      );
    },
    async create(submission, csrfToken, signal) {
      if (!csrfToken || !validWire(submission, wireSchema("LegitimacyScreenSubmission"))) {
        return failure("invalid_request");
      }
      return request(fetcher, ROUTE, post(submission, csrfToken), 201, "LegitimacyScreenRecord", signal);
    },
    async revalidate(screenRef, revalidation, csrfToken, signal) {
      if (!csrfToken || !screenRef || !validWire(revalidation, wireSchema("LegitimacyScreenRevalidation"))) {
        return failure("invalid_request");
      }
      return request(
        fetcher,
        `${ROUTE}/${encodeURIComponent(screenRef)}`,
        post(revalidation, csrfToken),
        201,
        "LegitimacyScreenRecord",
        signal,
      );
    },
  };
}
