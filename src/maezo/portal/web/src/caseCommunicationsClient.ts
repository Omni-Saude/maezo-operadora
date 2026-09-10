import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";
import { isCurrent, timestampMicroseconds } from "./taskReadTime";

type Schemas = components["schemas"];

export type CommunicationPage = Schemas["CommunicationPage"];
export type CommunicationReceipt = Schemas["CommunicationReceipt"];
export type CommunicationSubmission = Schemas["CommunicationSubmission"];
export type HistoryPage = Schemas["HistoryPage"];
export type CommunicationsFetch = typeof fetch;

export type CommunicationsFailure =
  | Schemas["PortalIntakeError"]["code"]
  | "invalid-response"
  | "outcome-unknown";

export type CommunicationsResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: CommunicationsFailure }>;

export interface CaseCommunicationsClient {
  listCommunications(
    caseRef: string,
    signal: AbortSignal,
    cursor?: string,
    limit?: number,
  ): Promise<CommunicationsResult<CommunicationPage>>;
  listHistory(
    caseRef: string,
    signal: AbortSignal,
    cursor?: string,
    limit?: number,
  ): Promise<CommunicationsResult<HistoryPage>>;
  publishCommunication(
    caseRef: string,
    submission: CommunicationSubmission,
    signal: AbortSignal,
  ): Promise<CommunicationsResult<CommunicationReceipt>>;
}

const prefix = "/api/v1/portal";
const errorStatus: Readonly<Record<number, readonly Schemas["PortalIntakeError"]["code"][]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  404: ["resource_unavailable"],
  409: ["conflict"],
  503: ["dependency_unavailable"],
};

function failure<T>(value: CommunicationsFailure): CommunicationsResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function validRef(value: unknown) {
  return validWire(value, wireSchema("CommunicationSummary").properties!.communication_ref);
}

function exactInstant(value: unknown): value is string {
  return typeof value === "string" && /\.\d{6}Z$/.test(value) &&
    timestampMicroseconds(value) !== null;
}

function currentWindow(observedAt: string, validUntil: string) {
  const observed = timestampMicroseconds(observedAt);
  const until = timestampMicroseconds(validUntil);
  return exactInstant(observedAt) && exactInstant(validUntil) && observed! < until! &&
    isCurrent(validUntil);
}

function validateCommunicationPage(value: unknown, caseRef: string): CommunicationPage | null {
  if (!validWire(value, wireSchema("CommunicationPage"))) return null;
  const page = value as CommunicationPage;
  if (page.case_ref !== caseRef || !currentWindow(page.observed_at, page.valid_until)) return null;
  const refs = page.items.map((item) => item.communication_ref);
  if (refs.length !== new Set(refs).size) return null;
  return page.items.every((item) =>
    item.sender_kind != null && exactInstant(item.authored_at) &&
    exactInstant(item.inbox_available_at) && item.delivery_state === "inbox_available")
    ? page
    : null;
}

function validateHistoryPage(value: unknown, caseRef: string): HistoryPage | null {
  if (!validWire(value, wireSchema("HistoryPage"))) return null;
  const page = value as HistoryPage;
  if (page.case_ref !== caseRef || page.history_scope !== "portal_events" ||
      !currentWindow(page.observed_at, page.valid_until)) return null;
  const refs = page.items.map((item) => item.event_ref);
  if (refs.length !== new Set(refs).size) return null;
  let previous = -1n;
  for (const item of page.items) {
    if (item.sequence == null || item.kind == null || !exactInstant(item.occurred_at)) return null;
    const sequence = BigInt(item.sequence);
    if (sequence <= previous) return null;
    previous = sequence;
    if (item.kind === "communication_available") {
      if (item.communication_ref == null || item.command_ref != null || item.receipt_ref != null) return null;
    } else if (item.communication_ref != null || item.command_ref == null || item.receipt_ref == null) {
      return null;
    }
  }
  return page;
}

function validateReceipt(
  value: unknown,
  commandId: string,
): CommunicationReceipt | null {
  if (!validWire(value, wireSchema("CommunicationReceipt"))) return null;
  const receipt = value as CommunicationReceipt;
  return receipt.command_id === commandId && receipt.disposition === "inbox_available"
    ? receipt
    : null;
}

async function parseError(response: Response, signal: AbortSignal) {
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

function pagePath(caseRef: string, resource: "communications" | "history", cursor?: string, limit = 25) {
  if (!validRef(caseRef) || !Number.isSafeInteger(limit) || limit < 1 || limit > 100 ||
      (cursor !== undefined && !validRef(cursor))) return null;
  const query = new URLSearchParams();
  if (cursor !== undefined) query.set("cursor", cursor);
  if (limit !== 25) query.set("limit", String(limit));
  const suffix = query.size === 0 ? "" : `?${query}`;
  return `${prefix}/cases/${encodeURIComponent(caseRef)}/${resource}${suffix}`;
}

async function getPage<T>(
  fetcher: CommunicationsFetch,
  path: string | null,
  signal: AbortSignal,
  validate: (value: unknown) => T | null,
): Promise<CommunicationsResult<T>> {
  if (path === null) return failure("invalid_request");
  let response: Response;
  try {
    response = await fetcher(path, {
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
    const error = await parseError(response, signal);
    return failure(error ?? "invalid-response");
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return failure("invalid-response");
  }
  const parsed = validate(value);
  return parsed === null ? failure("invalid-response") : { kind: "success", value: parsed };
}

export function createCaseCommunicationsClient(options: Readonly<{
  csrfToken: string;
  fetcher?: CommunicationsFetch;
}>): CaseCommunicationsClient {
  const fetcher = options.fetcher ?? fetch;
  return {
    listCommunications(caseRef, signal, cursor, limit = 25) {
      return getPage(
        fetcher,
        pagePath(caseRef, "communications", cursor, limit),
        signal,
        (value) => validateCommunicationPage(value, caseRef),
      );
    },
    listHistory(caseRef, signal, cursor, limit = 25) {
      return getPage(
        fetcher,
        pagePath(caseRef, "history", cursor, limit),
        signal,
        (value) => validateHistoryPage(value, caseRef),
      );
    },
    async publishCommunication(caseRef, submission, signal) {
      if (!validRef(caseRef) || options.csrfToken.length === 0 ||
          !validWire(submission, wireSchema("CommunicationSubmission"))) {
        return failure("invalid_request");
      }
      let response: Response;
      try {
        response = await fetcher(`${prefix}/cases/${encodeURIComponent(caseRef)}/communications`, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-CSRF-Token": options.csrfToken,
          },
          body: JSON.stringify(submission),
          signal,
        });
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("outcome-unknown");
      }
      if (response.status !== 200) {
        const error = await parseError(response, signal);
        return failure(error ?? "outcome-unknown");
      }
      let value: unknown;
      try {
        value = await response.json();
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("outcome-unknown");
      }
      const receipt = validateReceipt(value, submission.command_id);
      return receipt === null
        ? failure("outcome-unknown")
        : { kind: "success", value: receipt };
    },
  };
}
