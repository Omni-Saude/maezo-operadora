import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  makeAssignmentSubmission,
  readAssignmentContext,
  readAssignmentCandidates,
  submitAssignment,
  validateAssignmentReceipt,
  type AssignmentContext,
} from "./assignmentClient";

const digest = "a".repeat(64);
const huge = "9007199254740993";

function context(overrides: Partial<AssignmentContext> = {}): AssignmentContext {
  return {
    schema_version: "portal-assignment-context.v2",
    binding_ref: "binding-opaque", binding_version: "1", binding_digest: digest,
    policy_ref: "policy-opaque", policy_version: "1", policy_digest: digest,
    source_revision: "1", generation_digest: digest,

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
    schema_version: "portal-assignment-submission.v2",
    command: {
      schema_version: 2,
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
      expected_assignee_ref: null,
      expected_binding_ref: "binding-opaque", expected_binding_version: "1", expected_binding_digest: digest,
      expected_policy_ref: "policy-opaque", expected_policy_version: "1", expected_policy_digest: digest,
      expected_source_revision: "1", expected_generation_digest: digest,
      target_ref: null, expected_target_membership_revision: null,
    },
  });
  expect(JSON.stringify(submission)).not.toMatch(/tenant|principal|actor|roles/);
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
    schema_version: "human-public-assignment-receipt.v1" as const,
    command_schema: "human-assignment.v2", operation: "claim",
    binding_ref: "binding-opaque", binding_version: "1", binding_digest: digest,
    policy_ref: "policy-opaque", policy_version: "1", policy_digest: digest,
    source_revision: "1", generation_digest: digest,
    target_ref: null, target_membership_revision: null,
    prior_assignee_ref: null, resulting_assignee_ref: null,
    assignment_disposition: null,

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
    resulting_assignee_ref: "principal-hidden", assignment_disposition: "changed",
    audit_result_ref: digest,
    engine_receipt_ref: "engine-opaque",
    engine_recorded_at: "2026-09-10T15:00:02Z",
    consumed_task_revision: huge,
  }, "task-opaque", "command-opaque")).toBeNull();
  expect(validateAssignmentReceipt({
    ...base,
    status: "committed",
    resulting_assignee_ref: "principal-hidden", assignment_disposition: "changed",
    audit_result_ref: digest,
    engine_receipt_ref: "engine-opaque",
    engine_recorded_at: "2026-09-10T15:00:02Z",
    consumed_task_revision: huge,
    resulting_task_revision: huge,
  }, "task-opaque", "command-opaque")?.status).toBe("committed");
});

it.each(["admission_unavailable", "dependency_unavailable"] as const)("preserva incerteza de POST503 %s sem inferir rollback", async (code) => {
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ schema_version: "portal-decision-error.v1", code }, 503));
  const submission = makeAssignmentSubmission(context(), "claim", "command-opaque")!;
  await expect(submitAssignment(submission, "csrf", new AbortController().signal)).resolves.toEqual({ kind: "outcome-unknown" });
});

it("negocia v2 e recusa revisões não canônicas ou acima do limite nativo", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(context()));
  expect((await readAssignmentContext("task-opaque", new AbortController().signal)).kind).toBe("success");
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({ Accept: "application/vnd.maezo.assignment-context.v2+json" });
  for (const version of ["01", "-1", "9223372036854775808", "0"]) {
    expect(makeAssignmentSubmission(context({ binding_version: version }), "claim", "command")).toBeNull();
  }
  expect(makeAssignmentSubmission(context({ allowed_operations: ["release"], assignee_ref: "previous" }), "release", "command")?.command)
    .toMatchObject({ operation: "release", expected_assignee_ref: "previous", target_ref: null });
});

function candidatePage(basis = context({ allowed_operations: ["reassign"], assignee_ref: "previous" })) {
  const candidates = [{ target_membership_revision: huge, target_ref: "target-opaque" }];
  return { schema_version: "portal-assignment-candidates.v1", context: basis, candidates,
    candidate_count: "1", candidate_digest: createHash("sha256").update(JSON.stringify(candidates)).digest("hex"),
    valid_until: "2026-09-10T15:04:00Z" };
}
it("vincula destinatário à lista completa verificada e aos mesmos pins", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const basis = context({ allowed_operations: ["reassign"], assignee_ref: "previous" });
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(candidatePage(basis)));
  const result = await readAssignmentCandidates(basis, new AbortController().signal);
  expect(result.kind).toBe("success");
  if (result.kind !== "success") throw new Error("candidate failure");
  const command = makeAssignmentSubmission(basis, "reassign", "command", result.value, "target-opaque")?.command;
  expect(command).toMatchObject({ operation: "reassign", expected_assignee_ref: "previous",
    target_ref: "target-opaque", expected_target_membership_revision: huge,
    expected_generation_digest: digest, expected_binding_digest: digest, expected_policy_digest: digest });
  expect(makeAssignmentSubmission(basis, "reassign", "command", result.value, "invented-target")).toBeNull();
  expect(makeAssignmentSubmission(basis, "reassign", "command", candidatePage(basis) as never, "target-opaque")).toBeNull();
  expect(makeAssignmentSubmission({ ...basis, generation_digest: "b".repeat(64) }, "reassign", "command", result.value, "target-opaque")).toBeNull();
  vi.setSystemTime(new Date("2026-09-10T15:04:01Z"));
  expect(makeAssignmentSubmission(basis, "reassign", "command", result.value, "target-opaque")).toBeNull();
});
it("recusa candidatos incompletos, alterados ou de outra geração", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const basis = context({ allowed_operations: ["reassign"] });
  for (const page of [
    { ...candidatePage(basis), candidate_count: "2" },
    { ...candidatePage(basis), candidate_digest: "b".repeat(64) },
    candidatePage({ ...basis, generation_digest: "b".repeat(64) }),
    { ...candidatePage(basis), valid_until: "2026-09-10T15:06:00Z" },
  ]) {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(page));
    expect((await readAssignmentCandidates(basis, new AbortController().signal)).kind).toBe("invalid-response");
  }
});
