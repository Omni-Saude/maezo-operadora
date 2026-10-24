import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  createStaffOperationsClient,
  validateStaffCaseDependencies,
  validateStaffCommandBasis,
  validateStaffCommandReceipt,
} from "./staffOperationsClient";

const taskRef = "task_escalation_000000000001";
const commandRef = "command_000000000001";
const digest = "a".repeat(64);

function context() {
  return {
    schema_version: "portal-assignment-context.v2",
    task_id: taskRef,
    process_definition_key: "process_escalation_v3",
    process_definition_version: "3",
    process_definition_id: "process_escalation_v3:12:0001",
    process_definition_digest: digest,
    task_definition_key: "UT_TratarEscalonamento",
    form_key: "escalation",
    form_version: "4",
    form_digest: digest,
    expected_task_revision: "5",
    expected_evidence_revision: "9",
    expected_evidence_digest: digest,
    expected_membership_revision: "3",
    expected_authority_revision: "11",
    assignee_ref: "opaque_assignee_000000001",
    allowed_operations: ["claim", "release"],
    valid_until: new Date(Date.now() + 60_000).toISOString(),
    binding_ref: "binding_000000000000001",
    binding_version: "3",
    binding_digest: digest,
    policy_ref: "policy_00000000000001",
    policy_version: "2",
    policy_digest: digest,
    source_revision: "7",
    generation_digest: digest,
  } as const;
}

function taskContext() {
  return {
    schema: "portal-task-context.v1",
    task_id: taskRef,
    etapa: "atendimento",
    motivo_categoria: "autorizacao",
    severidade: "alta",
    prioridade: "P1",
    grupo_atendimento: "grupo-atendimento-1",
    aberto_em: "2026-10-24T12:00:00Z",
    ack_vence_em: "2026-10-24T13:00:00Z",
    resolucao_vence_em: "2026-10-24T15:00:00Z",
    resumo_contexto: "Resumo da colega que abriu o caso.",
    observed_at: new Date(Date.now() + 1_000).toISOString(),
  } as const;
}

function receipt() {
  return {
    schema_version: "human-public-assignment-receipt.v1",
    tenant: "test-tenant",
    task_id: taskRef,
    command_id: commandRef,
    status: "pending",
    operation: "claim",
    principal_ref: "opaque-principal",
    payload_digest: digest,
    workload_ref: "workload_0000000000001",
    audit_intent_ref: "audit_intent_00000001",
    audit_intent_hash: digest,
    audit_result_ref: null,
    engine_receipt_ref: null,
    engine_recorded_at: null,
    consumed_task_revision: null,
    resulting_task_revision: null,
    technical_code: null,
    command_schema: "human-assignment.v2",
    binding_ref: "binding_000000000000001",
    binding_version: "3",
    binding_digest: digest,
    policy_ref: "policy_00000000000001",
    policy_version: "2",
    policy_digest: digest,
    source_revision: "7",
    generation_digest: digest,
    target_ref: null,
    target_membership_revision: null,
    prior_assignee_ref: null,
    resulting_assignee_ref: null,
    assignment_disposition: null,
  } as const;
}

// The legacy v1 assignment receipt carries no operation on the wire (the server strips it)
// and none of the governed command pins: its closed shape is the plain public receipt.
function legacyReceipt() {
  return {
    schema_version: "human-public-receipt.v1",
    tenant: "test-tenant",
    task_id: taskRef,
    command_id: commandRef,
    status: "pending",
    principal_ref: "opaque-principal",
    payload_digest: digest,
    workload_ref: "workload_0000000000001",
    audit_intent_ref: "audit_intent_00000001",
    audit_intent_hash: digest,
  } as const;
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

it("consulta somente leitura a base de comandos na rota GET montada", async () => {
  const basis = context();
  vi.mocked(fetch).mockResolvedValue(response(basis));
  const result = await createStaffOperationsClient().readCommandBasis(taskRef, new AbortController().signal);
  expect(result).toEqual({ kind: "success", value: basis });
  expect(fetch).toHaveBeenCalledWith(
    `/api/v1/portal/tasks/${taskRef}/assignment-context`,
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/vnd.maezo.assignment-context.v2+json" },
    }),
  );
  // Prova de leitura pura: o init carrega só o par Accept; nenhum corpo, nenhum CSRF.
  const basisInit: RequestInit | undefined = vi.mocked(fetch).mock.calls[0]?.[1];
  expect(basisInit).toBeDefined();
  expect("body" in (basisInit as RequestInit)).toBe(false);
  expect(JSON.stringify(basisInit)).not.toMatch(/csrf|claim|release|submit/i);
});

