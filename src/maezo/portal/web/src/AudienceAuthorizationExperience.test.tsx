import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, expectTypeOf, it, vi } from "vitest";

import { AudienceAuthorizationExperience } from "./AudienceAuthorizationExperience";
import type {
  CaseCommunicationsClient,
  CommunicationPage,
  HistoryPage,
} from "./caseCommunicationsClient";
import type { CaseSummaryView, ProviderAuthorizationService } from "./caseExperienceModels";
import {
  admittedIntake,
  casePageFixture,
  intakeFormFixture,
  workspaceFixture,
} from "./test/caseExperienceFixtures";

function service(overrides: Partial<ProviderAuthorizationService> = {}): ProviderAuthorizationService {
  return {
    listCases: vi.fn().mockResolvedValue({ kind: "success", value: casePageFixture }),
    readCase: vi.fn().mockResolvedValue({ kind: "success", value: workspaceFixture }),
    downloadDocument: vi.fn().mockResolvedValue({ kind: "success", value: new Blob(["synthetic"]) }),
    readAuthorizationIntakeForm: vi.fn().mockResolvedValue({ kind: "success", value: intakeFormFixture }),
    submitAuthorization: vi.fn().mockResolvedValue({ kind: "success", progress: admittedIntake }),
    ...overrides,
  };
}

const observed = "2099-09-10T12:00:00.000000Z";
const future = "2099-09-10T13:00:00.000000Z";

function communicationPage(overrides: Partial<CommunicationPage> = {}): CommunicationPage {
  return {
    schema_version: "portal-communications.v1",
    case_ref: "case_synthetic_auth_1",
    items: [{
      communication_ref: "communication_synthetic_1",
      sender_kind: "system",
      authored_at: observed,
      inbox_available_at: observed,
      delivery_state: "inbox_available",
      body_ref: null,
    }],
    next_cursor: null,
    observed_at: observed,
    valid_until: future,
    ...overrides,
  };
}

function historyPage(overrides: Partial<HistoryPage> = {}): HistoryPage {
  return {
    schema_version: "portal-history.v1",
    history_scope: "portal_events",
    case_ref: "case_synthetic_auth_1",
    items: [{
      event_ref: "event_synthetic_1",
      sequence: "1",
      occurred_at: observed,
      kind: "communication_available",
      communication_ref: "communication_synthetic_1",
      command_ref: null,
      receipt_ref: null,
    }],
    next_cursor: null,
    observed_at: observed,
    valid_until: future,
    ...overrides,
  };
}

function communications(
  overrides: Partial<CaseCommunicationsClient> = {},
): CaseCommunicationsClient {
  return {
    listCommunications: vi.fn().mockResolvedValue({ kind: "success", value: communicationPage() }),
    listHistory: vi.fn().mockResolvedValue({ kind: "success", value: historyPage() }),
    publishCommunication: vi.fn(),
    ...overrides,
  };
}

