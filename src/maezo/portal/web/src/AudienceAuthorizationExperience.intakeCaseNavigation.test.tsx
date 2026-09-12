import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AudienceAuthorizationExperience } from "./AudienceAuthorizationExperience";
import type {
  AuthorizationIntakeProgressView,
  ProviderAuthorizationService,
} from "./caseExperienceModels";
import { intakeFormFixture, workspaceFixture } from "./test/caseExperienceFixtures";

const caseRef = "case_started_abcdefghijklmnop";
const emptyPage = {
  items: [],
  nextCursor: null,
  observedAt: "2099-09-10T12:00:00.000000Z",
  validUntil: "2099-09-10T13:00:00.000000Z",
} as const;
const started: AuthorizationIntakeProgressView = {
  intakeRef: "intake_started_abcdefghijklmnop",
  state: "started",
  stateLabel: "Caso iniciado",
  description: "O início foi confirmado pelo recibo.",
  revision: "3",
  caseRef,
  startReceiptRef: "receipt_started_abcdefghijklmnop",
};

function service(
  overrides: Partial<ProviderAuthorizationService> = {},
): ProviderAuthorizationService {
  return {
    listCases: vi.fn().mockResolvedValue({ kind: "success", value: emptyPage }),
    readCase: vi.fn().mockResolvedValue({
      kind: "success",
      value: {
        ...workspaceFixture,
        summary: { ...workspaceFixture.summary, caseRef },
      },
    }),
    downloadDocument: vi.fn(),
    readAuthorizationIntakeForm: vi.fn().mockResolvedValue({
      kind: "success",
      value: intakeFormFixture,
    }),
    submitAuthorization: vi.fn().mockResolvedValue({ kind: "success", progress: started }),
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
      kind: "success",
      value: { items: [], nextCursor: null },
    }),
    observePendingAuthorization: vi.fn().mockResolvedValue({ kind: "none" }),
    ...overrides,
  };
}

const communications = {
  listCommunications: vi.fn(),
  listHistory: vi.fn(),
  publishCommunication: vi.fn(),
};

async function submitStartedIntake() {
  const heading = await screen.findByRole("heading", { name: "Enviar nova solicitação" });
  const form = heading.closest("section");
  if (form === null) throw new Error("intake form region missing");
  for (const [label, value] of [
    ["Beneficiário", "beneficiary_synthetic_1"],
    ["Prestador solicitante", "provider_synthetic_1"],
    ["Guia TISS protegida", "guide_ref_synthetic_1"],
    ["Categoria do procedimento", "consulta"],
    ["Caráter do atendimento", "eletivo"],
    ["Código do procedimento TUSS", "10101012"],
  ]) {
    fireEvent.change(screen.getByLabelText(label, { selector: "select, input" }), {
      target: { value },
    });
  }
  fireEvent.change(screen.getByLabelText(/^Valor estimado em centavos/), {
    target: { value: "12500" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Enviar solicitação" }));
}

it("abre pelo case_ref confirmado no recibo do envio sem depender da primeira página", async () => {
  const api = service();
  render(
    <AudienceAuthorizationExperience
      audience="provider"
      service={api}
      communicationsClient={communications}
      onSessionUnavailable={vi.fn()}
    />,
  );

  await submitStartedIntake();
  await userEvent.click(await screen.findByRole("button", { name: "Abrir caso iniciado" }));

  await waitFor(() => expect(api.readCase).toHaveBeenCalledWith(caseRef, expect.any(AbortSignal)));
  expect(await screen.findByRole("heading", { name: "Dossiê autorizado" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Solicitações" })).toHaveAttribute("aria-selected", "true");
});

it("oferece a abertura somente para admissão recuperada que já confirmou o início", async () => {
  const api = service({
    submitAuthorization: vi.fn().mockResolvedValue({
      kind: "success",
      progress: { ...started, state: "admitted", stateLabel: "Solicitação recebida" },
    }),
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
      kind: "success",
      value: {
        items: [
          {
            commandId: "command_started_abcdefghijklmnop",
            progress: started,
          },
          {
            commandId: "command_pending_abcdefghijklmnop",
            progress: {
              intakeRef: "intake_pending_abcdefghijklmnop",
              state: "admitted",
              stateLabel: "Solicitação recebida",
              description: "O comando foi admitido.",
            },
          },
        ],
        nextCursor: null,
      },
    }),
  });
  render(
    <AudienceAuthorizationExperience
      audience="provider"
      service={api}
      communicationsClient={communications}
      onSessionUnavailable={vi.fn()}
    />,
  );

  const actions = await screen.findAllByRole("button", { name: "Abrir caso iniciado" });
  expect(actions).toHaveLength(1);
  await userEvent.click(actions[0]);
  await waitFor(() => expect(api.readCase).toHaveBeenCalledWith(caseRef, expect.any(AbortSignal)));
});

it("aborta a abertura exata e ignora a resposta tardia quando a sessão troca o serviço", async () => {
  let resolve!: (result: Awaited<ReturnType<ProviderAuthorizationService["readCase"]>>) => void;
  const pending = new Promise<Awaited<ReturnType<ProviderAuthorizationService["readCase"]>>>(
    (done) => { resolve = done; },
  );
  const old = service({
    readCase: vi.fn().mockReturnValue(pending),
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
      kind: "success",
      value: {
        items: [{ commandId: "command_started_abcdefghijklmnop", progress: started }],
        nextCursor: null,
      },
    }),
  });
  const fresh = service();
  const view = render(
    <AudienceAuthorizationExperience
      audience="provider"
      service={old}
      communicationsClient={communications}
      onSessionUnavailable={vi.fn()}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Abrir caso iniciado" }));
  const signal = vi.mocked(old.readCase).mock.calls[0][1];
  view.rerender(
    <AudienceAuthorizationExperience
      audience="provider"
      service={fresh}
      communicationsClient={communications}
      onSessionUnavailable={vi.fn()}
    />,
  );
  expect(signal.aborted).toBe(true);

  await act(async () => resolve({
    kind: "success",
    value: { ...workspaceFixture, summary: { ...workspaceFixture.summary, caseRef } },
  }));
  expect(screen.queryByRole("heading", { name: "Dossiê autorizado" })).not.toBeInTheDocument();
  expect(
    (await screen.findAllByText("Nenhuma solicitação autorizada foi encontrada.")).length,
  ).toBeGreaterThan(0);
});