it("consulta somente leitura as dependências do contexto da tarefa", async () => {
  const dependencies = taskContext();
  vi.mocked(fetch).mockResolvedValue(response(dependencies));
  const result = await createStaffOperationsClient().readCaseDependencies(taskRef, new AbortController().signal);
  expect(result).toEqual({ kind: "success", value: dependencies });
  expect(fetch).toHaveBeenCalledWith(
    `/api/v1/portal/tasks/${taskRef}/context`,
    expect.objectContaining({ method: "GET", redirect: "error", headers: { Accept: "application/json" } }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/"body"|csrf|complete|cancel/i);
});

it("consulta somente leitura o recibo público do comando", async () => {
  vi.mocked(fetch).mockResolvedValue(response(receipt()));
  const result = await createStaffOperationsClient().readCommandReceipt(
    taskRef,
    commandRef,
    new AbortController().signal,
  );
  expect(result).toEqual({ kind: "success", value: receipt() });
  expect(fetch).toHaveBeenCalledWith(
    `/api/v1/portal/commands/${commandRef}/receipt?task_id=${taskRef}`,
    expect.objectContaining({ method: "GET", redirect: "error" }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/"body"|csrf|submission/i);
});

it("recusa referência fora do formato opaco sem chamar o BFF", async () => {
  const client = createStaffOperationsClient();
  const signal = new AbortController().signal;
  expect((await client.readCommandBasis("tarefa com espaço", signal)).kind).toBe("failure");
  expect((await client.readCaseDependencies("", signal)).kind).toBe("failure");
  expect((await client.readCommandReceipt(taskRef, "protocolo/malicioso", signal)).kind).toBe("failure");
  expect(fetch).not.toHaveBeenCalled();
});

it("mapeia erros fechados dos dois planos de rota", async () => {
  const client = createStaffOperationsClient();
  const signal = new AbortController().signal;
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ schema_version: "portal-decision-error.v1", code: "operation_forbidden" }, 403))
    .mockResolvedValueOnce(response({ schema: "portal-context-error.v1", code: "context_unavailable" }, 501))
    .mockResolvedValueOnce(
      response({ schema_version: "portal-decision-error.v1", code: "operation_forbidden" }, 503),
    );
  expect(await client.readCommandBasis(taskRef, signal)).toEqual({
    kind: "failure",
    failure: "operation_forbidden",
  });
  expect(await client.readCaseDependencies(taskRef, signal)).toEqual({
    kind: "failure",
    failure: "context_unavailable",
  });
  // Um código que a linha de status não nomeia é recusado, nunca traduzido para outro erro.
  expect(await client.readCommandBasis(taskRef, signal)).toEqual({
    kind: "failure",
    failure: "invalid-response",
  });
});

it("recusa resposta sobre outra tarefa, wire driftado e corpo fora do contrato", async () => {
  const client = createStaffOperationsClient();
  const signal = new AbortController().signal;
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ ...context(), task_id: "task_outra_000000000000001" }))
    .mockResolvedValueOnce(response({ ...taskContext(), etapa: "urgente" }))
    .mockResolvedValueOnce(response({ surpresa: true }));
  expect((await client.readCommandBasis(taskRef, signal)).kind).toBe("failure");
  expect((await client.readCaseDependencies(taskRef, signal)).kind).toBe("failure");
  expect((await client.readCommandReceipt(taskRef, commandRef, signal)).kind).toBe("failure");
});

it("valida os validadores fechados direto, inclusive o recibo legado sem operação", () => {
  const basis = context();
  expect(validateStaffCommandBasis(basis, taskRef)).toEqual(basis);
  expect(validateStaffCommandBasis({ ...basis, allowed_operations: ["claim", "claim"] }, taskRef)).toBeNull();
  const dependencies = taskContext();
  expect(validateStaffCaseDependencies(dependencies, taskRef)).toEqual(dependencies);
  expect(validateStaffCaseDependencies({ ...taskContext(), task_id: "task_outra_000000000000001" }, taskRef)).toBeNull();
  expect(validateStaffCommandReceipt(receipt(), taskRef, commandRef)).toEqual(receipt());
  expect(validateStaffCommandReceipt(legacyReceipt(), taskRef, commandRef)).toEqual(legacyReceipt());
  // Recibo sobre outro comando não é recibo desta tela, mesmo com wire válido.
  expect(validateStaffCommandReceipt({ ...receipt(), command_id: "command_outra_00000001" }, taskRef, commandRef)).toBeNull();
});
