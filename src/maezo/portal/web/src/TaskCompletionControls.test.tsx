import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TaskCompletionControls } from "./TaskCompletionControls";
import { completionOutcomes, maxNotes } from "./completionClient";

const digest = "a".repeat(64);

function completion(resultado = "devolvido_agente") {
  return {
    schema: "portal-task-completion.v1",
    task_id: "task-opaque",
    process_definition_key: "SP-OP-ESCALATION-001",
    process_definition_version: "3",
    task_definition_key: "UT_TratarEscalonamento",
    form_key: "escalation",
    state: "completed",
    resultado,
    consumed_task_revision: "4",
    authority_revision: "7",
    evidence_revision: "3382",
    evidence_digest: digest,
    completed_at: "2026-09-28T11:45:00Z",
    audit_intent_ref: "b".repeat(64),
    audit_result_ref: "c".repeat(64),
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function controls(overrides: Partial<Parameters<typeof TaskCompletionControls>[0]> = {}) {
  return (
    <TaskCompletionControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-a"
      onSessionUnavailable={vi.fn()}
      {...overrides}
    />
  );
}

async function fill(user: ReturnType<typeof userEvent.setup>, outcome = "Devolvo ao agente, com instrução") {
  await user.click(screen.getByRole("radio", { name: outcome }));
  await user.type(screen.getByLabelText(/Instrução \/ notas de resolução/), "procure o pronto-socorro");
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("oferece os desfechos do contrato, e não uma lista escrita na tela", () => {
  render(controls());
  expect(completionOutcomes).toEqual(["resolvido_humano", "devolvido_agente", "emergencia_acionada"]);
  expect(screen.getAllByRole("radio")).toHaveLength(completionOutcomes.length);
  expect(screen.getByLabelText(/Instrução \/ notas de resolução/)).toHaveAttribute("maxLength", String(maxNotes));
});

it("mostra o contador N/limite com o limite do contrato (o mesmo maxLength do fio)", async () => {
  const user = userEvent.setup();
  render(controls());
  // O limite vem do OpenAPI gerado (`notas_resolucao.maxLength`), o mesmo número que o servidor
  // aplica depois da pseudonimização (`MAX_NOTES`); nunca um literal desta tela.
  expect(maxNotes).toBeGreaterThan(0);
  expect(screen.getByTestId("notas-contador")).toHaveTextContent(`0/${maxNotes}`);
  await user.type(screen.getByLabelText(/Instrução \/ notas de resolução/), "abc");
  expect(screen.getByTestId("notas-contador")).toHaveTextContent(`3/${maxNotes}`);
});

it("exige desfecho e instrução antes de permitir o envio", async () => {
  const user = userEvent.setup();
  render(controls());
  const submit = screen.getByRole("button", { name: "Concluir tarefa" });
  expect(submit).toBeDisabled();

  await user.click(screen.getByRole("radio", { name: "Resolvi o caso" }));
  expect(submit).toBeDisabled();

  await user.type(screen.getByLabelText(/Instrução \/ notas de resolução/), "   ");
  expect(submit).toBeDisabled();

  await user.type(screen.getByLabelText(/Instrução \/ notas de resolução/), "resolvido por telefone");
  expect(submit).toBeEnabled();
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});

it("avisa o que o sistema NÃO envia, mas só quando o desfecho é devolver ao agente", async () => {
  // Teste do Filipe em 02/10/2026: a nota "Entraremos em contato." foi barrada pelas cercas de saida e a
  // pessoa recebeu um texto generico, sem que a tela tivesse avisado o atendente.
  const user = userEvent.setup();
  render(controls());
  expect(screen.queryByTestId("devolucao-filtro")).not.toBeInTheDocument();

  await user.click(screen.getByRole("radio", { name: "Resolvi o caso" }));
  expect(screen.queryByTestId("devolucao-filtro")).not.toBeInTheDocument();

  await user.click(screen.getByRole("radio", { name: "Devolvo ao agente, com instrução" }));
  const aviso = screen.getByTestId("devolucao-filtro");
  expect(aviso).toHaveTextContent("não envia");
  expect(aviso).toHaveTextContent("entraremos em contato");
  expect(aviso).toHaveTextContent("R$");
});

it("envia o desfecho com CSRF e mostra os dois elos de auditoria", async () => {
  const user = userEvent.setup();
  const onCompleted = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(completion()));
  render(controls({ onCompleted }));

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("heading", { name: "Tarefa concluída" })).toBeTruthy());
  const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/v1/portal/tasks/task-opaque/completion");
  expect(init.method).toBe("POST");
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-secret");
  expect(JSON.parse(init.body as string)).toEqual({
    resultado: "devolvido_agente",
    notas_resolucao: "procure o pronto-socorro",
  });
  expect(screen.getByText("b".repeat(64))).toBeTruthy();
  expect(screen.getByText("c".repeat(64))).toBeTruthy();
  expect(onCompleted).toHaveBeenCalledOnce();
});

it("recusa uma resposta que fala de outro desfecho e não declara sucesso", async () => {
  const user = userEvent.setup();
  const onCompleted = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(completion("resolvido_humano")));
  render(controls({ onCompleted }));

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  expect(screen.queryByRole("heading", { name: "Tarefa concluída" })).toBeNull();
  expect(onCompleted).not.toHaveBeenCalled();
});

it("diz que a conclusão está desligada quando o portão responde 501", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({ schema: "portal-completion-error.v1", code: "completion_unavailable" }, 501),
  );
  render(controls());

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("desligada neste ambiente"),
  );
});

it("explica o 422 notes_too_long: a nota passou do limite depois de ocultar telefone, e-mail e documento", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({ schema: "portal-completion-error.v1", code: "notes_too_long" }, 422),
  );
  render(controls());

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Encurte o texto"));
  expect(screen.queryByRole("heading", { name: "Tarefa concluída" })).toBeNull();
});

