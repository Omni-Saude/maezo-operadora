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
