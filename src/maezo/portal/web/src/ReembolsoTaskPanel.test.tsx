import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { ReembolsoTaskPanel } from "./ReembolsoTaskPanel";

const REF = `rdoc_${"a".repeat(32)}`;

function contexto(overrides: Record<string, unknown> = {}) {
  return {
    schema: "portal-task-reembolso.v1",
    task_id: "task-1",
    task_definition_key: "UT_AnaliseReembolso",
    protocolo: "REE-AB12-CD34",
    relacao_com_titular: "titular",
    categoria_procedimento: "exame",
    tipo_reembolso: "fora_rede",
    motivo_nao_rede: "urgencia",
    data_atendimento: "2026-10-25",
    data_solicitacao: "2026-10-28",
    dias_desde_atendimento: 3,
    valor_solicitado_cents: "120050",
    decisao_reembolso: null,
    valor_reembolso_aprovado_cents: null,
    anexos: [{ tipo: "nota_fiscal", document_ref: REF }],
    situacao_prazo: "NAO_AVALIADO",
    resposta_beneficiario: "Foi de urgência.",
    observed_at: "2026-10-29T12:00:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function conclusao(resultado: string, formKey: string) {
  return {
    schema: "portal-task-completion.v1",
    task_id: "task-1",
    process_definition_key: "SP-OP-REEMBOLSO-001",
    process_definition_version: "2",
    task_definition_key: "UT_AnaliseReembolso",
    form_key: formKey,
    state: "completed",
    resultado,
    consumed_task_revision: "2",
    authority_revision: "1",
    evidence_revision: "1",
    evidence_digest: "e".repeat(64),
    completed_at: "2026-10-29T12:01:00Z",
    audit_intent_ref: "a".repeat(64),
    audit_result_ref: "b".repeat(64),
  };
}

afterEach(() => vi.unstubAllGlobals());

it("mostra os dados fechados do pedido, a resposta da beneficiaria e o link de cada documento", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(contexto())));
  render(
    <ReembolsoTaskPanel
      taskId="task-1"
      formKey="reembolso_decisao"
      csrfToken="csrf"
      sessionBinding="s"
      onSessionUnavailable={() => undefined}
    />,
  );
  expect(await screen.findByText("REE-AB12-CD34")).toBeTruthy();
  expect(screen.getByText("R$ 1.200,50")).toBeTruthy();
  expect(screen.getByText("Foi de urgência.")).toBeTruthy();
  const link = screen.getByRole("link", { name: "Nota fiscal 1" });
  expect(link.getAttribute("href")).toBe(`/api/v1/portal/tasks/task-1/reembolso/anexos/${REF}`);
});

it("negar exige os tres textos e envia o corpo fechado, sem quem decidiu", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) =>
    String(url).endsWith("/completion") ? json(conclusao("NEGAR", "reembolso_decisao")) : json(contexto()),
  );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(
    <ReembolsoTaskPanel
      taskId="task-1"
      formKey="reembolso_decisao"
      csrfToken="csrf"
      sessionBinding="s"
      onSessionUnavailable={() => undefined}
    />,
  );
  await screen.findByText("REE-AB12-CD34");
  await user.click(screen.getByLabelText("Negar"));
  const concluir = screen.getByRole("button", { name: "Concluir" });
  expect((concluir as HTMLButtonElement).disabled).toBe(true);
  await user.type(screen.getByLabelText("Justificativa (interna)"), "Fora do contrato");
  await user.type(screen.getByLabelText("Fundamentação contratual (interna)"), "Cláusula 5");
  await user.type(screen.getByLabelText("Texto que a beneficiária vai receber"), "Não está coberto.");
  await user.click(concluir);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Tarefa concluída"));
  const chamada = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/completion"))!;
  const corpo = JSON.parse(String(chamada[1]!.body));
  expect(corpo).toEqual({
    kind: "reembolso_decisao",
    decisao_reembolso: "NEGAR",
    justificativa: "Fora do contrato",
    fundamentacao_contratual: "Cláusula 5",
    texto_ao_beneficiario: "Não está coberto.",
  });
  expect(JSON.stringify(corpo)).not.toContain("analista_id");
});

it("o financeiro registra a data do pagamento", async () => {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
    String(url).endsWith("/completion")
      ? json(conclusao("PAGO", "reembolso_pagamento"))
      : json(contexto({ task_definition_key: "UT_RegistrarPagamento", decisao_reembolso: "APROVAR",
          valor_reembolso_aprovado_cents: "100000" })),
  );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(
    <ReembolsoTaskPanel
      taskId="task-1"
      formKey="reembolso_pagamento"
      csrfToken="csrf"
      sessionBinding="s"
      onSessionUnavailable={() => undefined}
    />,
  );
  expect(await screen.findByText("R$ 1.000,00")).toBeTruthy();
  await user.type(screen.getByLabelText("Pago em"), "2026-10-27");
  await user.click(screen.getByRole("button", { name: "Registrar pagamento" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Tarefa concluída"));
  const chamada = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/completion"))!;
  expect(JSON.parse(String(chamada[1]!.body))).toEqual({
    kind: "reembolso_pagamento",
    data_pagamento: "2026-10-27",
  });
});
