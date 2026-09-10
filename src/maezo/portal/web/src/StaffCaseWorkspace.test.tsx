import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { StaffCaseWorkspace } from "./StaffCaseWorkspace";
import type { StaffCaseClient, StaffCaseResult } from "./staffCaseClient";

const caseRef = "case_staff_abcdefghijklmnop";

function detail() {
  return {
    schema: "portal-staff-case-detail.v1" as const,
    case: {
      case_ref: caseRef,
      kind: "authorization" as const,
      state: "active" as const,
      record_revision: "7",
      state_observed_at: "2099-09-10T12:00:00.000000Z",
    },
    identity: {
      upstream_resource_key: "guide-opaque",
      case_ref: caseRef,
      process_instance_ref: "instance-opaque",
      process_definition_id: "definition-opaque",
      process_definition_key: "SP-OP-AUTH-001",
      process_definition_version: "3",
      process_definition_digest: "a".repeat(64),
      kind: "authorization" as const,
    },
    active_tasks: [{
      task_id: "task-opaque-1",
      task_definition_key: "UT_AnaliseMedicoAuditor",
      task_revision: "8",
      created_at: "2099-09-10T11:00:00.000000Z",
      due_at: null,
      assignee_ref: null,
    }],
    next_task_cursor: null,
    tasks_complete: true,
    freshness: {
      observed_at: "2099-09-10T12:00:00.000000Z",
      source_observed_at: "2099-09-10T11:59:59.000000Z",
      valid_until: "2099-09-10T12:00:10.000000Z",
      refresh_after_seconds: 10 as const,
    },
  };
}

function service(readCase: StaffCaseClient["readCase"]): StaffCaseClient {
  return { readCase };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => vi.useRealTimers());

it("consulta o caso e apresenta somente a projeção staff autorizada", async () => {
  const readCase = vi.fn().mockResolvedValue({ kind: "success", value: detail() });
  render(<StaffCaseWorkspace service={service(readCase)} onSessionUnavailable={vi.fn()} />);
  await userEvent.type(screen.getByLabelText("Referência exata do caso"), caseRef);
  await userEvent.click(screen.getByRole("button", { name: "Consultar caso" }));
  expect(await screen.findByRole("heading", { name: "Caso de autorização" })).toHaveFocus();
  expect(screen.getByText("UT_AnaliseMedicoAuditor")).toBeInTheDocument();
  expect(screen.getByText("Sem prazo informado")).toBeInTheDocument();
  expect(screen.getByText(/página completa de tarefas/i)).toBeInTheDocument();
  expect(readCase).toHaveBeenCalledWith(caseRef, expect.any(AbortSignal));
  expect(window.location.href).not.toContain(caseRef);
  expect(screen.queryByText(/dossiê autorizado/i)).not.toBeInTheDocument();
});

it("aborta e descarta a resposta protegida quando o serviço da sessão muda", async () => {
  const old = deferred<StaffCaseResult>();
  const oldRead = vi.fn((_caseRef: string, _signal: AbortSignal) => old.promise);
  const nextRead = vi.fn().mockResolvedValue({ kind: "failure", failure: "resource_unavailable" });
  const view = render(
    <StaffCaseWorkspace service={service(oldRead)} onSessionUnavailable={vi.fn()} />,
  );
  await userEvent.type(screen.getByLabelText("Referência exata do caso"), caseRef);
  await userEvent.click(screen.getByRole("button", { name: "Consultar caso" }));
  const oldSignal = oldRead.mock.calls[0][1];
  view.rerender(<StaffCaseWorkspace service={service(nextRead)} onSessionUnavailable={vi.fn()} />);
  expect(oldSignal.aborted).toBe(true);
  expect(screen.getByLabelText("Referência exata do caso")).toHaveValue("");
  old.resolve({ kind: "success", value: detail() });
  await waitFor(() => expect(screen.queryByText("UT_AnaliseMedicoAuditor")).not.toBeInTheDocument());
});

it("invalida a sessão sem manter detalhes quando a leitura recebe 401", async () => {
  const onSessionUnavailable = vi.fn();
  const readCase = vi.fn().mockResolvedValue({
    kind: "failure",
    failure: "authentication_unavailable",
  });
  render(<StaffCaseWorkspace service={service(readCase)} onSessionUnavailable={onSessionUnavailable} />);
  await userEvent.type(screen.getByLabelText("Referência exata do caso"), caseRef);
  await userEvent.click(screen.getByRole("button", { name: "Consultar caso" }));
  await waitFor(() => expect(onSessionUnavailable).toHaveBeenCalledOnce());
  expect(screen.queryByText("UT_AnaliseMedicoAuditor")).not.toBeInTheDocument();
});

it("renova a referência selecionada na cadência autorizada sem usar uma edição posterior", async () => {
  vi.useFakeTimers();
  const readCase = vi.fn().mockResolvedValue({ kind: "success", value: detail() });
  render(<StaffCaseWorkspace service={service(readCase)} onSessionUnavailable={vi.fn()} />);
  const input = screen.getByLabelText("Referência exata do caso");
  fireEvent.change(input, { target: { value: caseRef } });
  fireEvent.submit(input.closest("form")!);
  await act(async () => Promise.resolve());
  expect(readCase).toHaveBeenCalledTimes(1);
  fireEvent.change(input, { target: { value: "case_edited_abcdefghijklmnop" } });
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(readCase).toHaveBeenNthCalledWith(2, caseRef, expect.any(AbortSignal));
});
