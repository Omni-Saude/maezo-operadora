import type { components } from "./generated/api";
import { isPublicTaskSnapshot } from "./taskReadClient";
import { isCurrent, timestampMicroseconds } from "./taskReadTime";
import { formSchema, validWire, wireSchema } from "./decisionSchema";

type Schemas = components["schemas"];
export type DecisionContext = Schemas["DecisionContextResponse"];
export type Submission = Schemas["DecisionSubmission"];
export type Inputs = Submission["decision"]["inputs"];
export type Receipt = Schemas["DecisionReceiptResponse"];
export type Admission = Schemas["PendingDecisionAdmission"];
export type Failure = Schemas["PortalDecisionError"]["code"] | "invalid-response" | "outcome-unknown";
export type Result<T> = { kind: "success"; value: T } | { kind: Failure };
const prefix = "/api/v1/portal";

export function validateContext(value: unknown, taskId: string): DecisionContext | null {
  if (!validWire(value, wireSchema("DecisionContextResponse"))) return null;
  const c = value as DecisionContext, s = c.snapshot;
  if (c.schema_version !== "portal-decision-context.v1" || s.task_id !== taskId ||
      !isCurrent(c.valid_until) || timestampMicroseconds(s.snapshot_at)! >= timestampMicroseconds(c.valid_until)! ||
      s.allowed_actions.length !== 1 || s.allowed_actions[0] !== "decision" ||
      !isPublicTaskSnapshot({ ...s, allowed_actions: [] })) return null;
  const form = formSchema(s.form_key);
  const keys = Object.keys(form?.properties ?? {}).filter((key) => key !== "kind");
  if (!form || keys.length !== s.allowed_inputs.length || !keys.every((key) => s.allowed_inputs.includes(key as never))) return null;
  return c;
}
export function makeSubmission(c: DecisionContext, inputs: unknown, commandId: string): Submission | null {
  if (!validateContext(c, c.snapshot.task_id)) return null;
  const s = c.snapshot;
  const value = { schema_version: "portal-decision-submission.v1", expected_authority_revision: c.expected_authority_revision,
    expected_binding_digest: c.binding_digest, decision: {
      schema_version: s.schema_version, command_id: commandId, task_id: s.task_id,
      process_definition_key: s.process_definition_key, process_definition_version: s.process_definition_version,
      process_definition_id: s.process_definition_id, process_definition_digest: s.process_definition_digest,
      task_definition_key: s.task_definition_key, form_key: s.form_key, form_version: s.form_version,
      form_digest: s.form_digest, expected_task_revision: s.task_revision,
      expected_evidence_revision: s.evidence_revision, expected_evidence_digest: s.evidence_digest,
      expected_membership_revision: c.expected_membership_revision, inputs,
    } };
  return validWire(value, wireSchema("DecisionSubmission")) &&
    typeof inputs === "object" && inputs !== null && "kind" in inputs && inputs.kind === s.form_key ? value as Submission : null;
}
export function validateReceipt(value: unknown, taskId: string, commandId: string): Receipt | null {
  if (!validWire(value, wireSchema("DecisionReceiptResponse"))) return null;
  const r = value as Receipt;
  if (r.schema_version !== "human-public-receipt.v2" || r.task_id !== taskId || r.command_id !== commandId || r.operation !== "decision" || r.resulting_task_revision != null) return null;
  if (r.engine_recorded_at != null && !/(?:Z|[+-]00:00)$/.test(r.engine_recorded_at)) return null;
  const engine = [r.engine_receipt_ref, r.engine_recorded_at, r.consumed_task_revision];
  if (r.status === "committed") return engine.every((v) => v != null) && r.audit_result_ref != null && r.technical_code == null ? r : null;
  if (engine.some((v) => v != null)) return null;
  return r.status === "pending" ? (r.audit_result_ref == null && r.technical_code == null ? r : null) :
    (r.audit_result_ref != null && r.technical_code != null ? r : null);
}
const errorStatus: Record<number, readonly Failure[]> = {
  400: ["invalid_request"], 422: ["invalid_decision"], 401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  409: ["revision_conflict"], 503: ["task_unavailable", "credential_scope_mismatch", "authority_unavailable", "form_projection_unavailable", "form_contract_unavailable", "admission_unavailable", "production_capabilities_unavailable", "dependency_unavailable"],
};
async function request<T>(path: string, signal: AbortSignal, status: number, validate: (v: unknown) => T | null, body?: Submission, csrf?: string): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetch(path, { method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json", "X-CSRF-Token": csrf! } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}), signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { kind: body ? "outcome-unknown" : "dependency_unavailable" };
  }
  let value: unknown;
  try { value = await response.json(); } catch { return { kind: body ? "outcome-unknown" : "invalid-response" }; }
  if (response.status === status) {
    const parsed = validate(value);
    return parsed ? { kind: "success", value: parsed } : { kind: body ? "outcome-unknown" : "invalid-response" };
  }
  if (validWire(value, wireSchema("PortalDecisionError"))) {
    const code = (value as Schemas["PortalDecisionError"]).code;
    if (errorStatus[response.status]?.includes(code)) return { kind: code };
  }
  return { kind: body ? "outcome-unknown" : "invalid-response" };
}
function validRef(value: unknown): boolean { return validWire(value, wireSchema("BrowserTaskDecision").properties!.task_id); }
export function readDecisionContext(taskId: string, signal: AbortSignal): Promise<Result<DecisionContext>> {
  if (!validRef(taskId)) return Promise.resolve({ kind: "invalid_request" });
  return request(`${prefix}/tasks/${encodeURIComponent(taskId)}/decision-context`, signal, 200, (v) => validateContext(v, taskId));
}
export function submitDecision(body: Submission, csrf: string, signal: AbortSignal): Promise<Result<Admission>> {
  if (!csrf || !validWire(body, wireSchema("DecisionSubmission"))) return Promise.resolve({ kind: "invalid_request" });
  return request(`${prefix}/tasks/${encodeURIComponent(body.decision.task_id)}/decisions`, signal, 202, (v) => {
    if (!validWire(v, wireSchema("PendingDecisionAdmission"))) return null;
    const a = v as Admission;
    return a.status === "pending" && a.task_id === body.decision.task_id && a.command_id === body.decision.command_id ? a : null;
  }, body, csrf);
}
export function readDecisionReceipt(taskId: string, commandId: string, signal: AbortSignal, receipt = false): Promise<Result<Receipt>> {
  if (!validRef(taskId) || !validRef(commandId)) return Promise.resolve({ kind: "invalid_request" });
  const query = new URLSearchParams({ task_id: taskId });
  return request(`${prefix}/commands/${encodeURIComponent(commandId)}${receipt ? "/receipt" : ""}?${query}`, signal, 200, (v) => validateReceipt(v, taskId, commandId));
}
