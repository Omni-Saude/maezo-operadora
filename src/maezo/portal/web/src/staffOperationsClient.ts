import type { components } from "./generated/api";
import { validWire, wireSchema, type Schema } from "./decisionSchema";
import { validateAssignmentContext } from "./assignmentClient";

type Schemas = components["schemas"];

// W5 CLNT 5.5: the staff operations panel reads ONLY the GET routes the BFF already mounts —
// the governed assignment context (`decisions.py`, `Accept: …assignment-context.v2+json`),
// the escalation task context (`tasks.py`) and the public command receipt
// (`decisions.py`, `/commands/{id}/receipt?task_id=…`). There is deliberately no submit,
// completion or claim method here: the panel is read-only, and commands stay on the queues
// surface (`TaskOwnershipControls`), where the mutation discipline already lives.

export type StaffCommandBasis = Schemas["GovernedAssignmentContextResponse"];
export type StaffCaseDependencies = Schemas["TaskContextResponse"];
export type StaffCommandReceipt =
  | Schemas["PublicAssignmentReceipt"]
  | Schemas["AssignmentReceiptResponse"]
  | Schemas["DecisionReceiptResponse"];

export type StaffOperationsFailure =
  | Schemas["PortalDecisionError"]["code"]
  | Schemas["PortalContextError"]["code"]
  | "invalid-response";

export type StaffOperationsResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: StaffOperationsFailure }>;

export interface StaffOperationsClient {
  readCommandBasis(taskId: string, signal: AbortSignal): Promise<StaffOperationsResult<StaffCommandBasis>>;
  readCaseDependencies(
    taskId: string,
    signal: AbortSignal,
  ): Promise<StaffOperationsResult<StaffCaseDependencies>>;
  readCommandReceipt(
    taskId: string,
    commandId: string,
    signal: AbortSignal,
  ): Promise<StaffOperationsResult<StaffCommandReceipt>>;
}

type StaffOperationsFetch = typeof fetch;

const prefix = "/api/v1/portal";

// The two route taxonomies the panel can meet, folded into one closed status map: the
// decision plane (`PortalDecisionError`) and the read/context plane (`PortalContextError`).
// A code the status row does not name is refused, never guessed into a friendlier story.
const errorStatus: Readonly<Record<number, readonly StaffOperationsFailure[]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable", "session_unavailable"],
  403: ["operation_forbidden", "employee_access_required"],
  404: ["resource_unavailable"],
  409: ["revision_conflict", "refresh_required"],
  422: ["invalid_decision"],
  501: ["context_unavailable"],
  503: [
    "authority_unavailable",
    "task_unavailable",
    "form_projection_unavailable",
    "form_contract_unavailable",
    "admission_unavailable",
    "credential_scope_mismatch",
    "production_capabilities_unavailable",
    "dependency_unavailable",
    "read_dependency_unavailable",
  ],
};

// `authentication_unavailable`/`session_unavailable`: the session is gone and the portal
// moves the person to the session surface; no retry is offered for these.
export const sessionLevelFailures: readonly StaffOperationsFailure[] = [
  "authentication_unavailable",
  "session_unavailable",
];

function failure<T>(value: StaffOperationsFailure): StaffOperationsResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

// The same opaque-reference shape the queues plane submits (`ClaimAssignment`): validated
// against the reviewed artifact, never a local pattern. A drifted schema refuses closed.
const taskRefSchema: Schema | undefined = wireSchema("ClaimAssignment").properties?.task_id;
const commandRefSchema: Schema | undefined = wireSchema("ClaimAssignment").properties?.command_id;

export function validStaffReference(value: unknown, schema: Schema | undefined): value is string {
  return schema !== undefined && validWire(value, schema);
}

// The context the queues plane already reads, revalidated here: full closed wire plus the
// echoed task. A reply about another task is not a basis this screen may render.
export function validateStaffCommandBasis(value: unknown, taskId: string): StaffCommandBasis | null {
  return validStaffReference(taskId, taskRefSchema) ? validateAssignmentContext(value, taskId) : null;
}

export function validateStaffCaseDependencies(
  value: unknown,
  taskId: string,
): StaffCaseDependencies | null {
  if (!validStaffReference(taskId, taskRefSchema) || !validWire(value, wireSchema("TaskContextResponse"))) {
    return null;
  }
  const context = value as StaffCaseDependencies;
  return context.task_id === taskId ? context : null;
}

// The receipt route answers one of three closed shapes (governed assignment, legacy
// assignment, decision). The first wire-valid shape wins; a shape that echoes another
// command or task is refused outright, never re-matched against the next schema.
const receiptSchemas = [
  "PublicAssignmentReceipt",
  "AssignmentReceiptResponse",
  "DecisionReceiptResponse",
] as const;

export function validateStaffCommandReceipt(
  value: unknown,
  taskId: string,
  commandId: string,
): StaffCommandReceipt | null {
  if (
    !validStaffReference(taskId, taskRefSchema) ||
    !validStaffReference(commandId, commandRefSchema)
  ) {
    return null;
  }
  for (const name of receiptSchemas) {
    const schema = wireSchema(name);
    if (!validWire(value, schema)) continue;
    const receipt = value as StaffCommandReceipt;
    return receipt.task_id === taskId && receipt.command_id === commandId ? receipt : null;
  }
  return null;
}

async function parseError(
  response: Response,
  signal: AbortSignal,
): Promise<StaffOperationsFailure | null> {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return null;
  }
  const decision = validWire(value, wireSchema("PortalDecisionError"))
    ? ((value as Schemas["PortalDecisionError"]).code as StaffOperationsFailure)
    : null;
  const context = validWire(value, wireSchema("PortalContextError"))
    ? ((value as Schemas["PortalContextError"]).code as StaffOperationsFailure)
    : null;
  const code = decision ?? context;
  return code !== null && errorStatus[response.status]?.includes(code) ? code : null;
}

export function createStaffOperationsClient(options: Readonly<{
  fetcher?: StaffOperationsFetch;
}> = {}): StaffOperationsClient {
  const fetcher = options.fetcher ?? fetch;
  async function read<T>(
    path: string,
    accept: string,
    signal: AbortSignal,
    validate: (value: unknown) => T | null,
  ): Promise<StaffOperationsResult<T>> {
    let response: Response;
    try {
      response = await fetcher(path, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: { Accept: accept },
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
    async readCommandBasis(taskId, signal) {
      if (!validStaffReference(taskId, taskRefSchema)) return failure("invalid_request");
      return read(
        `${prefix}/tasks/${encodeURIComponent(taskId)}/assignment-context`,
        "application/vnd.maezo.assignment-context.v2+json",
        signal,
        (value) => validateStaffCommandBasis(value, taskId),
      );
    },
    async readCaseDependencies(taskId, signal) {
      if (!validStaffReference(taskId, taskRefSchema)) return failure("invalid_request");
      return read(
        `${prefix}/tasks/${encodeURIComponent(taskId)}/context`,
        "application/json",
        signal,
        (value) => validateStaffCaseDependencies(value, taskId),
      );
    },
    async readCommandReceipt(taskId, commandId, signal) {
      if (!validStaffReference(taskId, taskRefSchema) || !validStaffReference(commandId, commandRefSchema)) {
        return failure("invalid_request");
      }
      const query = new URLSearchParams({ task_id: taskId });
      return read(
        `${prefix}/commands/${encodeURIComponent(commandId)}/receipt?${query}`,
        "application/json",
        signal,
        (value) => validateStaffCommandReceipt(value, taskId, commandId),
      );
    },
  };
}
