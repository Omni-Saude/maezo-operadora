import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";
import { isCurrent } from "./taskReadTime";

type Schemas = components["schemas"];

export type AssignmentContext = Schemas["AssignmentContextResponse"];
export type AssignmentOperation = AssignmentContext["allowed_operations"][number];
export type AssignmentSubmission = Schemas["AssignmentSubmission"];
export type AssignmentAdmission = Schemas["PendingAdmission"];
export type AssignmentReceipt = Schemas["AssignmentReceiptResponse"];
export type AssignmentFailure =
  | Schemas["PortalDecisionError"]["code"]
  | "invalid-response"
  | "outcome-unknown";
export type AssignmentResult<T> =
  | { kind: "success"; value: T }
  | { kind: AssignmentFailure };

const prefix = "/api/v1/portal";

const errorStatus: Readonly<Record<number, readonly AssignmentFailure[]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  409: ["revision_conflict"],
  422: ["invalid_decision"],
  503: [
    "authority_unavailable",
    "task_unavailable",
    "form_projection_unavailable",
    "form_contract_unavailable",
    "admission_unavailable",
    "credential_scope_mismatch",
    "production_capabilities_unavailable",
    "dependency_unavailable",
  ],
};

function validReference(value: unknown): boolean {
  const taskSchema = wireSchema("ClaimAssignment").properties?.task_id;
  return taskSchema !== undefined && validWire(value, taskSchema);
}

export function validateAssignmentContext(
  value: unknown,
  taskId: string,
): AssignmentContext | null {
  if (!validWire(value, wireSchema("AssignmentContextResponse"))) return null;
  const context = value as AssignmentContext;
  if (
    context.schema_version !== "portal-assignment-context.v1" ||
    context.task_id !== taskId ||
    !isCurrent(context.valid_until) ||
    new Set(context.allowed_operations).size !== context.allowed_operations.length
  ) {
    return null;
  }
  return context;
}

export function makeAssignmentSubmission(
  context: AssignmentContext,
  operation: AssignmentOperation,
  commandId: string,
): AssignmentSubmission | null {
  if (
    !validateAssignmentContext(context, context.task_id) ||
    !context.allowed_operations.includes(operation) ||
    !validReference(commandId)
  ) {
    return null;
  }

  const submission = {
    schema_version: "portal-assignment-submission.v1",
    command: {
      schema_version: 1,
      command_id: commandId,
      operation,
      task_id: context.task_id,
      process_definition_key: context.process_definition_key,
      process_definition_version: context.process_definition_version,
      process_definition_id: context.process_definition_id,
      process_definition_digest: context.process_definition_digest,
      task_definition_key: context.task_definition_key,
      form_key: context.form_key,
      form_version: context.form_version,
      form_digest: context.form_digest,
      expected_task_revision: context.expected_task_revision,
      expected_evidence_revision: context.expected_evidence_revision,
      expected_evidence_digest: context.expected_evidence_digest,
      expected_membership_revision: context.expected_membership_revision,
      expected_authority_revision: context.expected_authority_revision,
    },
  };
  return validWire(submission, wireSchema("AssignmentSubmission"))
    ? (submission as AssignmentSubmission)
    : null;
}

export function validateAssignmentReceipt(
  value: unknown,
  taskId: string,
  commandId: string,
): AssignmentReceipt | null {
  if (!validWire(value, wireSchema("AssignmentReceiptResponse"))) return null;
  const receipt = value as AssignmentReceipt;
  if (
    receipt.schema_version !== "human-public-receipt.v1" ||
    receipt.task_id !== taskId ||
    receipt.command_id !== commandId
  ) {
    return null;
  }
  if (
    receipt.engine_recorded_at != null &&
    !/(?:Z|[+-]00:00)$/.test(receipt.engine_recorded_at)
  ) {
    return null;
  }

  const engineProof = [
    receipt.engine_receipt_ref,
    receipt.engine_recorded_at,
    receipt.consumed_task_revision,
    receipt.resulting_task_revision,
  ];
  if (receipt.status === "committed") {
    return engineProof.every((part) => part != null) &&
      receipt.audit_result_ref != null &&
      receipt.technical_code == null
      ? receipt
      : null;
  }
  if (engineProof.some((part) => part != null)) return null;
  if (receipt.status === "pending") {
    return receipt.audit_result_ref == null && receipt.technical_code == null
      ? receipt
      : null;
  }
  return receipt.audit_result_ref != null && receipt.technical_code != null
    ? receipt
    : null;
}

async function request<T>(
  path: string,
  signal: AbortSignal,
  successStatus: number,
  validate: (value: unknown) => T | null,
  submission?: AssignmentSubmission,
  csrfToken?: string,
): Promise<AssignmentResult<T>> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: submission === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        ...(submission === undefined
          ? {}
          : {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken!,
            }),
      },
      ...(submission === undefined ? {} : { body: JSON.stringify(submission) }),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { kind: submission === undefined ? "dependency_unavailable" : "outcome-unknown" };
  }

  let value: unknown;
  try {
    value = await response.json();
  } catch {
    return { kind: submission === undefined ? "invalid-response" : "outcome-unknown" };
  }
  if (response.status === successStatus) {
    const parsed = validate(value);
    return parsed === null
      ? { kind: submission === undefined ? "invalid-response" : "outcome-unknown" }
      : { kind: "success", value: parsed };
  }
  if (validWire(value, wireSchema("PortalDecisionError"))) {
    const code = (value as Schemas["PortalDecisionError"]).code;
    if (errorStatus[response.status]?.includes(code)) return { kind: code };
  }
  return { kind: submission === undefined ? "invalid-response" : "outcome-unknown" };
}

export function readAssignmentContext(
  taskId: string,
  signal: AbortSignal,
): Promise<AssignmentResult<AssignmentContext>> {
  if (!validReference(taskId)) return Promise.resolve({ kind: "invalid_request" });
  return request(
    `${prefix}/tasks/${encodeURIComponent(taskId)}/assignment-context`,
    signal,
    200,
    (value) => validateAssignmentContext(value, taskId),
  );
}

export function submitAssignment(
  submission: AssignmentSubmission,
  csrfToken: string,
  signal: AbortSignal,
): Promise<AssignmentResult<AssignmentAdmission>> {
  if (!csrfToken || !validWire(submission, wireSchema("AssignmentSubmission"))) {
    return Promise.resolve({ kind: "invalid_request" });
  }
  const command = submission.command;
  return request(
    `${prefix}/tasks/${encodeURIComponent(command.task_id)}/assignments`,
    signal,
    202,
    (value) => {
      if (!validWire(value, wireSchema("PendingAdmission"))) return null;
      const admission = value as AssignmentAdmission;
      return admission.status === "pending" &&
        admission.task_id === command.task_id &&
        admission.command_id === command.command_id
        ? admission
        : null;
    },
    submission,
    csrfToken,
  );
}

export function readAssignmentReceipt(
  taskId: string,
  commandId: string,
  signal: AbortSignal,
  receipt = false,
): Promise<AssignmentResult<AssignmentReceipt>> {
  if (!validReference(taskId) || !validReference(commandId)) {
    return Promise.resolve({ kind: "invalid_request" });
  }
  const query = new URLSearchParams({ task_id: taskId });
  return request(
    `${prefix}/commands/${encodeURIComponent(commandId)}${receipt ? "/receipt" : ""}?${query}`,
    signal,
    200,
    (value) => validateAssignmentReceipt(value, taskId, commandId),
  );
}
