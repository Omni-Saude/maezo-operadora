import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DecisionWorkspace, fieldLabel } from "./DecisionWorkspace";
import { EmployeeQueues } from "./EmployeeQueues";
import { taskReadBindings, taskReadInputs } from "./taskReadBindings";
import { admissionFixture, contextFixture, huge, jsonResponse, receiptFixture } from "./test/decisionFixtures";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
function setup(form = "auth_decisao") {
  const c = contextFixture(form), fetcher = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(c)));
  vi.stubGlobal("fetch", fetcher);
  const expired = vi.fn();
  const view = render(<DecisionWorkspace taskId="task-1" csrfToken="csrf" onSessionUnavailable={expired} />);
  return { ...view, fetcher, c, expired };
}
it.each(taskReadBindings)("mostra controles explícitos somente do formulário $process/$task", async (binding) => {
  const c = contextFixture(binding.form); c.snapshot.task_definition_key = binding.task;
  vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(c))));
  render(<DecisionWorkspace taskId="task-1" csrfToken="csrf" onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Revisar decisão" })).toBeEnabled();
  for (const key of taskReadInputs[binding.form]) {
    expect(screen.getAllByText(new RegExp(fieldLabel(key), "i")).length).toBeGreaterThan(0);
  }
  expect(screen.queryByRole("button", { name: "Enviar decisão" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/actor_id|tenant_id|aprovador_tier|variables/i)).not.toBeInTheDocument();
});
async function reviewAuth() {
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText("Decisão do auditor (obrigatório)"), "APROVAR");
  await user.click(screen.getByRole("button", { name: "Revisar decisão" }));
  expect(screen.getByRole("heading", { name: "Revise antes de enviar" })).toHaveFocus();
  expect(screen.getByRole("button", { name: "Enviar decisão" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /Revisei os dados/ }));
  expect(screen.getByRole("checkbox", { name: /Revisei os dados/ })).toHaveFocus();
  return user;
}
it("exige revisão, bloqueia duplo envio e separa 202 da execução auditada", async () => {
  const { fetcher } = setup(); const user = await reviewAuth();
  let acknowledge!: (value: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { acknowledge = resolve; }));
  await user.dblClick(screen.getByRole("button", { name: "Enviar decisão" }));
  expect(fetcher.mock.calls).toHaveLength(2);
  const body = JSON.parse(fetcher.mock.calls[1][1].body);
  expect(body.decision.expected_task_revision).toBe(huge);
  await act(async () => acknowledge(jsonResponse(admissionFixture(body.decision.command_id), 202)));
  expect(await screen.findByText(/Decisão recebida e pendente/)).toBeVisible();
  expect(screen.queryByText(/Execução confirmada/)).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/protected-tenant|protected-principal|protected-workload/);
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse(receiptFixture(body.decision.command_id))));
  await user.click(screen.getByRole("button", { name: "Consultar recibo" }));
  expect(await screen.findByText(/Execução confirmada pelo recibo/)).toBeVisible();
  expect(fetcher.mock.calls[2][0]).toContain(`/commands/${body.decision.command_id}/receipt?task_id=task-1`);
  expect(document.body.textContent).not.toMatch(/protected-tenant|protected-principal|protected-workload/);
});
it("preserva bytes e identidade no reenvio após perda de resposta", async () => {
  const { fetcher } = setup(); const user = await reviewAuth();
  fetcher.mockRejectedValueOnce(new Error("protected upstream exception"));
  await user.click(screen.getByRole("button", { name: "Enviar decisão" }));
  expect(await screen.findByText(/Resultado desconhecido. O envio pode/)).toBeVisible();
  const first = fetcher.mock.calls[1][1].body, body = JSON.parse(first);
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse(admissionFixture(body.decision.command_id), 202)));
  await user.click(screen.getByRole("button", { name: "Reenviar o mesmo comando" }));
  expect(await screen.findByText(/Decisão recebida e pendente/)).toBeVisible();
  expect(fetcher.mock.calls[2][1].body).toBe(first);
  expect(document.body.textContent).not.toContain("protected upstream exception");
});
it("recusa prova parcial sem mostrar conclusão", async () => {
  const { fetcher } = setup(); const user = await reviewAuth();
  fetcher.mockImplementationOnce((_url, options) => Promise.resolve(jsonResponse(admissionFixture(JSON.parse(options.body).decision.command_id), 202)));
  await user.click(screen.getByRole("button", { name: "Enviar decisão" }));
  await screen.findByText(/Decisão recebida e pendente/);
  const id = JSON.parse(fetcher.mock.calls[1][1].body).decision.command_id;
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse({ ...receiptFixture(id), audit_result_ref: null })));
  await user.click(screen.getByRole("button", { name: "Consultar comando" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("resposta inesperada");
  expect(screen.queryByText(/Execução confirmada/)).not.toBeInTheDocument();
});
it("mantém validação canônica no servidor e exige nova revisão após 422", async () => {
  const { fetcher } = setup(); const user = await reviewAuth();
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse({ schema_version: "portal-decision-error.v1", code: "invalid_decision" }, 422)));
  await user.click(screen.getByRole("button", { name: "Enviar decisão" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("não atende ao contrato");
  expect(screen.getByLabelText("Decisão do auditor (obrigatório)")).toHaveValue("APROVAR");
  expect(screen.queryByRole("button", { name: "Enviar decisão" })).not.toBeInTheDocument();
});
it("expiração elimina rascunho e revisão", async () => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-09-10T01:00:00Z"));
  const { c } = setup();
  await screen.findByRole("button", { name: "Revisar decisão" });
  fireEvent.change(screen.getByLabelText("Decisão do auditor (obrigatório)"), { target: { value: "APROVAR" } });
  vi.setSystemTime(new Date(Date.parse(c.valid_until) + 1));
  fireEvent.submit(screen.getByRole("button", { name: "Revisar decisão" }).closest("form")!);
  expect(screen.queryByRole("button", { name: "Enviar decisão" })).not.toBeInTheDocument();
});
it("nega sessão e elimina os dados da tela", async () => {
  const { fetcher, expired } = setup(); const user = await reviewAuth();
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse({ schema_version: "portal-decision-error.v1", code: "authentication_unavailable" }, 401)));
  await user.click(screen.getByRole("button", { name: "Enviar decisão" }));
  await waitFor(() => expect(expired).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: "Revisar decisão" })).not.toBeInTheDocument();
  expect(screen.queryByText("APROVAR")).not.toBeInTheDocument();
});
it("indisponibilidade de produção não mostra formulário nem elegibilidade falsa", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ schema_version: "portal-decision-error.v1", code: "production_capabilities_unavailable" }, 503)));
  render(<DecisionWorkspace taskId="task-1" csrfToken="csrf" onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("ainda não está habilitado");
  expect(screen.queryByRole("button", { name: "Revisar decisão" })).not.toBeInTheDocument();
});
it("mantém evidência PAGTO somente leitura e não presume lastro", async () => {
  setup("pagto_admissibilidade");
  await screen.findByRole("button", { name: "Revisar decisão" });
  expect(screen.getByText(/não libera pagamento/)).toBeVisible();
  expect(screen.getByText(huge)).toBeVisible();
  expect(screen.queryByLabelText(/lastro|valor_pagamento/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Decisão de admissibilidade (obrigatório)")).toHaveValue("");
});
it("apaga respostas tardias ao desmontar", async () => {
  let finish!: (v: Response) => void;
  const fetcher = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; })); vi.stubGlobal("fetch", fetcher);
  const { unmount } = render(<DecisionWorkspace taskId="task-1" csrfToken="csrf" onSessionUnavailable={vi.fn()} />);
  unmount(); await act(async () => finish(jsonResponse(contextFixture())));
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  expect(screen.queryByRole("button", { name: "Revisar decisão" })).not.toBeInTheDocument();
});
it("abre decisão a partir da consulta Q1 sem presumir ação no snapshot", async () => {
  const c = contextFixture(); const now = new Date().toISOString();
  const fresh = { state: "current", observed_at: now, source_observed_at: now, valid_until: c.valid_until, refresh_after_seconds: 10 };
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url.endsWith("decision-context") ? c : url.includes("?queue=") ? {
    schema: "portal-task-queue.v1", queue: "mine", items: [{ task_id: "task-1", task_definition_key: c.snapshot.task_definition_key, process_definition_key: c.snapshot.process_definition_key, task_revision: huge, ownership: "self", engine_due_at: null, snapshot_at: now }], next_cursor: null, freshness: fresh,
  } : { schema: "portal-task-read.v1", task: { ...c.snapshot, allowed_actions: [] }, freshness: fresh })));
  vi.stubGlobal("fetch", fetcher); const user = userEvent.setup();
  render(<EmployeeQueues sessionBinding="binding" csrfToken="csrf" onSessionUnavailable={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: /Abrir detalhes/ }));
  await user.click(await screen.findByRole("button", { name: "Preparar decisão humana" }));
  expect(await screen.findByRole("button", { name: "Revisar decisão" })).toBeVisible();
  expect(fetcher.mock.calls.at(-1)![0]).toBe("/api/v1/portal/tasks/task-1/decision-context");
});
it("retira confirmação antiga quando uma nova consulta falha", async () => {
  const { fetcher } = setup(); const user = await reviewAuth();
  fetcher.mockImplementationOnce((_url, options) => Promise.resolve(jsonResponse(admissionFixture(JSON.parse(options.body).decision.command_id), 202)));
  await user.click(screen.getByRole("button", { name: "Enviar decisão" }));
  await screen.findByText(/Decisão recebida e pendente/);
  const id = JSON.parse(fetcher.mock.calls[1][1].body).decision.command_id;
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse(receiptFixture(id))));
  await user.click(screen.getByRole("button", { name: "Consultar recibo" }));
  await screen.findByText(/Execução confirmada pelo recibo/);
  fetcher.mockImplementationOnce(() => Promise.resolve(jsonResponse({ schema_version: "portal-decision-error.v1", code: "authority_unavailable" }, 503)));
  await user.click(screen.getByRole("button", { name: "Consultar recibo" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("autorização atual");
  expect(screen.queryByText(/Execução confirmada pelo recibo/)).not.toBeInTheDocument();
});
it("campos booleanos exigem resposta explícita e preservam falso", async () => {
  const { fetcher } = setup("ans_pendencia"); const user = userEvent.setup();
  const field = await screen.findByLabelText("Conjunto de dados completo (obrigatório)");
  expect(field).toHaveValue("");
  for (const name of ["Conjunto de dados completo", "Estrutura dos dados válida", "Dados anonimizados conforme LGPD"]) {
    await user.selectOptions(screen.getByLabelText(`${name} (obrigatório)`), "false");
  }
  await user.click(screen.getByRole("button", { name: "Revisar decisão" }));
  await user.click(screen.getByRole("checkbox", { name: /Revisei os dados/ }));
  fetcher.mockImplementationOnce((_url, options) => Promise.resolve(jsonResponse(admissionFixture(JSON.parse(options.body).decision.command_id), 202)));
  await user.click(screen.getByRole("button", { name: "Enviar decisão" }));
  await screen.findByText(/Decisão recebida e pendente/);
  expect(JSON.parse(fetcher.mock.calls[1][1].body).decision.inputs).toEqual({ kind: "ans_pendencia", dataset_complete: false, schema_valid: false, lgpd_anonimizado: false });
});