it("leva beneficiário de solicitações a documentos, mensagens e recibos do caso autorizado", async () => {
  const api = service();
  const inbox = communications();
  render(
    <AudienceAuthorizationExperience
      audience="beneficiary"
      service={api}
      communicationsClient={inbox}
      onSessionUnavailable={vi.fn()}
    />,
  );
  expect(await screen.findByRole("heading", { name: "Referência case_synthetic_auth_1" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Enviar nova solicitação" })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("tab", { name: "Documentos" }));
  await userEvent.click(screen.getByRole("button", { name: "Abrir solicitação" }));
  expect(await screen.findByRole("heading", { name: "Pedidos de documentos" })).toBeInTheDocument();
  expect(screen.getByText("Envie o laudo solicitado")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("tab", { name: "Mensagens" }));
  expect(await screen.findByText("Origem registrada: Sistema")).toBeInTheDocument();
  expect(inbox.listCommunications).toHaveBeenCalledWith(
    "case_synthetic_auth_1",
    expect.any(AbortSignal),
  );
  expect(inbox.listHistory).toHaveBeenCalledWith(
    "case_synthetic_auth_1",
    expect.any(AbortSignal),
  );

  await userEvent.click(screen.getByRole("tab", { name: "Recibos" }));
  const accepted = screen.getByText("Envio da solicitação").closest("li");
  expect(accepted).not.toBeNull();
  expect(within(accepted!).getByText(/execução ainda não foi confirmada/i)).toBeInTheDocument();
});

it("envia intake AUTH do prestador com referências selecionadas e centavos preservados como string", async () => {
  const api = service();
  const hugeCents = "900719925474099312345678901234567890";
  render(
    <AudienceAuthorizationExperience
      audience="provider"
      service={api}
      communicationsClient={communications()}
      onSessionUnavailable={vi.fn()}
    />,
  );
  const form = await screen.findByRole("heading", { name: "Enviar nova solicitação" });
  const region = form.closest("section");
  expect(region).not.toBeNull();
  const scoped = within(region!);
  await userEvent.selectOptions(scoped.getByLabelText("Beneficiário"), "beneficiary_synthetic_1");
  await userEvent.selectOptions(scoped.getByLabelText("Prestador solicitante"), "provider_synthetic_1");
  const guide = scoped.getByLabelText("Guia TISS protegida");
  expect(guide).toHaveRole("combobox");
  await userEvent.selectOptions(guide, "guide_ref_synthetic_1");
  await userEvent.type(scoped.getByLabelText("Código do procedimento TUSS"), "10101012");
  await userEvent.selectOptions(scoped.getByLabelText("Categoria do procedimento"), "consulta");
  await userEvent.selectOptions(scoped.getByLabelText("Caráter do atendimento"), "eletivo");
  await userEvent.type(scoped.getByLabelText(/^Valor estimado em centavos/), hugeCents);
  await userEvent.click(scoped.getByRole("checkbox", { name: "Pedido médico verificado" }));
  await userEvent.click(scoped.getByRole("button", { name: "Enviar solicitação" }));

  await waitFor(() => expect(api.submitAuthorization).toHaveBeenCalledOnce());
  const [draft] = vi.mocked(api.submitAuthorization).mock.calls[0];
  expect(draft).toEqual({
    beneficiaryRef: "beneficiary_synthetic_1",
    providerRef: "provider_synthetic_1",
    guideRef: "guide_ref_synthetic_1",
    procedureCode: "10101012",
    procedureCategory: "consulta",
    careCharacter: "eletivo",
    estimatedValueCents: hugeCents,
    protectedDocumentRefs: ["document_synthetic_finalized_1"],
  });
  expect(JSON.stringify(draft)).not.toMatch(/tenant|actor|principal|approved/i);
  expect(await scoped.findByText(/caso ainda não foi confirmado como iniciado/i)).toBeInTheDocument();
  expect(scoped.queryByText(/autorização concedida/i)).toBeInTheDocument();
});

it("remove a experiência quando a sessão deixa de existir", async () => {
  const onSessionUnavailable = vi.fn();
  const api = service({
    listCases: vi.fn().mockResolvedValue({ kind: "failure", failure: "session-unavailable" }),
  });
  render(
    <AudienceAuthorizationExperience
      audience="beneficiary"
      service={api}
      communicationsClient={communications()}
      onSessionUnavailable={onSessionUnavailable}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("sessão não está mais disponível");
  expect(onSessionUnavailable).toHaveBeenCalledOnce();
});

it("não grava referências ou conteúdo da jornada em storage persistente", async () => {
  const local = vi.spyOn(Storage.prototype, "setItem");
  const remove = vi.spyOn(Storage.prototype, "removeItem");
  render(
    <AudienceAuthorizationExperience
      audience="beneficiary"
      service={service()}
      communicationsClient={communications()}
      onSessionUnavailable={vi.fn()}
    />,
  );
  expect(await screen.findByRole("heading", { name: "Referência case_synthetic_auth_1" })).toBeInTheDocument();
  expect(local).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
});

it("transforma falha inesperada da dependência em erro recuperável sem expor detalhes", async () => {
  const api = service({ listCases: vi.fn().mockRejectedValue(new Error("synthetic private detail")) });
  render(
    <AudienceAuthorizationExperience
      audience="beneficiary"
      service={api}
      communicationsClient={communications()}
      onSessionUnavailable={vi.fn()}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Uma dependência não respondeu");
  expect(screen.queryByText(/synthetic private detail/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Atualizar solicitações" })).toBeInTheDocument();
});


function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

async function openDocuments() {
  await screen.findByRole("heading", { name: "Referência case_synthetic_auth_1" });
  await userEvent.click(screen.getByRole("tab", { name: "Documentos" }));
  await userEvent.click(screen.getByRole("button", { name: "Abrir solicitação" }));
  await screen.findByRole("button", { name: "Abrir documento" });
}

async function submitIntake() {
  await screen.findByRole("heading", { name: "Enviar nova solicitação" });
  for (const [label, value] of [
    ["Beneficiário", "beneficiary_synthetic_1"],
    ["Prestador solicitante", "provider_synthetic_1"],
    ["Guia TISS protegida", "guide_ref_synthetic_1"],
    ["Categoria do procedimento", "consulta"],
    ["Caráter do atendimento", "eletivo"],
  ]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.change(screen.getByLabelText("Código do procedimento TUSS"), { target: { value: "10101012" } });
  fireEvent.change(screen.getByLabelText(/^Valor estimado em centavos/), { target: { value: "12500" } });
  await userEvent.click(screen.getByRole("button", { name: "Enviar solicitação" }));
}

it("apresenta o Blob autorizado como download e libera a URL ao trocar caso e desmontar", async () => {
  const create = vi.fn().mockReturnValueOnce("blob:synthetic-1").mockReturnValueOnce("blob:synthetic-2");
  const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
  try {
    const api = service();
    const view = render(<AudienceAuthorizationExperience audience="beneficiary" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
    await openDocuments();
    await userEvent.click(screen.getByRole("button", { name: "Abrir documento" }));
    const link = await screen.findByRole("link", { name: "Baixar documento preparado" });
    expect(link).toHaveAttribute("href", "blob:synthetic-1");
    expect(link).toHaveAttribute("download", "documento");
    expect(create).toHaveBeenCalledWith(expect.any(Blob));
    await userEvent.click(screen.getByRole("button", { name: "Solicitação aberta" }));
    expect(revoke).toHaveBeenCalledWith("blob:synthetic-1");
    expect(screen.queryByRole("link", { name: "Baixar documento preparado" })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Abrir documento" }));
    await screen.findByRole("link", { name: "Baixar documento preparado" });
    view.unmount();
    expect(revoke).toHaveBeenCalledWith("blob:synthetic-2");
  } finally { vi.unstubAllGlobals(); }
});

it.each(["access-revoked", "session-unavailable"] as const)("remove todo detalhe protegido após paginação %s sem depender do pai", async (failure) => {
  const api = service({ listCases: vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: { ...casePageFixture, nextCursor: "next" } })
    .mockResolvedValueOnce({ kind: "failure", failure }) });
  render(<AudienceAuthorizationExperience audience="beneficiary" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
  await openDocuments();
  expect(screen.getByText("Envie o laudo solicitado")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Carregar mais" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Envie o laudo solicitado")).not.toBeInTheDocument();
  expect(screen.queryAllByText("Procedimento sintético")).toHaveLength(0);
  expect(screen.queryAllByText("receipt-synthetic-2")).toHaveLength(0);
});

it("descarta detalhe e download pendente quando muda o serviço", async () => {
  const pending = deferred<Awaited<ReturnType<ProviderAuthorizationService["downloadDocument"]>>>();
  const api = service({ downloadDocument: vi.fn().mockReturnValue(pending.promise) });
  const create = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
  try {
    const view = render(<AudienceAuthorizationExperience audience="beneficiary" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
    await openDocuments();
    await userEvent.click(screen.getByRole("button", { name: "Abrir documento" }));
    const signal = vi.mocked(api.downloadDocument).mock.calls[0][1];
    const next = service({ listCases: vi.fn().mockResolvedValue({ kind: "success", value: { ...casePageFixture, items: [] } }) });
    view.rerender(<AudienceAuthorizationExperience audience="beneficiary" service={next} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
    expect(signal.aborted).toBe(true);
    expect(screen.queryByText("Envie o laudo solicitado")).not.toBeInTheDocument();
    await act(async () => pending.resolve({ kind: "success", value: new Blob(["old protected bytes"]) }));
    expect(create).not.toHaveBeenCalled();
    expect(await screen.findAllByText("Nenhuma solicitação autorizada foi encontrada.")).not.toHaveLength(0);
  } finally { vi.unstubAllGlobals(); }
});

it.each(["admitted", "started"] as const)("remove progresso %s e referências ao mudar contexto do prestador", async (state) => {
  const progress = state === "started" ? { ...admittedIntake, state: "started" as const, caseRef: "old-case", startReceiptRef: "old-receipt" } : admittedIntake;
  const api = service({ submitAuthorization: vi.fn().mockResolvedValue({ kind: "success", progress }) });
  const callback = vi.fn();
  const view = render(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={callback} />);
  await submitIntake();
  await screen.findByText("Acompanhamento do envio");
  view.rerender(<AudienceAuthorizationExperience audience="provider" service={service()} communicationsClient={communications()} onSessionUnavailable={callback} />);
  await screen.findByRole("heading", { name: "Enviar nova solicitação" });
  expect(screen.queryByText("Acompanhamento do envio")).not.toBeInTheDocument();
  expect(screen.queryByText("old-case")).not.toBeInTheDocument();
  expect(screen.queryByText("old-receipt")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Enviar solicitação" })).toBeEnabled();
});

it("aborta envio antigo e mantém novo formulário utilizável", async () => {
  const pending = deferred<Awaited<ReturnType<ProviderAuthorizationService["submitAuthorization"]>>>();
  const api = service({ submitAuthorization: vi.fn().mockReturnValue(pending.promise) });
  const callback = vi.fn();
  const view = render(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={callback} />);
  await submitIntake();
  const signal = vi.mocked(api.submitAuthorization).mock.calls[0][1];
  expect(screen.getByRole("button", { name: "Enviando com segurança…" })).toBeDisabled();
  view.rerender(<AudienceAuthorizationExperience audience="provider" service={service()} communicationsClient={communications()} onSessionUnavailable={callback} />);
  await screen.findByRole("heading", { name: "Enviar nova solicitação" });
  expect(signal.aborted).toBe(true);
  expect(screen.getByRole("button", { name: "Enviar solicitação" })).toBeEnabled();
  await act(async () => pending.resolve({ kind: "success", progress: { ...admittedIntake, state: "started", caseRef: "old-case", startReceiptRef: "old-receipt" } }));
  expect(screen.queryByText("old-receipt")).not.toBeInTheDocument();
});

it("desmontar aborta a submissão corrente, não apenas a consulta inicial", async () => {
  const pending = deferred<Awaited<ReturnType<ProviderAuthorizationService["submitAuthorization"]>>>();
  const api = service({ submitAuthorization: vi.fn().mockReturnValue(pending.promise) });
  const view = render(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
  await submitIntake();
  const signal = vi.mocked(api.submitAuthorization).mock.calls[0][1];
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ kind: "success", progress: admittedIntake }));
});

it.each([["authorization", "Autorização"], ["reimbursement", "Reembolso"], ["account", "Conta"]] as const)("preserva kind W6 %s separado da audiência", async (kind, label) => {
  const api = service({ listCases: vi.fn().mockResolvedValue({ kind: "success", value: { ...casePageFixture, items: [{ ...casePageFixture.items[0], kind }] } }) });
  render(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
  await screen.findByRole("heading", { name: "Referência case_synthetic_auth_1" });
  expect(screen.getAllByText(label)).not.toHaveLength(0);
  expect(screen.getByText("Área do prestador")).toBeInTheDocument();
  expect(screen.queryAllByText("Solicitação do beneficiário")).toHaveLength(0);
  expect(screen.queryAllByText("Solicitação do prestador")).toHaveLength(0);
});


it("mantém o vocabulário e os cinco campos W6 no tipo público", () => {
  expectTypeOf<CaseSummaryView["kind"]>().toEqualTypeOf<"authorization" | "reimbursement" | "account">();
  expectTypeOf<keyof CaseSummaryView>().toEqualTypeOf<"caseRef" | "kind" | "state" | "recordRevision" | "stateObservedAt">();
  expect(Object.keys(casePageFixture.items[0]).sort()).toEqual(["caseRef", "kind", "recordRevision", "state", "stateObservedAt"]);
});

it("substituição libera URL preparada e revogação impede download pendente de recriá-la", async () => {
  const pending = deferred<Awaited<ReturnType<ProviderAuthorizationService["downloadDocument"]>>>();
  const create = vi.fn().mockReturnValue("blob:protected");
  const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
  try {
    const api = service({ listCases: vi.fn()
      .mockResolvedValueOnce({ kind: "success", value: { ...casePageFixture, nextCursor: "next" } })
      .mockResolvedValueOnce({ kind: "failure", failure: "access-revoked" }),
      downloadDocument: vi.fn().mockResolvedValueOnce({ kind: "success", value: new Blob(["protected"]) }).mockReturnValueOnce(pending.promise) });
    const view = render(<AudienceAuthorizationExperience audience="beneficiary" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
    await openDocuments();
    await userEvent.click(screen.getByRole("button", { name: "Abrir documento" }));
    await screen.findByRole("link", { name: "Baixar documento preparado" });
    await userEvent.click(screen.getByRole("button", { name: "Abrir documento" }));
    expect(revoke).toHaveBeenCalledWith("blob:protected");
    const signal = vi.mocked(api.downloadDocument).mock.calls[1][1];
    await userEvent.click(screen.getByRole("button", { name: "Carregar mais" }));
    await screen.findByRole("alert");
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve({ kind: "success", value: new Blob(["late"]) }));
    expect(create).toHaveBeenCalledOnce();
    expect(screen.queryByRole("link", { name: "Baixar documento preparado" })).not.toBeInTheDocument();
    view.unmount();
  } finally { vi.unstubAllGlobals(); }
});

it("revogação da lista remove progresso do prestador e exige formulário novo após recuperação", async () => {
  const api = service({ listCases: vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: { ...casePageFixture, nextCursor: "next" } })
    .mockResolvedValueOnce({ kind: "failure", failure: "access-revoked" })
    .mockResolvedValueOnce({ kind: "success", value: casePageFixture }),
    submitAuthorization: vi.fn().mockResolvedValue({ kind: "success", progress: { ...admittedIntake, state: "started", caseRef: "old-case", startReceiptRef: "old-receipt" } }) });
  render(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
  await submitIntake();
  expect(await screen.findByText("old-receipt")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Carregar mais" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("old-receipt")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Enviar nova solicitação" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Atualizar solicitações" }));
  expect(await screen.findByRole("button", { name: "Enviar solicitação" })).toBeEnabled();
  expect(api.readAuthorizationIntakeForm).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("old-receipt")).not.toBeInTheDocument();
});

it("falha de vínculo no envio remove formulário e detalhe protegido", async () => {
  const api = service({ submitAuthorization: vi.fn().mockResolvedValue({ kind: "failure", failure: "access-revoked" }) });
  render(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
  await screen.findByRole("button", { name: "Abrir solicitação" });
  await userEvent.click(screen.getByRole("button", { name: "Abrir solicitação" }));
  await screen.findByText("Procedimento sintético");
  await submitIntake();
  await screen.findByRole("alert");
  expect(screen.queryByText("Procedimento sintético")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Enviar nova solicitação" })).not.toBeInTheDocument();
});

it("troca de audiência encerra o contexto mesmo com o mesmo serviço", async () => {
  const api = service();
  const callback = vi.fn();
  const view = render(<AudienceAuthorizationExperience audience="beneficiary" service={api} communicationsClient={communications()} onSessionUnavailable={callback} />);
  await openDocuments();
  view.rerender(<AudienceAuthorizationExperience audience="provider" service={api} communicationsClient={communications()} onSessionUnavailable={callback} />);
  expect(screen.queryByText("Envie o laudo solicitado")).not.toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Enviar solicitação" })).toBeEnabled();
  expect(api.listCases).toHaveBeenCalledTimes(2);
});


it("revogação remove e libera diretamente o link de download já preparado", async () => {
  const create = vi.fn().mockReturnValue("blob:revoked");
  const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
  try {
    const api = service({ listCases: vi.fn()
      .mockResolvedValueOnce({ kind: "success", value: { ...casePageFixture, nextCursor: "next" } })
      .mockResolvedValueOnce({ kind: "failure", failure: "access-revoked" }) });
    const view = render(<AudienceAuthorizationExperience audience="beneficiary" service={api} communicationsClient={communications()} onSessionUnavailable={vi.fn()} />);
    await openDocuments();
    await userEvent.click(screen.getByRole("button", { name: "Abrir documento" }));
    await screen.findByRole("link", { name: "Baixar documento preparado" });
    expect(revoke).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Carregar mais" }));
    await screen.findByRole("alert");
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:revoked");
    expect(screen.queryByRole("link", { name: "Baixar documento preparado" })).not.toBeInTheDocument();
    view.unmount();
    expect(revoke).toHaveBeenCalledOnce();
  } finally { vi.unstubAllGlobals(); }
});
