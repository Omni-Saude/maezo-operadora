import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";
import { isCurrent, timestampMicroseconds } from "./taskReadTime";

type Schemas = components["schemas"];

export type AssignmentContext = Schemas["GovernedAssignmentContextResponse"];
export type AssignmentCandidates = Schemas["AssignmentCandidatesResponse"];
export type AssignmentOperation = AssignmentContext["allowed_operations"][number];
export type AssignmentSubmission = Schemas["GovernedAssignmentSubmission"];
export type AssignmentAdmission = Schemas["PendingAdmission"];
export type AssignmentReceipt = Schemas["PublicAssignmentReceipt"];
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

const contextRevisions = ["process_definition_version", "form_version", "expected_task_revision",
  "expected_evidence_revision", "expected_membership_revision", "expected_authority_revision",
  "binding_version", "policy_version", "source_revision"] as const;
function revision(value: unknown, positive = false): boolean {
  return typeof value === "string" && /^(0|[1-9][0-9]*)$/.test(value) && value.length <= 19 &&
    BigInt(value) < 2n ** 63n && (!positive || value !== "0");
}
function sameContext(left: AssignmentContext, right: AssignmentContext): boolean {
  return (Object.keys(left) as (keyof AssignmentContext)[]).every((key) =>
    key === "valid_until" || JSON.stringify(left[key]) === JSON.stringify(right[key]));
}
function compareReferences(left: string, right: string): number {
  // The Python contract orders opaque strings by Unicode code point, not locale/UTF-16.
  const a = [...left], b = [...right];
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const difference = a[i].codePointAt(0)! - b[i].codePointAt(0)!;
    if (difference !== 0) return difference;
  }
  return a.length - b.length;
}
const verifiedCandidates = new WeakSet<AssignmentCandidates>();
export async function validateAssignmentCandidates(
  value: unknown, context: AssignmentContext,
): Promise<AssignmentCandidates | null> {
  if (!validWire(value, wireSchema("AssignmentCandidatesResponse"))) return null;
  const page = value as AssignmentCandidates;
  if (!validateAssignmentContext(context, context.task_id) ||
      !validateAssignmentContext(page.context, context.task_id) || !sameContext(context, page.context) ||
      !context.allowed_operations.includes("reassign") || !isCurrent(page.valid_until) ||
      timestampMicroseconds(page.valid_until)! > timestampMicroseconds(page.context.valid_until)! ||
      page.candidate_count !== String(page.candidates.length) ||
      page.candidates.some((item, index) => !revision(item.target_membership_revision) ||
        (index > 0 && compareReferences(page.candidates[index - 1].target_ref, item.target_ref) >= 0))) return null;
  // These two string-only keys are in JCS order; no numbers, free text, or locale sorting.
  const canonical = JSON.stringify(page.candidates.map((item) => ({
    target_membership_revision: item.target_membership_revision, target_ref: item.target_ref,
  })));
  const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical)))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== page.candidate_digest || !isCurrent(context.valid_until) ||
      !isCurrent(page.context.valid_until) || !isCurrent(page.valid_until)) return null;
  page.candidates.forEach(Object.freeze); Object.freeze(page.candidates);
  Object.freeze(page.context.allowed_operations); Object.freeze(page.context); Object.freeze(page);
  verifiedCandidates.add(page);
  return page;
}

export function validateAssignmentContext(
  value: unknown,
  taskId: string,
): AssignmentContext | null {
  if (!validWire(value, wireSchema("GovernedAssignmentContextResponse"))) return null;
  const context = value as AssignmentContext;
  if (
    context.schema_version !== "portal-assignment-context.v2" ||
    context.task_id !== taskId ||
    !isCurrent(context.valid_until) ||
    new Set(context.allowed_operations).size !== context.allowed_operations.length ||
    !contextRevisions.every((key) => revision(context[key], key.endsWith("version")))
  ) {
    return null;
  }
  return context;
}

export function makeAssignmentSubmission(
  context: AssignmentContext,
  operation: AssignmentOperation,
  commandId: string,
  candidates?: AssignmentCandidates,
  targetRef?: string,
): AssignmentSubmission | null {
  if (
    !validateAssignmentContext(context, context.task_id) ||
    !context.allowed_operations.includes(operation) ||
    !validReference(commandId)
  ) {
    return null;
  }

  const target = operation === "reassign" && candidates !== undefined && verifiedCandidates.has(candidates) &&
    sameContext(context, candidates.context) && isCurrent(candidates.context.valid_until) && isCurrent(candidates.valid_until)
      ? candidates.candidates.find((item) => item.target_ref === targetRef) : undefined;
  if (operation === "reassign" && target === undefined) return null;
  if (operation !== "reassign" && (candidates !== undefined || targetRef !== undefined)) return null;
  const submission = {
    schema_version: "portal-assignment-submission.v2",
    command: {
      schema_version: 2,
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
      expected_assignee_ref: context.assignee_ref,
      expected_binding_ref: context.binding_ref,
      expected_binding_version: context.binding_version,
      expected_binding_digest: context.binding_digest,
      expected_policy_ref: context.policy_ref,
      expected_policy_version: context.policy_version,
      expected_policy_digest: context.policy_digest,
      expected_source_revision: context.source_revision,
      expected_generation_digest: context.generation_digest,
      target_ref: target?.target_ref ?? null,
      expected_target_membership_revision: target?.target_membership_revision ?? null,
    },
  };
  if (!validSubmission(submission)) return null;
  Object.freeze(submission.command);
  return Object.freeze(submission);
}

