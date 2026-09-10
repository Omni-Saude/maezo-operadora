import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AudienceAuthorizationExperience } from "./AudienceAuthorizationExperience";
import type { ProviderAuthorizationService } from "./caseExperienceModels";
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

it("leva beneficiário de solicitações a documentos, mensagens e recibos do caso autorizado", async () => {
  const api = service();
  render(
    <AudienceAuthorizationExperience
      audience="beneficiary"
      service={api}
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
  expect(screen.getByText("Documentos adicionais")).toBeInTheDocument();

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
      onSessionUnavailable={vi.fn()}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Uma dependência não respondeu");
  expect(screen.queryByText(/synthetic private detail/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Atualizar solicitações" })).toBeInTheDocument();
});
