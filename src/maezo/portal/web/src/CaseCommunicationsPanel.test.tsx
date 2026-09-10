import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { CaseCommunicationsPanel } from "./CaseCommunicationsPanel";
import type {
  CaseCommunicationsClient,
  CommunicationPage,
  HistoryPage,
} from "./caseCommunicationsClient";

const ref = (name: string) => `${name}_abcdefghijklmnop`;
const observed = "2099-09-10T12:00:00.000000Z";
const future = "2099-09-10T13:00:00.000000Z";

function communicationPage(overrides: Partial<CommunicationPage> = {}): CommunicationPage {
  return {
    schema_version: "portal-communications.v1",
    case_ref: ref("case"),
    items: [{
      communication_ref: ref("communication"), sender_kind: "provider", authored_at: observed,
      inbox_available_at: observed, delivery_state: "inbox_available", body_ref: ref("body"),
    }],
    next_cursor: null, observed_at: observed, valid_until: future, ...overrides,
  };
}

function historyPage(overrides: Partial<HistoryPage> = {}): HistoryPage {
  return {
    schema_version: "portal-history.v1", history_scope: "portal_events", case_ref: ref("case"),
    items: [
      { event_ref: ref("event_1"), sequence: "1", occurred_at: observed, kind: "communication_available", communication_ref: ref("communication"), command_ref: null, receipt_ref: null },
      { event_ref: ref("event_2"), sequence: "2", occurred_at: observed, kind: "command_receipt_indexed", communication_ref: null, command_ref: ref("command"), receipt_ref: ref("receipt") },
    ],
    next_cursor: null, observed_at: observed, valid_until: future, ...overrides,
  };
}

