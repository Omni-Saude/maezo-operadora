import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";
import { isCurrent, timestampMicroseconds } from "./taskReadTime";

type Schemas = components["schemas"];

export type CasePage = Schemas["CasePage"];
export type CaseDetail = Schemas["CaseDetail"];
export type AuthIntakeSubmission = Schemas["AuthIntakeSubmission"];
export type IntakeReceipt = Schemas["IntakeReceipt"];
export type UploadInitiation = Schemas["UploadInitiation"];
export type UploadCompletion = Schemas["UploadCompletion"];
export type UploadReceipt = Schemas["UploadReceipt"];
export type DocumentPage = Schemas["DocumentPage"];
export type DocumentRequestPage = Schemas["DocumentRequestPage"];
export type DocumentResponse = Schemas["DocumentResponse"];
export type DocumentResponseReceipt = Schemas["DocumentResponseReceipt"];

export type ProductApiFailure =
  | Schemas["PortalIntakeError"]["code"]
  | "invalid-response"
  | "outcome-unknown";

export type ProductApiResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: ProductApiFailure }>;

export type ProductApiFetch = typeof fetch;

export interface PortalProductClient {
  listCases(signal: AbortSignal, cursor?: string): Promise<ProductApiResult<CasePage>>;
  readCase(caseRef: string, signal: AbortSignal): Promise<ProductApiResult<CaseDetail>>;
  submitAuthorization(
    submission: AuthIntakeSubmission,
    signal: AbortSignal,
  ): Promise<ProductApiResult<IntakeReceipt>>;
  readAuthorizationIntake(
    intakeRef: string,
    signal: AbortSignal,
  ): Promise<ProductApiResult<IntakeReceipt>>;
  listDocuments(caseRef: string, signal: AbortSignal): Promise<ProductApiResult<DocumentPage>>;
  listDocumentRequests(
    caseRef: string,
    signal: AbortSignal,
  ): Promise<ProductApiResult<DocumentRequestPage>>;
  initiateCaseUpload(
    caseRef: string,
    submission: UploadInitiation,
    signal: AbortSignal,
  ): Promise<ProductApiResult<UploadReceipt>>;
  initiateIntakeUpload(
    intakeRef: string,
    submission: UploadInitiation,
    signal: AbortSignal,
  ): Promise<ProductApiResult<UploadReceipt>>;
  completeUpload(
    uploadRef: string,
    submission: UploadCompletion,
    signal: AbortSignal,
  ): Promise<ProductApiResult<UploadReceipt>>;
  respondToDocumentRequest(
    caseRef: string,
    requestRef: string,
    submission: DocumentResponse,
    signal: AbortSignal,
  ): Promise<ProductApiResult<DocumentResponseReceipt>>;
  downloadDocument(documentRef: string, signal: AbortSignal): Promise<ProductApiResult<Blob>>;
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

function failure<T>(value: ProductApiFailure): ProductApiResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function validRef(value: unknown) {
  return validWire(value, wireSchema("DocumentSummary").properties!.document_ref);
}

function validFreshness(value: Schemas["Freshness"]) {
  const observed = timestampMicroseconds(value.observed_at);
  const until = timestampMicroseconds(value.valid_until);
  return observed !== null && until !== null && /\.\d{6}Z$/.test(value.observed_at) &&
    /\.\d{6}Z$/.test(value.valid_until) && observed < until && isCurrent(value.valid_until);
}

const audienceKinds: Readonly<Record<
  "beneficiary" | "provider",
  ReadonlySet<CasePage["items"][number]["kind"]>
>> = {
  beneficiary: new Set(["authorization", "reimbursement"]),
  provider: new Set(["authorization", "account"]),
};

function validCaseSummary(
  value: CasePage["items"][number],
  audience: "beneficiary" | "provider",
) {
  return audienceKinds[audience].has(value.kind) && /\.\d{6}Z$/.test(value.state_observed_at) &&
    value.record_revision.length <= 19 && BigInt(value.record_revision) <= 9_223_372_036_854_775_807n;
}

function validateCasePage(value: unknown, audience: "beneficiary" | "provider"): CasePage | null {
  if (!validWire(value, wireSchema("CasePage"))) return null;
  const page = value as CasePage;
  if (page.audience !== audience || !validFreshness(page.freshness)) return null;
  const refs = page.items.map((item) => item.case_ref);
  return refs.every((item, index) => index === 0 || refs[index - 1] < item) &&
    page.items.every((item) => validCaseSummary(item, audience)) ? page : null;
}

function validateCaseDetail(
  value: unknown,
  caseRef: string,
  audience: "beneficiary" | "provider",
): CaseDetail | null {
  if (!validWire(value, wireSchema("CaseDetail"))) return null;
  const detail = value as CaseDetail;
  return detail.case.case_ref === caseRef && validCaseSummary(detail.case, audience) &&
    detail.allowed_actions.length === 0 &&
    validFreshness(detail.freshness) ? detail : null;
}

function validateIntakeReceipt(
  value: unknown,
  expected: Readonly<{ commandId?: string; intakeRef?: string }>,
): IntakeReceipt | null {
  if (!validWire(value, wireSchema("IntakeReceipt"))) return null;
  const receipt = value as IntakeReceipt;
  if ((expected.commandId !== undefined && receipt.command_id !== expected.commandId) ||
      (expected.intakeRef !== undefined && receipt.intake_ref !== expected.intakeRef)) return null;
  if (receipt.disposition === "started") {
    return receipt.case_ref != null && receipt.start_receipt_ref != null ? receipt : null;
  }
  return receipt.case_ref == null && receipt.start_receipt_ref == null ? receipt : null;
}

function validateUploadReceipt(value: unknown): UploadReceipt | null {
  if (!validWire(value, wireSchema("UploadReceipt"))) return null;
  const receipt = value as UploadReceipt;
  return (receipt.disposition === "verified") === (receipt.document_ref != null) ? receipt : null;
}

function validateDocumentPage(value: unknown, caseRef: string): DocumentPage | null {
  if (!validWire(value, wireSchema("DocumentPage"))) return null;
  const page = value as DocumentPage;
  const refs = page.documents.map((item) => item.document_ref);
  return page.case_ref === caseRef && refs.length === new Set(refs).size ? page : null;
}

function validateDocumentRequestPage(value: unknown, caseRef: string): DocumentRequestPage | null {
  if (!validWire(value, wireSchema("DocumentRequestPage"))) return null;
  const page = value as DocumentRequestPage;
  const refs = page.requests.map((item) => item.request_ref);
  return page.case_ref === caseRef && refs.length === new Set(refs).size ? page : null;
}

function validateDocumentResponseReceipt(
  value: unknown,
  commandId: string,
  requestRef: string,
): DocumentResponseReceipt | null {
  if (!validWire(value, wireSchema("DocumentResponseReceipt"))) return null;
  const receipt = value as DocumentResponseReceipt;
  if (receipt.command_id !== commandId || receipt.request_ref !== requestRef) return null;
  return (receipt.disposition === "correlated") === (receipt.correlation_receipt_ref != null)
    ? receipt
    : null;
}

function validDocumentResponse(value: DocumentResponse) {
  return validWire(value, wireSchema("DocumentResponse")) && value.document_refs.length > 0 &&
    value.document_refs.length === new Set(value.document_refs).size;
}

async function parseError(response: Response, signal: AbortSignal): Promise<ProductApiFailure | null> {
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

type RequestOptions<T> = Readonly<{
  fetcher: ProductApiFetch;
  path: string;
  signal: AbortSignal;
  successStatus: number;
  validate: (value: unknown) => T | null;
  body?: unknown;
  csrfToken?: string;
}>;

async function requestJson<T>(options: RequestOptions<T>): Promise<ProductApiResult<T>> {
  const mutating = options.body !== undefined;
  let response: Response;
  try {
    response = await options.fetcher(options.path, {
      method: mutating ? "POST" : "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        ...(mutating
          ? { "Content-Type": "application/json", "X-CSRF-Token": options.csrfToken! }
          : {}),
      },
      ...(mutating ? { body: JSON.stringify(options.body) } : {}),
      signal: options.signal,
    });
  } catch (error) {
    if (aborted(error, options.signal)) throw error;
    return failure(mutating ? "outcome-unknown" : "dependency_unavailable");
  }
  if (response.status !== options.successStatus) {
    const error = await parseError(response, options.signal);
    return failure(error ?? (mutating ? "outcome-unknown" : "invalid-response"));
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, options.signal)) throw error;
    return failure(mutating ? "outcome-unknown" : "invalid-response");
  }
  const parsed = options.validate(value);
  return parsed === null
    ? failure(mutating ? "outcome-unknown" : "invalid-response")
    : { kind: "success", value: parsed };
}

