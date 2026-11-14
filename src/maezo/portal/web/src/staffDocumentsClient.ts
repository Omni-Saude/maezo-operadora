import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";

type Schemas = components["schemas"];

// W5 CLNT 5.5: the staff documents panel reads the SAME mounted BFF routes the external
// case workspace reads (`documents.py` GET list/list-requests). There is deliberately no
// upload, response or content method here: the panel is read-only, and document bytes stay
// on the protected PHI plane (`/api/v1/phi/documents/{ref}/content`), which this client
// never touches.
export type StaffDocumentPage = Schemas["DocumentPage"];
export type StaffDocumentRequestPage = Schemas["DocumentRequestPage"];
export type StaffDocumentsFailure = Schemas["PortalIntakeError"]["code"] | "invalid-response";
export type StaffDocumentsResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: StaffDocumentsFailure }>;

export interface StaffDocumentsClient {
  listDocuments(caseRef: string, signal: AbortSignal): Promise<StaffDocumentsResult<StaffDocumentPage>>;
  listDocumentRequests(
    caseRef: string,
    signal: AbortSignal,
  ): Promise<StaffDocumentsResult<StaffDocumentRequestPage>>;
}

type StaffDocumentsFetch = typeof fetch;

const errorStatus: Readonly<Record<number, readonly Schemas["PortalIntakeError"]["code"][]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  404: ["resource_unavailable"],
  409: ["conflict"],
  503: ["dependency_unavailable"],
};

function failure<T>(value: StaffDocumentsFailure): StaffDocumentsResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function validCaseRef(value: unknown) {
  return typeof value === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(value);
}

// Same closed checks the external plane applies to these two pages: reviewed wire shape,
// the echoed case_ref matches the request, and refs stay unique.
export function validateStaffDocumentPage(value: unknown, caseRef: string): StaffDocumentPage | null {
  if (!validCaseRef(caseRef) || !validWire(value, wireSchema("DocumentPage"))) return null;
  const page = value as StaffDocumentPage;
  if (page.case_ref !== caseRef) return null;
  const refs = page.documents.map((document) => document.document_ref);
  return refs.length === new Set(refs).size ? page : null;
}

export function validateStaffDocumentRequestPage(
  value: unknown,
  caseRef: string,
): StaffDocumentRequestPage | null {
  if (!validCaseRef(caseRef) || !validWire(value, wireSchema("DocumentRequestPage"))) return null;
  const page = value as StaffDocumentRequestPage;
  if (page.case_ref !== caseRef) return null;
  const refs = page.requests.map((request) => request.request_ref);
  return refs.length === new Set(refs).size ? page : null;
}

async function parseError(
  response: Response,
  signal: AbortSignal,
): Promise<StaffDocumentsFailure | null> {
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

export function createStaffDocumentsClient(options: Readonly<{
  fetcher?: StaffDocumentsFetch;
}> = {}): StaffDocumentsClient {
  const fetcher = options.fetcher ?? fetch;
  async function read<T>(
    path: string,
    caseRef: string,
    signal: AbortSignal,
    validate: (value: unknown) => T | null,
  ): Promise<StaffDocumentsResult<T>> {
    if (!validCaseRef(caseRef)) return failure("invalid_request");
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
      return failure(await parseError(response, signal) ?? "invalid-response");
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
  return {
    listDocuments(caseRef, signal) {
      return read(
        `/api/v1/portal/cases/${encodeURIComponent(caseRef)}/documents`,
        caseRef,
        signal,
        (value) => validateStaffDocumentPage(value, caseRef),
      );
    },
    listDocumentRequests(caseRef, signal) {
      return read(
        `/api/v1/portal/cases/${encodeURIComponent(caseRef)}/document-requests`,
        caseRef,
        signal,
        (value) => validateStaffDocumentRequestPage(value, caseRef),
      );
    },
  };
}
