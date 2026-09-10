import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  makeAssignmentSubmission,
  readAssignmentContext,
  submitAssignment,
  validateAssignmentReceipt,
  type AssignmentContext,
} from "./assignmentClient";

const digest = "a".repeat(64);
const huge = "900719925474099312345678901234567890";

function context(overrides: Partial<AssignmentContext> = {}): AssignmentContext {
  return {
    schema_version: "portal-assignment-context.v1",
    task_id: "task-opaque",
    process_definition_key: "SP-OP-AUTH-001",
    process_definition_version: huge,
    process_definition_id: "AUTH:opaque",
    process_definition_digest: digest,
    task_definition_key: "UT_AnaliseMedicoAuditor",
    form_key: "auth_decisao",
    form_version: huge,
    form_digest: digest,
    expected_task_revision: huge,
    expected_evidence_revision: huge,
    expected_evidence_digest: digest,
    expected_membership_revision: huge,
    expected_authority_revision: huge,
    assignee_ref: null,
    allowed_operations: ["claim"],
    valid_until: "2026-09-10T15:05:00Z",
    ...overrides,
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.setSystemTime(new Date("2026-09-10T15:00:00Z"));
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("preserva todos os pins e revisões do contexto aprovado sem autoridade do navegador", () => {
  const value = context();
  const submission = makeAssignmentSubmission(value, "claim", "command-opaque");
  expect(submission).toEqual({
    schema_version: "portal-assignment-submission.v1",
    command: {
      schema_version: 1,
      command_id: "command-opaque",
      operation: "claim",
      task_id: value.task_id,
      process_definition_key: value.process_definition_key,
      process_definition_version: huge,
      process_definition_id: value.process_definition_id,
      process_definition_digest: digest,
      task_definition_key: value.task_definition_key,
      form_key: value.form_key,
      form_version: huge,
      form_digest: digest,
      expected_task_revision: huge,
      expected_evidence_revision: huge,
      expected_evidence_digest: digest,
      expected_membership_revision: huge,
      expected_authority_revision: huge,
    },
  });
  expect(JSON.stringify(submission)).not.toMatch(/tenant|principal|actor|roles|assignee_ref/);
  expect(makeAssignmentSubmission(value, "release", "command-opaque")).toBeNull();
});

it("recusa contexto vencido, operação duplicada e resposta para outra tarefa", async () => {
  expect(makeAssignmentSubmission(context({ valid_until: "2026-09-10T14:59:59Z" }), "claim", "command-opaque")).toBeNull();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse(context({ allowed_operations: ["claim", "claim"] })),
  );
  await expect(readAssignmentContext("task-opaque", new AbortController().signal)).resolves.toEqual({
    kind: "invalid-response",
  });
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(context({ task_id: "task-other" })));
  await expect(readAssignmentContext("task-opaque", new AbortController().signal)).resolves.toEqual({
    kind: "invalid-response",
  });
});

it("envia somente o wrapper gerado por POST same-origin e aceita apenas admissão pendente correlata", async () => {
  const submission = makeAssignmentSubmission(context(), "claim", "command-opaque")!;
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({
      schema_version: 1,
      transaction_ref: "transaction-opaque",
      command_id: "command-opaque",
      tenant: "tenant-hidden",
      principal_ref: "principal-hidden",
      task_id: "task-opaque",
      workload_ref: "workload-opaque",
      audit_intent_ref: "audit-opaque",
      outbox_ref: "outbox-opaque",
      committed_at: "2026-09-10T15:00:01Z",
      status: "pending",
    }, 202),
  );
  await expect(
    submitAssignment(submission, "csrf-secret", new AbortController().signal),
  ).resolves.toMatchObject({ kind: "success", value: { status: "pending" } });
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe("/api/v1/portal/tasks/task-opaque/assignments");
  expect(init).toMatchObject({
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": "csrf-secret",
    },
  });
  expect(JSON.parse(String(init?.body))).toEqual(submission);
});

it("não converte 202 nem recibo pendente em execução e exige prova completa no committed", () => {
  const base = {
    schema_version: "human-public-receipt.v1" as const,
    tenant: "tenant-hidden",
    task_id: "task-opaque",
    command_id: "command-opaque",
    payload_digest: digest,
    principal_ref: "principal-hidden",
    workload_ref: "workload-opaque",
    status: "pending" as const,
    audit_intent_ref: "audit-opaque",
    audit_intent_hash: digest,
    audit_result_ref: null,
    engine_receipt_ref: null,
    engine_recorded_at: null,
    consumed_task_revision: null,
    resulting_task_revision: null,
    technical_code: null,
  };
  expect(validateAssignmentReceipt(base, "task-opaque", "command-opaque")?.status).toBe("pending");
  expect(validateAssignmentReceipt({
    ...base,
    status: "committed",
    audit_result_ref: digest,
    engine_receipt_ref: "engine-opaque",
    engine_recorded_at: "2026-09-10T15:00:02Z",
    consumed_task_revision: huge,
  }, "task-opaque", "command-opaque")).toBeNull();
  expect(validateAssignmentReceipt({
    ...base,
    status: "committed",
    audit_result_ref: digest,
    engine_receipt_ref: "engine-opaque",
    engine_recorded_at: "2026-09-10T15:00:02Z",
    consumed_task_revision: huge,
    resulting_task_revision: huge,
  }, "task-opaque", "command-opaque")?.status).toBe("committed");
});