function validSubmission(value: unknown, schema: string) {
  return validWire(value, wireSchema(schema));
}

function validAuthIntakeSubmission(value: AuthIntakeSubmission) {
  return validSubmission(value, "AuthIntakeSubmission") &&
    value.document_refs.length === new Set(value.document_refs).size;
}

export function createPortalProductClient(options: Readonly<{
  audience: "beneficiary" | "provider";
  csrfToken: string;
  fetcher?: ProductApiFetch;
}>): PortalProductClient {
  const fetcher = options.fetcher ?? fetch;
  const csrfToken = options.csrfToken;

  const post = <T>(
    path: string,
    body: unknown,
    schema: string,
    signal: AbortSignal,
    validate: (value: unknown) => T | null,
  ): Promise<ProductApiResult<T>> => {
    if (csrfToken.length === 0 || !validSubmission(body, schema)) {
      return Promise.resolve(failure("invalid_request"));
    }
    return requestJson({ fetcher, path, body, csrfToken, signal, successStatus: 202, validate });
  };

  return {
    listCases(signal, cursor) {
      if (cursor !== undefined && !/^[A-Za-z0-9_-]{1,2048}$/.test(cursor)) {
        return Promise.resolve(failure("invalid_request"));
      }
      const query = new URLSearchParams();
      if (cursor !== undefined) query.set("cursor", cursor);
      const suffix = query.size === 0 ? "" : `?${query}`;
      return requestJson({
        fetcher,
        path: `${prefix}/cases${suffix}`,
        signal,
        successStatus: 200,
        validate: (value) => validateCasePage(value, options.audience),
      });
    },
    readCase(caseRef, signal) {
      if (!validRef(caseRef)) return Promise.resolve(failure("invalid_request"));
      return requestJson({
        fetcher,
        path: `${prefix}/cases/${encodeURIComponent(caseRef)}`,
        signal,
        successStatus: 200,
        validate: (value) => validateCaseDetail(value, caseRef, options.audience),
      });
    },
    submitAuthorization(submission, signal) {
      if (!validAuthIntakeSubmission(submission)) {
        return Promise.resolve(failure("invalid_request"));
      }
      return post(
        `${prefix}/intakes/auth`,
        submission,
        "AuthIntakeSubmission",
        signal,
        (value) => validateIntakeReceipt(value, { commandId: submission.command_id }),
      );
    },
    readAuthorizationIntake(intakeRef, signal) {
      if (!validRef(intakeRef)) return Promise.resolve(failure("invalid_request"));
      return requestJson({
        fetcher,
        path: `${prefix}/intakes/${encodeURIComponent(intakeRef)}`,
        signal,
        successStatus: 200,
        validate: (value) => validateIntakeReceipt(value, { intakeRef }),
      });
    },
    listDocuments(caseRef, signal) {
      if (!validRef(caseRef)) return Promise.resolve(failure("invalid_request"));
      return requestJson({
        fetcher,
        path: `${prefix}/cases/${encodeURIComponent(caseRef)}/documents`,
        signal,
        successStatus: 200,
        validate: (value) => validateDocumentPage(value, caseRef),
      });
    },
    listDocumentRequests(caseRef, signal) {
      if (!validRef(caseRef)) return Promise.resolve(failure("invalid_request"));
      return requestJson({
        fetcher,
        path: `${prefix}/cases/${encodeURIComponent(caseRef)}/document-requests`,
        signal,
        successStatus: 200,
        validate: (value) => validateDocumentRequestPage(value, caseRef),
      });
    },
    initiateCaseUpload(caseRef, submission, signal) {
      if (!validRef(caseRef)) return Promise.resolve(failure("invalid_request"));
      return post(
        `${prefix}/cases/${encodeURIComponent(caseRef)}/document-uploads`,
        submission,
        "UploadInitiation",
        signal,
        validateUploadReceipt,
      );
    },
    initiateIntakeUpload(intakeRef, submission, signal) {
      if (!validRef(intakeRef)) return Promise.resolve(failure("invalid_request"));
      return post(
        `${prefix}/intakes/${encodeURIComponent(intakeRef)}/document-uploads`,
        submission,
        "UploadInitiation",
        signal,
        validateUploadReceipt,
      );
    },
    completeUpload(uploadRef, submission, signal) {
      if (!validRef(uploadRef)) return Promise.resolve(failure("invalid_request"));
      return post(
        `${prefix}/document-uploads/${encodeURIComponent(uploadRef)}/complete`,
        submission,
        "UploadCompletion",
        signal,
        validateUploadReceipt,
      );
    },
    respondToDocumentRequest(caseRef, requestRef, submission, signal) {
      if (!validRef(caseRef) || !validRef(requestRef) || !validDocumentResponse(submission)) {
        return Promise.resolve(failure("invalid_request"));
      }
      return post(
        `${prefix}/cases/${encodeURIComponent(caseRef)}/document-requests/${encodeURIComponent(requestRef)}/responses`,
        submission,
        "DocumentResponse",
        signal,
        (value) => validateDocumentResponseReceipt(
          value,
          submission.command_id,
          requestRef,
        ),
      );
    },
    async downloadDocument(documentRef, signal) {
      if (!validRef(documentRef)) return failure("invalid_request");
      let response: Response;
      try {
        response = await fetcher(`${prefix}/documents/${encodeURIComponent(documentRef)}/content`, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "follow",
          headers: { Accept: "application/octet-stream" },
          signal,
        });
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("dependency_unavailable");
      }
      if (!response.redirected) {
        const error = await parseError(response, signal);
        return failure(error ?? "invalid-response");
      }
      let target: URL;
      try {
        target = new URL(response.url);
      } catch {
        return failure("invalid-response");
      }
      const expectedOrigin = globalThis.location?.origin;
      const match = /^\/api\/v1\/phi\/documents\/([^/]+)\/content$/.exec(target.pathname);
      let redirectedRef: string | null = null;
      try {
        redirectedRef = match?.[1] === undefined ? null : decodeURIComponent(match[1]);
      } catch {
        return failure("invalid-response");
      }
      if (expectedOrigin === undefined || target.origin !== expectedOrigin || target.search !== "" ||
          target.hash !== "" || redirectedRef === null || !validRef(redirectedRef)) {
        return failure("invalid-response");
      }
      if (response.status !== 200) {
        const error = await parseError(response, signal);
        return failure(error ?? "invalid-response");
      }
      try {
        return { kind: "success", value: await response.blob() };
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("invalid-response");
      }
    },
  };
}
