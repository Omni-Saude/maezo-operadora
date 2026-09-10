import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EmployeeQueues } from "./EmployeeQueues";

const digest = "b".repeat(64);
const huge = "900719925474099312345678901234567890";

function freshness(second = "01") {
  return {
    state: "current",
    observed_at: `2026-09-09T15:00:${second}Z`,
    source_observed_at: "2026-09-09T15:00:00Z",
    valid_until: "2026-09-09T15:00:59Z",
    refresh_after_seconds: 10,
  };
}

function queuePage(
  queue: "mine" | "team" = "mine",
  taskId = "task-1",
  definition = "UT_AnaliseMedicoAuditor",
  nextCursor: string | null = null,
) {
  return {
    schema: "portal-task-queue.v1",
    queue,
    items: [
      {
        task_id: taskId,
        process_definition_key: queue === "mine" ? "SP-OP-AUTH-001" : "SP-OP-ESCALATION-001",
        task_definition_key: definition,
        task_revision: huge,
        ownership: queue === "mine" ? "self" : "unassigned",
        engine_due_at: null,
        snapshot_at: "2026-09-09T15:00:00Z",
      },
    ],
    next_cursor: nextCursor,
    freshness: freshness(),
  };
}

function taskResponse() {
  return {
    schema: "portal-task-read.v1",
    task: {
      schema_version: 1,
      snapshot_at: "2026-09-09T15:00:00Z",
      task_id: "task-1",
      process_definition_key: "SP-OP-PAGTO-001",
      process_definition_version: huge,
      process_definition_id: "PAGTO:opaque",
      process_definition_digest: digest,
      task_definition_key: "UT_AnaliseAdmissibilidade",
      form_key: "pagto_admissibilidade",
      form_version: huge,
      form_digest: digest,
      form_source_status: "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
      task_revision: huge,
      assignee_ref: null,
      eligible_candidate_groups: ["coordenacao-financeira"],
      evidence_revision: huge,
      evidence_digest: digest,
      engine_due_at: null,
      allowed_actions: [],
      allowed_inputs: ["decisao_admissibilidade", "justificativa_recusa"],
      read_only_evidence: {
        kind: "pagto_admissibilidade",
        valor_pagamento_cents: huge,
        dados_pagamento_validos: true,
        lastro_confirmado: false,
        duplicidade_suspeita: true,
        lastro_origem: "contas_adjudicacao_humana",
        lastro_decisor_id: "decisor-opaque",
      },
    },
    freshness: freshness(),
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.setSystemTime(new Date("2026-09-09T15:00:02Z"));
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("abre Meu trabalho em tabela semântica e mostra freshness textual", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(queuePage()));
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByRole("table", { name: "Tarefas elegíveis da fila selecionada" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Responsabilidade" })).toBeInTheDocument();
  expect(screen.getByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" })).toBeInTheDocument();
  expect(screen.getByText("Minha responsabilidade")).toBeInTheDocument();
  expect(screen.getByText(huge)).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Atualizada em");
  expect(screen.getByText(/Fonte observada em/)).toBeInTheDocument();
});

it("navega para Filas da equipe e ignora a resposta antiga de Meu trabalho", async () => {
  const mine = deferred<Response>();
  vi.mocked(fetch)
    .mockReturnValueOnce(mine.promise)
    .mockResolvedValueOnce(jsonResponse(queuePage("team", "task-2", "UT_ResolverEscalation")));
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Filas da equipe" }));
  expect(await screen.findByRole("rowheader", { name: "UT_ResolverEscalation" })).toBeInTheDocument();
  mine.resolve(jsonResponse(queuePage()));
  await act(async () => Promise.resolve());
  expect(screen.queryByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Filas da equipe" })).toHaveAttribute("aria-current", "page");
});

it("atualiza a fila visível a cada dez segundos", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage()))
    .mockResolvedValueOnce(jsonResponse({ ...queuePage(), freshness: freshness("11") }));
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await act(async () => Promise.resolve());
  expect(fetch).toHaveBeenCalledTimes(1);
  const priorFreshness = screen.getByRole("status").textContent;
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("status").textContent).not.toBe(priorFreshness);
});

it("não transforma falha em fila vazia e oferece retry seguro", async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse(
      { schema: "portal-read-error.v1", code: "read_dependency_unavailable" },
      503,
    ),
  );
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("temporariamente indisponível");
  expect(screen.queryByText("Nenhuma tarefa elegível nesta consulta.")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
});

it.each([
  [400, "invalid_request", "A solicitação da fila foi recusada"],
  [403, "employee_access_required", "não autoriza esta fila"],
  [404, "resource_unavailable", "não está mais disponível"],
] as const)("apresenta UX segura e específica para HTTP %s", async (status, code, message) => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ schema: "portal-read-error.v1", code }, status),
  );
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByText("Nenhuma tarefa elegível nesta consulta.")).not.toBeInTheDocument();
});