it.each([
  ["promessa_sem_start", "entraremos em contato"],
  ["negativa_clinica", "avaliação clínica"],
  ["promessa_de_capacidade", "encaminhar, registrar, emitir ou agendar"],
  ["canal_nao_confirmado", "não está confirmado"],
  ["outra_regra", "regra de segurança"],
])("explica em português o 422 resumption_refused do grupo %s", async (group, trecho) => {
  const user = userEvent.setup();
  const onCompleted = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({ schema: "portal-completion-error.v1", code: "resumption_refused", refusal_group: group }, 422),
  );
  render(controls({ onCompleted }));

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("não enviaria esta instrução"));
  expect(screen.getByRole("alert").textContent).toContain(trecho);
  expect(screen.queryByRole("heading", { name: "Tarefa concluída" })).toBeNull();
  expect(onCompleted).not.toHaveBeenCalled();
  // O rascunho continua para ser reescrito.
  expect(screen.getByLabelText(/Instrução \/ notas de resolução/)).toHaveValue("procure o pronto-socorro");
});

it("um resumption_refused sem o grupo não cumpre o contrato e não vira sucesso", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({ schema: "portal-completion-error.v1", code: "resumption_refused" }, 422),
  );
  render(controls());

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("resposta inesperada"));
});

it("trata dependência indisponível como desfecho DESCONHECIDO, nunca como falha limpa", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({ schema: "portal-completion-error.v1", code: "completion_dependency_unavailable" }, 503),
  );
  render(controls());

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  // DL-0049: a intenção é gravada ANTES do efeito e o engine não aceita revisão esperada, então
  // um 503 não prova que a tarefa continuou aberta. Reenviar às cegas registraria um segundo
  // desfecho.
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("pode ter sido concluída"));
});

it("avisa a sessão perdida ao dono da sessão", async () => {
  const user = userEvent.setup();
  const onSessionUnavailable = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(
    jsonResponse({ schema: "portal-completion-error.v1", code: "session_unavailable" }, 401),
  );
  render(controls({ onSessionUnavailable }));

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(onSessionUnavailable).toHaveBeenCalledOnce());
});

it("descarta o rascunho ao trocar de tarefa", async () => {
  const user = userEvent.setup();
  const { rerender } = render(controls());
  await fill(user);
  expect(screen.getByRole("button", { name: "Concluir tarefa" })).toBeEnabled();

  rerender(controls({ taskId: "outra-tarefa" }));

  expect(screen.getByRole("button", { name: "Concluir tarefa" })).toBeDisabled();
  expect((screen.getByLabelText(/Instrução \/ notas de resolução/) as HTMLTextAreaElement).value).toBe("");
});

it("nunca fica preso em enviando quando algo inesperado quebra no meio", async () => {
  const user = userEvent.setup();
  // Uma resposta que quebra fora do try do cliente: antes, o erro subia e a tela
  // ficava para sempre com o campo e o botao desabilitados.
  vi.mocked(fetch).mockResolvedValueOnce({
    get headers(): never {
      throw new TypeError("resposta quebrada");
    },
  } as unknown as Response);
  render(controls());

  await fill(user);
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("pode ter sido concluída"));
  expect(screen.getByLabelText(/Instrução \/ notas de resolução/)).toBeEnabled();
  expect(screen.getByRole("button", { name: "Concluir tarefa" })).toBeEnabled();
});

it("dá ao texto altura de escrita, e não as duas linhas do padrão do navegador", () => {
  render(controls());
  expect(screen.getByLabelText(/Instrução \/ notas de resolução/)).toHaveAttribute("rows", "4");
});

// DL-0111: a execução do cancelamento a pedido no cadastro (`cancel_execucao`).
function cancelCompletion(resultado = "EXECUTADO") {
  return {
    ...completion(resultado),
    process_definition_key: "SP-OP-CANCEL-001",
    task_definition_key: "UT_ExecutarCancelamentoCadastro",
    form_key: "cancel_execucao",
  };
}

it("cancel_execucao: dois valores fechados do contrato e nenhum campo de texto", () => {
  render(controls({ formKey: "cancel_execucao" }));
  expect(screen.getAllByRole("radio")).toHaveLength(2);
  expect(screen.getByRole("radio", { name: "Cancelamento executado no sistema" })).toBeTruthy();
  expect(screen.getByRole("radio", { name: "Impedido (volta para a análise do jurídico)" })).toBeTruthy();
  expect(screen.queryByLabelText(/Instrução \/ notas de resolução/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Concluir tarefa" })).toBeDisabled();
});

it("cancel_execucao: envia só execucao_cadastro e mostra o desfecho registrado", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(cancelCompletion()));
  render(controls({ formKey: "cancel_execucao" }));

  await user.click(screen.getByRole("radio", { name: "Cancelamento executado no sistema" }));
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("heading", { name: "Tarefa concluída" })).toBeTruthy());
  const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
  expect(JSON.parse(init.body as string)).toEqual({ execucao_cadastro: "EXECUTADO" });
  expect(screen.getByRole("status")).toHaveTextContent("Cancelamento executado no sistema");
});

it("cancel_execucao: resposta com outro desfecho não vira sucesso", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(cancelCompletion("IMPEDIDO")));
  render(controls({ formKey: "cancel_execucao" }));

  await user.click(screen.getByRole("radio", { name: "Cancelamento executado no sistema" }));
  await user.click(screen.getByRole("button", { name: "Concluir tarefa" }));

  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  expect(screen.queryByRole("heading", { name: "Tarefa concluída" })).not.toBeInTheDocument();
});