function validSubmission(value: unknown): value is AssignmentSubmission {
  if (!validWire(value, wireSchema("GovernedAssignmentSubmission"))) return false;
  const c = (value as AssignmentSubmission).command;
  const names = ["process_definition_version", "form_version", "expected_task_revision", "expected_evidence_revision",
    "expected_membership_revision", "expected_authority_revision", "expected_binding_version", "expected_policy_version",
    "expected_source_revision"] as const;
  return names.every((key) => revision(c[key], key.endsWith("version"))) &&
    (c.operation === "reassign"
      ? c.target_ref !== null && revision(c.expected_target_membership_revision)
      : c.target_ref === null && c.expected_target_membership_revision === null);
}

export function validateAssignmentReceipt(
  value: unknown,
  taskId: string,
  commandId: string,
  submission?: AssignmentSubmission,
): AssignmentReceipt | null {
  if (!validWire(value, wireSchema("PublicAssignmentReceipt"))) return null;
  const receipt = value as AssignmentReceipt;
  if (
    receipt.schema_version !== "human-public-assignment-receipt.v1" ||
    receipt.task_id !== taskId ||
    receipt.command_id !== commandId
  ) {
    return null;
  }
  if (receipt.command_schema !== "human-assignment.v2" ||
      ![receipt.binding_version, receipt.policy_version].every((v) => revision(v, true)) ||
      !revision(receipt.source_revision) ||
      (receipt.operation === "reassign"
        ? receipt.target_ref == null || !revision(receipt.target_membership_revision)
        : receipt.target_ref != null || receipt.target_membership_revision != null)) return null;
  if (submission !== undefined) {
    const c = submission.command;
    if (receipt.operation !== c.operation || receipt.target_ref !== c.target_ref ||
        receipt.target_membership_revision !== c.expected_target_membership_revision ||
        receipt.binding_ref !== c.expected_binding_ref || receipt.binding_version !== c.expected_binding_version ||
        receipt.binding_digest !== c.expected_binding_digest || receipt.policy_ref !== c.expected_policy_ref ||
        receipt.policy_version !== c.expected_policy_version || receipt.policy_digest !== c.expected_policy_digest ||
        receipt.source_revision !== c.expected_source_revision || receipt.generation_digest !== c.expected_generation_digest ||
        (receipt.status === "committed" && (receipt.prior_assignee_ref !== c.expected_assignee_ref ||
          receipt.consumed_task_revision !== c.expected_task_revision))) return null;
  }
  if (receipt.status === "committed") {
    const target = receipt.operation === "claim" ? receipt.principal_ref : receipt.operation === "release" ? null : receipt.target_ref;
    if (receipt.resulting_assignee_ref !== target || receipt.assignment_disposition !==
        (receipt.prior_assignee_ref === target ? "unchanged" : "changed") ||
        !revision(receipt.consumed_task_revision) || !revision(receipt.resulting_task_revision)) return null;
  } else if ([receipt.prior_assignee_ref, receipt.resulting_assignee_ref, receipt.assignment_disposition].some((v) => v != null)) return null;
  if (
    receipt.engine_recorded_at != null &&
    (timestampMicroseconds(receipt.engine_recorded_at) === null || !/(?:Z|[+-]00:00)$/.test(receipt.engine_recorded_at))
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
  validate: (value: unknown) => T | null | Promise<T | null>,
  submission?: AssignmentSubmission,
  csrfToken?: string,
  accept = "application/json",
): Promise<AssignmentResult<T>> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: submission === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: accept,
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

  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  if (response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/json") {
    return { kind: submission === undefined ? "invalid-response" : "outcome-unknown" };
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    return { kind: submission === undefined ? "invalid-response" : "outcome-unknown" };
  }
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  if (response.status === successStatus) {
    const parsed = await validate(value);
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    return parsed === null
      ? { kind: submission === undefined ? "invalid-response" : "outcome-unknown" }
      : { kind: "success", value: parsed };
  }
  if (validWire(value, wireSchema("PortalDecisionError"))) {
    const code = (value as Schemas["PortalDecisionError"]).code;
    if (errorStatus[response.status]?.includes(code)) {
      // Admission can be durable before its acknowledgement/response fails. A closed
      // 503 is not proof of rollback; retain the original submission for reconciliation.
      if (submission !== undefined && (code === "admission_unavailable" || code === "dependency_unavailable")) {
        return { kind: "outcome-unknown" };
      }
      return { kind: code };
    }
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
    undefined, undefined, "application/vnd.maezo.assignment-context.v2+json",
  );
}

export function readAssignmentCandidates(context: AssignmentContext, signal: AbortSignal): Promise<AssignmentResult<AssignmentCandidates>> {
  if (!validateAssignmentContext(context, context.task_id)) return Promise.resolve({ kind: "invalid_request" });
  return request(`${prefix}/tasks/${encodeURIComponent(context.task_id)}/assignment-candidates`, signal, 200,
    (value) => validateAssignmentCandidates(value, context));
}

export function submitAssignment(
  submission: AssignmentSubmission,
  csrfToken: string,
  signal: AbortSignal,
): Promise<AssignmentResult<AssignmentAdmission>> {
  if (!csrfToken || !validSubmission(submission)) {
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
  submission?: AssignmentSubmission,
): Promise<AssignmentResult<AssignmentReceipt>> {
  if (!validReference(taskId) || !validReference(commandId)) {
    return Promise.resolve({ kind: "invalid_request" });
  }
  const query = new URLSearchParams({ task_id: taskId });
  return request(
    `${prefix}/commands/${encodeURIComponent(commandId)}${receipt ? "/receipt" : ""}?${query}`,
    signal,
    200,
    (value) => validateAssignmentReceipt(value, taskId, commandId, submission),
  );
}
