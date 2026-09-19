import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { StaffCaseWorkspace } from "./StaffCaseWorkspace";
import type {
  StaffCaseClient,
  StaffCasePageResult,
  StaffPage,
} from "./staffCaseClient";

const caseRef = "case_staff_abcdefghijklmnop";
const nextCaseRef = "case_staff_bcdefghijklmnopq";

function detail(ref = caseRef) {
  return {
    schema: "portal-staff-case-detail.v1" as const,
    case: {
      case_ref: ref,
      kind: "authorization" as const,
      state: "active" as const,
      record_revision: "7",
      state_observed_at: "2099-09-10T12:00:00.000000Z",
    },
    identity: {
      upstream_resource_key: "guide-opaque",
      case_ref: ref,
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
    outcome: null,
  };
}

function page(
  refs: readonly string[] = [caseRef],
  nextCursor: string | null = "cursor.staff-page-2",
): StaffPage {
  return {
    schema: "portal-staff-case-page.v1",
    items: refs.map((ref, index) => ({
      case_ref: ref,
      kind: "authorization",
      state: index === 0 ? "active" : "ended",
      record_revision: String(index + 7),
      state_observed_at: "2099-09-10T11:59:58.000000Z",
    })),
    next_cursor: nextCursor,
    freshness: {
      observed_at: "2099-09-10T12:00:00.000000Z",
      source_observed_at: "2099-09-10T11:59:59.000000Z",
      valid_until: "2099-09-10T12:00:10.000000Z",
      refresh_after_seconds: 10,
    },
  };
}

function service(
  readCase: StaffCaseClient["readCase"] = vi.fn().mockResolvedValue({
    kind: "success",
    value: detail(),
  }),
  listCases: StaffCaseClient["listCases"] = vi.fn().mockResolvedValue({
    kind: "success",
    value: page(),
  }),
): StaffCaseClient {
  return { listCases, readCase };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => vi.useRealTimers());

it("lista a página autorizada na entrada e abre o detalhe existente pela linha", async () => {
  const listCases = vi.fn().mockResolvedValue({ kind: "success", value: page() });
  const readCase = vi.fn().mockResolvedValue({ kind: "success", value: detail() });
  render(
    <StaffCaseWorkspace
      service={service(readCase, listCases)}
      onSessionUnavailable={vi.fn()}
    />,
  );

  expect(await screen.findByRole("heading", { name: "Página atual" })).toBeInTheDocument();
  expect(screen.getByText(/somente a página liberada/i)).toBeInTheDocument();
  expect(screen.queryByText(/total/i)).not.toBeInTheDocument();
  expect(listCases).toHaveBeenCalledWith(null, expect.any(AbortSignal));

  await userEvent.click(screen.getByRole("button", { name: new RegExp(caseRef) }));
  expect(await screen.findByRole("heading", { name: "Caso de autorização" })).toHaveFocus();
  expect(screen.getByText("UT_AnaliseMedicoAuditor")).toBeInTheDocument();
  expect(readCase).toHaveBeenCalledWith(caseRef, expect.any(AbortSignal));
  expect(window.location.href).not.toContain(caseRef);
});

it("acrescenta somente a próxima página ligada ao cursor opaco", async () => {
  const listCases = vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: page() })
    .mockResolvedValueOnce({ kind: "success", value: page([nextCaseRef], null) });
  render(<StaffCaseWorkspace service={service(undefined, listCases)} onSessionUnavailable={vi.fn()} />);

  await userEvent.click(await screen.findByRole("button", { name: "Carregar próxima página" }));
  expect(await screen.findByRole("button", { name: new RegExp(nextCaseRef) })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: new RegExp(caseRef) })).toBeInTheDocument();
  expect(listCases).toHaveBeenNthCalledWith(2, "cursor.staff-page-2", expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: "Carregar próxima página" })).not.toBeInTheDocument();
});

