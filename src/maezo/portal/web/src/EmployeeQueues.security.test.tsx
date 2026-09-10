import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EmployeeQueues } from "./EmployeeQueues";
import { taskReadInputs } from "./taskReadBindings";

function freshness(until = "59") {
  return { state: "current", observed_at: "2026-09-09T15:00:01Z", source_observed_at: "2026-09-09T15:00:00Z",
    valid_until: `2026-09-09T15:00:${until}Z`, refresh_after_seconds: 10 };
}
function page(id = "task-1", until = "59", next_cursor: string | null = null, revision = "1") {
  return { schema: "portal-task-queue.v1", queue: "mine", next_cursor, freshness: freshness(until), items: [{
    task_id: id, process_definition_key: "SP-OP-PAGTO-001", task_definition_key: "UT_AnaliseAdmissibilidade",
    task_revision: revision, ownership: "self", engine_due_at: null, snapshot_at: "2026-09-09T15:00:00Z" }] };
}
function detail(until = "59", revision = "1") {
  return { schema: "portal-task-read.v1", freshness: freshness(until), task: {
    schema_version: 1, snapshot_at: "2026-09-09T15:00:00Z", task_id: "task-1",
    process_definition_key: "SP-OP-PAGTO-001", process_definition_version: "7", process_definition_id: "definition-1",
    process_definition_digest: "a".repeat(64), task_definition_key: "UT_AnaliseAdmissibilidade", form_key: "pagto_admissibilidade",
    form_version: "1", form_digest: "b".repeat(64), form_source_status: "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    task_revision: revision, assignee_ref: null, eligible_candidate_groups: [], evidence_revision: "1", evidence_digest: "c".repeat(64),
    engine_due_at: null, allowed_actions: [], allowed_inputs: [...taskReadInputs.pagto_admissibilidade],
    read_only_evidence: { kind: "pagto_admissibilidade", valor_pagamento_cents: "999999999999999999999999999999999999999",
      dados_pagamento_validos: true, lastro_confirmado: false, duplicidade_suspeita: false } } };
}
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function pending() { let resolve!: (response: Response) => void; const promise = new Promise<Response>((done) => { resolve = done; }); return { promise, resolve }; }
async function settle() { await act(async () => { for (let n = 0; n < 8; n += 1) await Promise.resolve(); }); }
const rows = () => screen.queryAllByRole("rowheader");
const shownDetail = () => screen.queryByRole("heading", { name: "UT_AnaliseAdmissibilidade" });
function open() { fireEvent.click(screen.getAllByRole("button", { name: "Abrir detalhes de UT_AnaliseAdmissibilidade" })[0]); }
const unavailable = vi.fn();
async function start() { render(<EmployeeQueues sessionBinding="original" onSessionUnavailable={unavailable} />); await settle(); }
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-09-09T15:00:02Z")); vi.stubGlobal("fetch", vi.fn()); unavailable.mockReset(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("detail403 fences a concurrently pending queue, stops old polling, and permits an explicit fresh retry", async () => {
  const task = pending(), refresh = pending();
  vi.mocked(fetch).mockResolvedValueOnce(response(page())).mockReturnValueOnce(task.promise).mockReturnValueOnce(refresh.promise);
  await start(); open(); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Atualizar agora" })); await settle();
  const queueSignal = vi.mocked(fetch).mock.calls[2][1]?.signal;
  task.resolve(response({ schema: "portal-read-error.v1", code: "employee_access_required" }, 403)); await settle();
  expect(queueSignal?.aborted).toBe(true);
  refresh.resolve(response(page())); await settle();
  await act(async () => vi.advanceTimersByTimeAsync(20_000));
  expect(rows()).toHaveLength(0); expect(shownDetail()).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalledTimes(3);
  vi.mocked(fetch).mockResolvedValueOnce(response(page()));
  fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" })); await settle();
  expect(rows()).toHaveLength(1); expect(unavailable).not.toHaveBeenCalled();
});

it("the retained queue ceiling aborts both pending lanes and cannot be renewed by their late responses", async () => {
  const task = pending(), refresh = pending();
  vi.mocked(fetch).mockResolvedValueOnce(response(page("task-1", "03"))).mockReturnValueOnce(task.promise).mockReturnValueOnce(refresh.promise);
  await start(); open(); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Atualizar agora" })); await settle();
  const signals = vi.mocked(fetch).mock.calls.slice(1).map((call) => call[1]?.signal);
  await act(async () => vi.advanceTimersByTimeAsync(1_001));
  expect(signals.every((signal) => signal?.aborted)).toBe(true);
  refresh.resolve(response(page())); task.resolve(response(detail())); await settle();
  expect(rows()).toHaveLength(0); expect(shownDetail()).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("A fila mudou");
});

it("an already-expired detail response never becomes visible despite a current queue", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(page())).mockResolvedValueOnce(response(detail("01.999999")));
  await start(); open(); await settle();
  expect(shownDetail()).not.toBeInTheDocument(); expect(rows()).toHaveLength(1);
  expect(screen.getByRole("alert")).toHaveTextContent("A fila mudou");
});

it("a new selected-task revision fences pending old detail and can be explicitly opened again", async () => {
  const task = pending();
  vi.mocked(fetch).mockResolvedValueOnce(response(page())).mockReturnValueOnce(task.promise).mockResolvedValueOnce(response(page("task-1", "59", null, "2")));
  await start(); open(); await settle();
  const signal = vi.mocked(fetch).mock.calls[1][1]?.signal;
  fireEvent.click(screen.getByRole("button", { name: "Atualizar agora" })); await settle();
  expect(signal?.aborted).toBe(true);
  task.resolve(response(detail())); await settle(); expect(shownDetail()).not.toBeInTheDocument();
  vi.mocked(fetch).mockResolvedValueOnce(response(detail("59", "2"))); open(); await settle();
  expect(shownDetail()).toBeInTheDocument(); expect(screen.getAllByText("2")).toHaveLength(2);
});

it("an earlier second-page microsecond ceiling expires every retained row and pending detail", async () => {
  const task = pending();
  vi.mocked(fetch).mockResolvedValueOnce(response(page("task-1", "59", "next"))).mockResolvedValueOnce(response(page("task-2", "02.500001"))).mockReturnValueOnce(task.promise);
  await start(); fireEvent.click(screen.getByRole("button", { name: "Carregar mais" })); await settle();
  expect(rows()).toHaveLength(2); open(); await settle();
  const signal = vi.mocked(fetch).mock.calls[2][1]?.signal;
  await act(async () => vi.advanceTimersByTimeAsync(500));
  expect(rows()).toHaveLength(0); expect(signal?.aborted).toBe(true);
  task.resolve(response(detail())); await settle(); expect(shownDetail()).not.toBeInTheDocument();
});
