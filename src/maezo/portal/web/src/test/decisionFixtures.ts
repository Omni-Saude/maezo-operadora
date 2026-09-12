import { taskReadBindings, taskReadInputs } from "../taskReadBindings";
import type { DecisionContext } from "../decisionClient";
export const huge = "9".repeat(100);
export const digest = "a".repeat(64);
export function contextFixture(form = "auth_decisao"): DecisionContext {
  const binding = taskReadBindings.find((b) => b.form === form)!;
  return {
    schema_version: "portal-decision-context.v1", expected_membership_revision: huge,
    expected_authority_revision: huge, binding_digest: digest, valid_until: new Date(Date.now() + 60_000).toISOString(),
    snapshot: { schema_version: 1, snapshot_at: new Date(Date.now() - 1000).toISOString(),
      task_id: "task-1", process_definition_key: binding.process, process_definition_version: huge,
      process_definition_id: "definition-1", process_definition_digest: digest,
      task_definition_key: binding.task, form_key: binding.form, form_version: huge,
      form_digest: digest, form_source_status: binding.source, task_revision: huge,
      assignee_ref: "opaque-assignee", eligible_candidate_groups: ["medico-auditor"],
      evidence_revision: huge, evidence_digest: digest, engine_due_at: null,
      allowed_actions: ["decision"], allowed_inputs: [...taskReadInputs[binding.form]],
      read_only_evidence: form === "pagto_admissibilidade" ? { kind: "pagto_admissibilidade", valor_pagamento_cents: huge,
        dados_pagamento_validos: true, lastro_confirmado: false, duplicidade_suspeita: true,
        lastro_origem: "contas_adjudicacao_humana", lastro_decisor_id: "lastro-reference" } : null,
    },
  };
}
export function admissionFixture(commandId: string) {
  return { schema_version: 1, status: "pending", tenant: "protected-tenant", principal_ref: "protected-principal",
    workload_ref: "protected-workload", task_id: "task-1", command_id: commandId,
    audit_intent_ref: "audit", outbox_ref: "outbox", transaction_ref: "transaction",
    committed_at: new Date().toISOString(), payload_digest: digest, request_digest: digest };
}
export function receiptFixture(commandId: string, status = "committed") {
  return { schema_version: "human-public-receipt.v2", operation: "decision", status,
    task_id: "task-1", command_id: commandId, tenant: "protected-tenant", principal_ref: "protected-principal", workload_ref: "protected-workload",
    audit_intent_ref: "audit", audit_intent_hash: digest, payload_digest: digest,
    audit_result_ref: status === "pending" ? null : digest, resulting_task_revision: null,
    engine_receipt_ref: status === "committed" ? "engine-receipt" : null,
    engine_recorded_at: status === "committed" ? new Date().toISOString() : null,
    consumed_task_revision: status === "committed" ? huge : null,
    technical_code: status === "conflict" ? "REVISION_CONFLICT" : null };
}
export function jsonResponse(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