it("limpa a página vencida em 409 e reinicia sem reutilizar cursor", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage("mine", "task-1", "UT_AnaliseMedicoAuditor", "cursor_a")))
    .mockResolvedValueOnce(
      jsonResponse({ schema: "portal-read-error.v1", code: "refresh_required" }, 409),
    )
    .mockResolvedValueOnce(jsonResponse(queuePage("mine", "task-3", "UT_CoordenacaoAssume")));
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await screen.findByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" });
  await userEvent.click(screen.getByRole("button", { name: "Carregar mais" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("A fila mudou");
  expect(screen.queryByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Atualizar fila" }));
  expect(await screen.findByRole("rowheader", { name: "UT_CoordenacaoAssume" })).toBeInTheDocument();
  expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/api/v1/portal/tasks?queue=mine&limit=25");
});

it("abre apenas o snapshot público e preserva valores exatos sem ações de mutação", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage()))
    .mockResolvedValueOnce(jsonResponse(taskResponse()));
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Abrir detalhes de UT_AnaliseMedicoAuditor" }));
  expect(await screen.findByRole("heading", { name: "UT_AnaliseAdmissibilidade" })).toBeInTheDocument();
  const detail = screen.getByRole("region", { name: "UT_AnaliseAdmissibilidade" });
  expect(within(detail).getAllByText(huge)).toHaveLength(5);
  expect(screen.getByText(/não confirma obrigação nem autoriza pagamento/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /claim|assumir|liberar|decidir/i })).not.toBeInTheDocument();
  expect(screen.getByText(/Esta consulta é somente leitura/)).toBeInTheDocument();
});

it("não remove o detalhe aberto durante a atualização periódica da fila", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage()))
    .mockResolvedValueOnce(jsonResponse(taskResponse()))
    .mockResolvedValueOnce(jsonResponse(queuePage("mine", "task-2", "UT_Atualizada")));
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await act(async () => Promise.resolve());
  await act(async () => {
    screen.getByRole("button", { name: "Abrir detalhes de UT_AnaliseMedicoAuditor" }).click();
    await Promise.resolve();
  });
  expect(screen.getByRole("heading", { name: "UT_AnaliseAdmissibilidade" })).toBeInTheDocument();
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(screen.getByRole("rowheader", { name: "UT_Atualizada" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "UT_AnaliseAdmissibilidade" })).toBeInTheDocument();
});

it("remove conteúdo da fila quando a sessão expira no endpoint de leitura", async () => {
  const onSessionUnavailable = vi.fn();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage()))
    .mockResolvedValueOnce(
      jsonResponse({ schema: "portal-read-error.v1", code: "session_unavailable" }, 401),
    );
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={onSessionUnavailable} />);
  await screen.findByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" });
  await userEvent.click(screen.getByRole("button", { name: "Atualizar agora" }));
  await waitFor(() => expect(onSessionUnavailable).toHaveBeenCalledOnce());
  expect(screen.queryByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" })).not.toBeInTheDocument();
});