it("atualiza a primeira página e mantém a origem da validade visível", async () => {
  const listCases = vi.fn().mockResolvedValue({ kind: "success", value: page([], null) });
  render(<StaffCaseWorkspace service={service(undefined, listCases)} onSessionUnavailable={vi.fn()} />);

  expect(await screen.findByText(/lista válida até/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Atualizar casos" }));
  await waitFor(() => expect(listCases).toHaveBeenCalledTimes(2));
  expect(listCases).toHaveBeenLastCalledWith(null, expect.any(AbortSignal));
});

it("aborta paginação e recusa a resposta tardia quando um caso é selecionado", async () => {
  const later = deferred<StaffCasePageResult>();
  const listCases = vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: page() })
    .mockImplementationOnce((_cursor: string | null, _signal: AbortSignal) => later.promise);
  const readCase = vi.fn().mockResolvedValue({ kind: "success", value: detail() });
  render(<StaffCaseWorkspace service={service(readCase, listCases)} onSessionUnavailable={vi.fn()} />);

  await userEvent.click(await screen.findByRole("button", { name: "Carregar próxima página" }));
  const pageSignal = listCases.mock.calls[1][1];
  const input = screen.getByLabelText("Abrir pela referência exata");
  await userEvent.type(input, caseRef);
  await userEvent.click(screen.getByRole("button", { name: "Consultar caso" }));
  expect(pageSignal.aborted).toBe(true);
  expect(await screen.findByRole("heading", { name: "Caso de autorização" })).toBeInTheDocument();

  later.resolve({ kind: "success", value: page([nextCaseRef], null) });
  await act(async () => Promise.resolve());
  expect(screen.queryByText(nextCaseRef)).not.toBeInTheDocument();
});

it("aborta e descarta a lista protegida quando o serviço da sessão muda", async () => {
  const old = deferred<StaffCasePageResult>();
  const oldList = vi.fn((_cursor: string | null, _signal: AbortSignal) => old.promise);
  const nextList = vi.fn().mockResolvedValue({
    kind: "success",
    value: page([nextCaseRef], null),
  });
  const onSessionUnavailable = vi.fn();
  const view = render(
    <StaffCaseWorkspace
      service={service(undefined, oldList)}
      onSessionUnavailable={onSessionUnavailable}
    />,
  );
  const oldSignal = oldList.mock.calls[0][1];

  view.rerender(
    <StaffCaseWorkspace
      service={service(undefined, nextList)}
      onSessionUnavailable={onSessionUnavailable}
    />,
  );
  expect(oldSignal.aborted).toBe(true);
  expect(await screen.findByRole("button", { name: new RegExp(nextCaseRef) })).toBeInTheDocument();

  old.resolve({ kind: "success", value: page([caseRef], null) });
  await act(async () => Promise.resolve());
  expect(screen.queryByText(caseRef)).not.toBeInTheDocument();
});

