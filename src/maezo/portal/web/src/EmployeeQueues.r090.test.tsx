// R-090 (F-098, F-099, F-100): rotulos legiveis, nome acessivel com o texto
// visivel e atualizacao automatica que o usuario pode pausar (WCAG 2.2.2).
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EmployeeQueues } from "./EmployeeQueues";
import { formatCents, processLabel, taskLabel } from "./queueLabels";
import { taskLabels } from "./taskLabels";

function queuePage(items: Array<{ id: string; process: string; task: string }>, second = "01") {
  return {
    schema: "portal-task-queue.v1",
    queue: "mine",
    items: items.map(({ id, process, task }) => ({
      task_id: id,
      process_definition_key: process,
      task_definition_key: task,
      task_revision: "4815162342",
      ownership: "self",
      engine_due_at: null,
      snapshot_at: "2026-09-09T15:00:00Z",
    })),
    next_cursor: null,
    freshness: {
      state: "current",
      observed_at: `2026-09-09T15:00:${second}Z`,
      source_observed_at: "2026-09-09T15:00:00Z",
      valid_until: "2026-09-09T15:00:59Z",
      refresh_after_seconds: 10,
    },
  };
}

const known = { id: "task-1", process: "SP-OP-AUTH-001", task: "UT_AnaliseMedicoAuditor" };

function ok(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

async function settle() {
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-09T15:00:02Z"));
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------- F-098 (2.5.3)

it("F-098: o nome acessivel de cada 'Ver detalhes' comeca pelo texto visivel", async () => {
  vi.mocked(fetch).mockResolvedValue(ok(queuePage([known, { id: "task-2", process: "SP-OP-X", task: "UT_Desconhecida" }])));
  render(<EmployeeQueues sessionBinding="s" onSessionUnavailable={vi.fn()} />);
  await settle();
  const buttons = screen.getAllByRole("button", { name: /detalhes/i });
  expect(buttons).toHaveLength(2);
  for (const button of buttons) {
    const visible = (button.textContent ?? "").trim();
    expect(visible).toBe("Ver detalhes");
    expect(button.getAttribute("aria-label")?.startsWith(visible)).toBe(true);
  }
  expect(screen.getByRole("button", { name: "Ver detalhes: Analise do medico auditor" })).toBeInTheDocument();
});

// ---------------------------------------------------------------- F-099

it("F-099: a fila mostra o nome BPMN da tarefa e do processo, nao as chaves", async () => {
  vi.mocked(fetch).mockResolvedValue(ok(queuePage([known])));
  render(<EmployeeQueues sessionBinding="s" onSessionUnavailable={vi.fn()} />);
  await settle();
  const table = screen.getByRole("table");
  expect(within(table).getByRole("rowheader", { name: "Analise do medico auditor" })).toBeInTheDocument();
  expect(within(table).getByRole("cell", { name: "Autorizacao Previa" })).toBeInTheDocument();
  expect(table).not.toHaveTextContent("UT_AnaliseMedicoAuditor");
  expect(table).not.toHaveTextContent("SP-OP-AUTH-001");
  expect(table).not.toHaveTextContent("4815162342");
  expect(within(table).queryByRole("columnheader", { name: "Revisão" })).not.toBeInTheDocument();
});

it("F-099: chave fora do catalogo gerado aparece crua, nunca escondida nem inventada", async () => {
  vi.mocked(fetch).mockResolvedValue(ok(queuePage([{ id: "task-9", process: "SP-OP-NOVO-001", task: "UT_Nova" }])));
  render(<EmployeeQueues sessionBinding="s" onSessionUnavailable={vi.fn()} />);
  await settle();
  expect(screen.getByRole("rowheader", { name: "UT_Nova" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "SP-OP-NOVO-001" })).toBeInTheDocument();
});

it("F-099: o rotulo depende do par processo/tarefa (mesmo id em dois processos)", () => {
  expect(taskLabel("SP-OP-AUTH-001", "UT_DecidirPendenciaExpirada")).toBe("Decidir destino da guia com pendencia expirada");
  expect(taskLabel("SP-OP-REEMBOLSO-001", "UT_DecidirPendenciaExpirada")).toBe(
    "Decidir destino da solicitacao com pendencia expirada",
  );
  expect(taskLabel("SP-OP-PAGTO-001", "UT_AnaliseMedicoAuditor")).toBe("UT_AnaliseMedicoAuditor");
  expect(processLabel("SP-OP-ESCALATION-001")).toBe("Escalonamento Humano Universal");
});

it("F-099: centavos viram reais por aritmetica de string, sem perder digito", () => {
  expect(formatCents("0")).toBe("R$ 0,00");
  expect(formatCents("5")).toBe("R$ 0,05");
  expect(formatCents("123456")).toBe("R$ 1.234,56");
  expect(formatCents("-100")).toBe("-R$ 1,00");
  expect(formatCents("900719925474099312345678901234567890")).toBe("R$ 9.007.199.254.740.993.123.456.789.012.345.678,90");
});

it("guarda PHI: rotulos sao nomes estaticos de desenho, sem numeros longos nem contato", () => {
  expect(taskLabels.length).toBeGreaterThan(0);
  for (const row of taskLabels) {
    for (const label of [row.taskLabel, row.processLabel]) {
      expect(label).not.toMatch(/\d{5,}/);
      expect(label).not.toMatch(/@|\$\{|#\{/);
      expect(label).not.toMatch(/\d{3}\.\d{3}\.\d{3}-\d{2}/);
    }
  }
});

// ---------------------------------------------------------------- F-100 (2.2.2)

it("F-100: pausar a atualizacao automatica interrompe o ciclo de 10 s e retomar o religa", async () => {
  vi.mocked(fetch).mockImplementation(async () => ok(queuePage([known])));
  render(<EmployeeQueues sessionBinding="s" onSessionUnavailable={vi.fn()} />);
  await settle();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByText("A fila é atualizada automaticamente a cada 10 segundos.")).toBeInTheDocument();

  const pause = screen.getByRole("button", { name: "Pausar atualização automática" });
  expect(pause).toHaveAttribute("aria-pressed", "false");
  await act(async () => { pause.click(); });
  expect(pause).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText(/Atualização automática pausada/)).toBeInTheDocument();

  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(fetch).toHaveBeenCalledTimes(1);

  // A atualizacao manual continua disponivel com a pausa ligada.
  await act(async () => { screen.getByRole("button", { name: "Atualizar agora" }).click(); });
  await settle();
  expect(fetch).toHaveBeenCalledTimes(2);

  await act(async () => { screen.getByRole("button", { name: "Pausar atualização automática" }).click(); });
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("F-100: pausar nao estende a validade informada; a fila vencida sai da tela", async () => {
  vi.mocked(fetch).mockImplementation(async () => ok(queuePage([known])));
  render(<EmployeeQueues sessionBinding="s" onSessionUnavailable={vi.fn()} />);
  await settle();
  await act(async () => { screen.getByRole("button", { name: "Pausar atualização automática" }).click(); });
  await act(async () => vi.advanceTimersByTimeAsync(58_000));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("A fila mudou");
});