it("remove fila e detalhe quando o servidor revoga o acesso de colaborador", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage()))
    .mockResolvedValueOnce(jsonResponse(taskResponse()))
    .mockResolvedValueOnce(
      jsonResponse({ schema: "portal-read-error.v1", code: "employee_access_required" }, 403),
    );
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Abrir detalhes de UT_AnaliseMedicoAuditor" }));
  expect(await screen.findByRole("heading", { name: "UT_AnaliseAdmissibilidade" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Atualizar agora" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("não autoriza esta fila");
  expect(screen.queryByRole("heading", { name: "UT_AnaliseAdmissibilidade" })).not.toBeInTheDocument();
});

it("cancela a leitura pendente ao desmontar o workspace", () => {
  const pending = deferred<Response>();
  vi.mocked(fetch).mockReturnValue(pending.promise);
  const view = render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
  expect(signal?.aborted).toBe(false);
  view.unmount();
  expect(signal?.aborted).toBe(true);
});

it("cancela e ignora a resposta ligada à sessão anterior", async () => {
  const oldSession = deferred<Response>();
  vi.mocked(fetch)
    .mockReturnValueOnce(oldSession.promise)
    .mockResolvedValueOnce(jsonResponse(queuePage("mine", "task-2", "UT_CoordenacaoAssume")));
  const view = render(
    <EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />,
  );
  const firstSignal = vi.mocked(fetch).mock.calls[0][1]?.signal;
  view.rerender(<EmployeeQueues sessionBinding="session-b" onSessionUnavailable={vi.fn()} />);
  expect(firstSignal?.aborted).toBe(true);
  expect(await screen.findByRole("rowheader", { name: "UT_CoordenacaoAssume" })).toBeInTheDocument();
  oldSession.resolve(jsonResponse(queuePage()));
  await act(async () => Promise.resolve());
  expect(screen.queryByRole("rowheader", { name: "UT_AnaliseMedicoAuditor" })).not.toBeInTheDocument();
});

function queueOwnershipAssignmentcontext() {
  return {
    schema_version: "portal-assignment-context.v2",
    binding_ref: "binding-opaque", binding_version: "1", binding_digest: digest,
    policy_ref: "policy-opaque", policy_version: "1", policy_digest: digest,
    source_revision: "1", generation_digest: digest,

    task_id: "task-1",
    process_definition_key: "SP-OP-AUTH-001",
    process_definition_version: "9007199254740993",
    process_definition_id: "AUTH:opaque",
    process_definition_digest: digest,
    task_definition_key: "UT_AnaliseMedicoAuditor",
    form_key: "auth_decisao",
    form_version: "9007199254740993",
    form_digest: digest,
    expected_task_revision: "9007199254740993",
    expected_evidence_revision: "9007199254740993",
    expected_evidence_digest: digest,
    expected_membership_revision: "9007199254740993",
    expected_authority_revision: "9007199254740993",
    assignee_ref: null,
    allowed_operations: ["claim"],
    valid_until: "2026-09-09T15:05:00Z",
  };
}

function queueOwnershipAdmission() {
  return {
    schema_version: 1,
    transaction_ref: "transaction-opaque",
    command_id: queueCommandId,
    tenant: "tenant-hidden",
    principal_ref: "principal-hidden",
    task_id: "task-1",
    workload_ref: "workload-opaque",
    audit_intent_ref: "audit-opaque",
    outbox_ref: "outbox-opaque",
    committed_at: "2026-09-09T15:00:01Z",
    status: "pending",
  };
}

function queueOwnershipReceipt(status: "pending" | "committed" = "pending") {
  const committed = status === "committed";
  return {
    schema_version: "human-public-assignment-receipt.v1",
    command_schema: "human-assignment.v2", operation: "claim",
    binding_ref: "binding-opaque", binding_version: "1", binding_digest: digest,
    policy_ref: "policy-opaque", policy_version: "1", policy_digest: digest,
    source_revision: "1", generation_digest: digest,
    target_ref: null, target_membership_revision: null,
    prior_assignee_ref: null, resulting_assignee_ref: committed ? "principal-hidden" : null,
    assignment_disposition: committed ? "changed" : null,

    tenant: "tenant-hidden",
    task_id: "task-1",
    command_id: queueCommandId,
    payload_digest: digest,
    principal_ref: "principal-hidden",
    workload_ref: "workload-opaque",
    status,
    audit_intent_ref: "audit-opaque",
    audit_intent_hash: digest,
    audit_result_ref: committed ? digest : null,
    engine_receipt_ref: committed ? "engine-opaque" : null,
    engine_recorded_at: committed ? "2026-09-09T15:00:02Z" : null,
    consumed_task_revision: committed ? "9007199254740993" : null,
    resulting_task_revision: committed ? "9007199254740993" : null,
    technical_code: null,
  };
}

const queueCommandId = "00000000-0000-4000-8000-000000000001";

it.each(["pending", "committed"] as const)("atualiza fila a10s e mantém comando %s além da validade do snapshot", async (status) => {
  vi.useFakeTimers();
  vi.spyOn(crypto, "randomUUID").mockReturnValue(queueCommandId);
  const queue = queuePage();
  queue.freshness.valid_until = "2026-09-09T15:00:08Z";
  const detail = taskResponse();
  detail.freshness.valid_until = "2026-09-09T15:00:08Z";
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(queue)).mockResolvedValueOnce(jsonResponse(detail))
    .mockResolvedValueOnce(jsonResponse(queueOwnershipAssignmentcontext())).mockResolvedValueOnce(jsonResponse(queueOwnershipAdmission(), 202));
  render(<EmployeeQueues sessionBinding="session-a" csrfToken="csrf-secret" onSessionUnavailable={vi.fn()} />);
  await act(async () => { await Promise.resolve(); });
  const click = async (name: string) => { await act(async () => { screen.getByRole("button", { name }).click(); }); };
  await click("Abrir detalhes de UT_AnaliseMedicoAuditor");
  await click("Consultar responsabilidade");
  await click("Assumir responsabilidade");
  if (status === "committed") {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(queueOwnershipReceipt("committed")));
    await click("Consultar recibo");
  }
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(queuePage("mine", "task-2", "UT_Atualizada")));
  await act(async () => { await vi.advanceTimersByTimeAsync(6001); });
  expect(screen.queryByRole("heading", { name: "UT_AnaliseAdmissibilidade" })).not.toBeInTheDocument();
  expect(screen.queryByText("decisor-opaque")).not.toBeInTheDocument();
  expect(screen.getByText(`Protocolo: ${queueCommandId}`)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(3999); });
  expect(screen.getByRole("rowheader", { name: "UT_Atualizada" })).toBeInTheDocument();
  expect(screen.getByText(`Protocolo: ${queueCommandId}`)).toBeInTheDocument();
  if (status === "committed") expect(screen.getByText(/Alteração executada e confirmada/)).toBeInTheDocument();
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ schema: "portal-read-error.v1", code: "employee_access_required" }, 403));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.queryByText(`Protocolo: ${queueCommandId}`)).not.toBeInTheDocument();
  expect(screen.queryByRole("rowheader")).not.toBeInTheDocument();
});