function service(overrides: Partial<CaseCommunicationsClient> = {}): CaseCommunicationsClient {
  return {
    listCommunications: vi.fn().mockResolvedValue({ kind: "success", value: communicationPage() }),
    listHistory: vi.fn().mockResolvedValue({ kind: "success", value: historyPage() }),
    publishCommunication: vi.fn(),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

it("apresenta metadados neutros da caixa e o histórico limitado", async () => {
  render(<CaseCommunicationsPanel caseRef={ref("case")} client={service()} onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByText(`Referência`)).toBeInTheDocument();
  expect(screen.getByText("Origem registrada: Prestador")).toBeInTheDocument();
  expect(screen.getByText(/Disponível nesta caixa em/)).toBeInTheDocument();
  expect(screen.getByText("Comunicação disponibilizada")).toBeInTheDocument();
  expect(screen.getByText("Recibo de comando indexado")).toBeInTheDocument();
  expect(screen.getByText(/não representa o histórico completo/i)).toBeInTheDocument();
  expect(screen.getAllByText(/serviço PHI separado/i)).toHaveLength(2);
  expect(screen.queryByText(/^(enviada por você|recebida de|conteúdo da mensagem)/i)).not.toBeInTheDocument();
  expect(screen.queryByText(ref("body"))).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("não chama publicação sem uma composição PHI", async () => {
  const api = service();
  render(<CaseCommunicationsPanel caseRef={ref("case")} client={api} onSessionUnavailable={vi.fn()} />);
  await screen.findByText("Comunicação disponibilizada");
  expect(api.publishCommunication).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /enviar mensagem/i })).not.toBeInTheDocument();
});

it("pagina a caixa e mantém o menor prazo de validade", async () => {
  const earlier = "2099-09-10T12:30:00.000000Z";
  const api = service({
    listCommunications: vi.fn()
      .mockResolvedValueOnce({ kind: "success", value: communicationPage({ next_cursor: ref("cursor"), valid_until: earlier }) })
      .mockResolvedValueOnce({ kind: "success", value: communicationPage({
        items: [{ communication_ref: ref("communication_2"), sender_kind: "staff", authored_at: observed, inbox_available_at: observed, delivery_state: "inbox_available", body_ref: null }],
        valid_until: future,
      }) }),
  });
  render(<CaseCommunicationsPanel caseRef={ref("case")} client={api} onSessionUnavailable={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Carregar mais comunicações" }));
  expect(await screen.findByText("Origem registrada: Colaborador")).toBeInTheDocument();
  expect(api.listCommunications).toHaveBeenLastCalledWith(ref("case"), expect.any(AbortSignal), ref("cursor"));
});

it("recusa repetição entre páginas em vez de renderizar registro duplicado", async () => {
  const api = service({
    listCommunications: vi.fn()
      .mockResolvedValueOnce({ kind: "success", value: communicationPage({ next_cursor: ref("cursor") }) })
      .mockResolvedValueOnce({ kind: "success", value: communicationPage() }),
  });
  render(<CaseCommunicationsPanel caseRef={ref("case")} client={api} onSessionUnavailable={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Carregar mais comunicações" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("resposta inesperada");
  const communicationsSection = screen.getByRole("heading", { name: "Comunicações disponíveis" })
    .closest("section");
  expect(communicationsSection).not.toBeNull();
  expect(within(communicationsSection!).queryByText(ref("communication"))).not.toBeInTheDocument();
});

it("remove ambas as projeções quando a sessão deixa de existir", async () => {
  const callback = vi.fn();
  const api = service({
    listCommunications: vi.fn().mockResolvedValue({ kind: "failure", failure: "authentication_unavailable" }),
  });
  render(<CaseCommunicationsPanel caseRef={ref("case")} client={api} onSessionUnavailable={callback} />);
  expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("sessão não está mais disponível");
  expect(screen.queryByText("Recibo de comando indexado")).not.toBeInTheDocument();
  expect(callback).toHaveBeenCalledOnce();
});

it("aborta e limpa conteúdo antigo ao trocar caso e cliente", async () => {
  const pending = deferred<Awaited<ReturnType<CaseCommunicationsClient["listCommunications"]>>>();
  const old = service({ listCommunications: vi.fn().mockReturnValue(pending.promise) });
  const fresh = service({
    listCommunications: vi.fn().mockResolvedValue({ kind: "success", value: communicationPage({ case_ref: ref("new_case"), items: [] }) }),
    listHistory: vi.fn().mockResolvedValue({ kind: "success", value: historyPage({ case_ref: ref("new_case"), items: [] }) }),
  });
  const view = render(<CaseCommunicationsPanel caseRef={ref("case")} client={old} onSessionUnavailable={vi.fn()} />);
  await screen.findByText("Recibo de comando indexado");
  const oldSignal = vi.mocked(old.listCommunications).mock.calls[0][1];
  view.rerender(<CaseCommunicationsPanel caseRef={ref("new_case")} client={fresh} onSessionUnavailable={vi.fn()} />);
  expect(oldSignal.aborted).toBe(true);
  expect(screen.queryByText(ref("receipt"))).not.toBeInTheDocument();
  expect(await screen.findByText("Nenhuma comunicação está disponível nesta caixa.")).toBeInTheDocument();
  await act(async () => pending.resolve({ kind: "success", value: communicationPage() }));
  expect(screen.queryByText(ref("communication"))).not.toBeInTheDocument();
});

it("mantém as seções nomeadas e ações acessíveis por teclado", async () => {
  const api = service({
    listHistory: vi.fn()
      .mockResolvedValueOnce({ kind: "failure", failure: "dependency_unavailable" })
      .mockResolvedValueOnce({ kind: "success", value: historyPage({ items: [] }) }),
  });
  render(<CaseCommunicationsPanel caseRef={ref("case")} client={api} onSessionUnavailable={vi.fn()} />);
  const region = screen.getByRole("region", { name: "Comunicações e eventos do portal" });
  expect(within(region).getByRole("heading", { name: "Comunicações disponíveis" })).toBeInTheDocument();
  const update = await screen.findByRole("button", { name: "Atualizar" });
  update.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(api.listHistory).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("Nenhum evento do portal está disponível para este caso.")).toBeInTheDocument();
});