it("mostra indisponibilidade da fonte sem inventar uma lista", async () => {
  const listCases = vi.fn().mockResolvedValue({
    kind: "failure",
    failure: "dependency_unavailable",
  });
  render(<StaffCaseWorkspace service={service(undefined, listCases)} onSessionUnavailable={vi.fn()} />);

  expect(await screen.findByRole("alert")).toHaveTextContent(/fonte autorizada de casos/i);
  expect(screen.queryByRole("list")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
});

it("invalida a sessão sem manter a lista quando a leitura recebe 401", async () => {
  const onSessionUnavailable = vi.fn();
  const listCases = vi.fn().mockResolvedValue({
    kind: "failure",
    failure: "authentication_unavailable",
  });
  render(
    <StaffCaseWorkspace
      service={service(undefined, listCases)}
      onSessionUnavailable={onSessionUnavailable}
    />,
  );

  await waitFor(() => expect(onSessionUnavailable).toHaveBeenCalledOnce());
  expect(screen.queryByRole("list")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Tentar novamente" })).not.toBeInTheDocument();
});

it("volta do detalhe por uma nova leitura da primeira página", async () => {
  const listCases = vi.fn().mockResolvedValue({ kind: "success", value: page() });
  render(<StaffCaseWorkspace service={service(undefined, listCases)} onSessionUnavailable={vi.fn()} />);

  await userEvent.click(await screen.findByRole("button", { name: new RegExp(caseRef) }));
  await userEvent.click(await screen.findByRole("button", { name: "Voltar para casos" }));
  expect(await screen.findByRole("heading", { name: "Página atual" })).toBeInTheDocument();
  expect(listCases).toHaveBeenNthCalledWith(2, null, expect.any(AbortSignal));
});

it("renova o detalhe selecionado na cadência autorizada", async () => {
  vi.useFakeTimers();
  const readCase = vi.fn().mockResolvedValue({ kind: "success", value: detail() });
  const listCases = vi.fn().mockResolvedValue({ kind: "success", value: page() });
  render(<StaffCaseWorkspace service={service(readCase, listCases)} onSessionUnavailable={vi.fn()} />);
  await act(async () => Promise.resolve());

  fireEvent.click(screen.getByRole("button", { name: new RegExp(caseRef) }));
  await act(async () => Promise.resolve());
  expect(readCase).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(readCase).toHaveBeenNthCalledWith(2, caseRef, expect.any(AbortSignal));
});


it("retira a página ao expirar mesmo com paginação pendente e recusa o append tardio", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2099-09-10T12:00:00Z"));
  const next = deferred<StaffCasePageResult>();
  const refresh = deferred<StaffCasePageResult>();
  const listCases = vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: page() })
    .mockReturnValueOnce(next.promise)
    .mockReturnValueOnce(refresh.promise);
  render(<StaffCaseWorkspace service={service(undefined, listCases)} onSessionUnavailable={vi.fn()} />);
  await act(async () => Promise.resolve());
  await act(async () => vi.advanceTimersByTimeAsync(9_000));
  fireEvent.click(screen.getByRole("button", { name: "Carregar próxima página" }));
  const pageSignal = listCases.mock.calls[1][1] as AbortSignal;
  expect(screen.getByRole("button", { name: new RegExp(caseRef) })).toBeInTheDocument();
  await act(async () => vi.advanceTimersByTimeAsync(1_001));
  expect(pageSignal.aborted).toBe(true);
  expect(listCases).toHaveBeenNthCalledWith(3, null, expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: new RegExp(caseRef) })).not.toBeInTheDocument();
  const laterPage = page([nextCaseRef], null);
  laterPage.freshness.valid_until = "2099-09-10T12:00:30.000000Z";
  await act(async () => {
    next.resolve({ kind: "success", value: laterPage });
    await next.promise;
  });
  expect(screen.queryByText(nextCaseRef)).not.toBeInTheDocument();
  expect(screen.getByText("Consultando seus casos autorizados.")).toBeInTheDocument();
});

it("retém o prazo anterior ao acrescentar uma página com validade posterior", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2099-09-10T12:00:00Z"));
  const refresh = deferred<StaffCasePageResult>();
  const laterPage = page([nextCaseRef], null);
  laterPage.freshness.valid_until = "2099-09-10T12:00:30.000000Z";
  const listCases = vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: page() })
    .mockResolvedValueOnce({ kind: "success", value: laterPage })
    .mockReturnValueOnce(refresh.promise);
  render(<StaffCaseWorkspace service={service(undefined, listCases)} onSessionUnavailable={vi.fn()} />);
  await act(async () => Promise.resolve());
  await act(async () => vi.advanceTimersByTimeAsync(4_000));
  fireEvent.click(screen.getByRole("button", { name: "Carregar próxima página" }));
  await act(async () => Promise.resolve());
  expect(screen.getByRole("button", { name: new RegExp(caseRef) })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: new RegExp(nextCaseRef) })).toBeInTheDocument();
  await act(async () => vi.advanceTimersByTimeAsync(6_001));
  expect(listCases).toHaveBeenNthCalledWith(3, null, expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: new RegExp(caseRef) })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: new RegExp(nextCaseRef) })).not.toBeInTheDocument();
});
