import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { IntakeRecoveryPanel } from "./IntakeRecoveryPanel";
import type {
  AuthorizationIntakeProgressView,
  ProviderAuthorizationService,
} from "./caseExperienceModels";

const ref = (name: string) => `${name}_abcdefghijklmnop`;

function progress(
  overrides: Partial<AuthorizationIntakeProgressView> = {},
): AuthorizationIntakeProgressView {
  return {
    intakeRef: ref("intake"),
    state: "reconciling",
    stateLabel: "Conferindo resultado",
    description: "O portal está conciliando o processamento.",
    revision: "2",
    ...overrides,
  } as AuthorizationIntakeProgressView;
}

function service(overrides: Partial<ProviderAuthorizationService> = {}): ProviderAuthorizationService {
  return {
    listCases: vi.fn(),
    readCase: vi.fn(),
    downloadDocument: vi.fn(),
    readAuthorizationIntakeForm: vi.fn(),
    submitAuthorization: vi.fn(),
    observePendingAuthorization: vi.fn().mockResolvedValue({ kind: "none" }),
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
      kind: "success",
      value: {
        items: [{ commandId: ref("command"), progress: progress() }],
        nextCursor: null,
      },
    }),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

it("mostra acompanhamento reautorizado sem promover ponteiro a efeito", async () => {
  const api = service({
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
      kind: "success",
      value: {
        items: [{
          commandId: ref("command"),
          progress: progress({
            state: "started",
            stateLabel: "Caso iniciado",
            description: "O início foi confirmado pelo recibo.",
            caseRef: ref("case"),
            startReceiptRef: ref("receipt"),
          }),
        }],
        nextCursor: null,
      },
    }),
  });
  render(<IntakeRecoveryPanel service={api} onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "Caso iniciado" })).toBeInTheDocument();
  expect(screen.getByText(ref("command"))).toBeInTheDocument();
  expect(screen.getByText(ref("intake"))).toBeInTheDocument();
  expect(screen.getByText(ref("case"))).toBeInTheDocument();
  expect(screen.getByText(ref("receipt"))).toBeInTheDocument();
  expect(screen.getByText(/não reenvia solicitações/i)).toBeInTheDocument();
  expect(screen.queryByText(/guia|beneficiário|valor|documento/i)).not.toBeInTheDocument();
});

it("mantém ausência observada como incerteza e oferece consulta fresca", async () => {
  const discover = vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: { items: [], nextCursor: null } })
    .mockResolvedValueOnce({ kind: "success", value: {
      items: [{ commandId: ref("command"), progress: progress() }], nextCursor: null,
    } });
  render(<IntakeRecoveryPanel service={service({ discoverAuthorizationIntakes: discover })} onSessionUnavailable={vi.fn()} />);
  expect(await screen.findByText(/Um envio incerto ainda pode ser confirmado/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Fazer nova consulta" }));
  expect(await screen.findByText(ref("command"))).toBeInTheDocument();
  expect(discover).toHaveBeenNthCalledWith(1, expect.any(AbortSignal));
  expect(discover).toHaveBeenNthCalledWith(2, expect.any(AbortSignal));
});

it("pagina ponteiros e recusa repetição entre páginas", async () => {
  const discover = vi.fn()
    .mockResolvedValueOnce({ kind: "success", value: {
      items: [{ commandId: ref("command"), progress: progress() }], nextCursor: ref("cursor"),
    } })
    .mockResolvedValueOnce({ kind: "success", value: {
      items: [{ commandId: ref("command"), progress: progress() }], nextCursor: null,
    } });
  render(<IntakeRecoveryPanel service={service({ discoverAuthorizationIntakes: discover })} onSessionUnavailable={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Consultar próxima página" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("resposta inesperada");
  expect(screen.queryByText(ref("command"))).not.toBeInTheDocument();
  expect(discover).toHaveBeenLastCalledWith(expect.any(AbortSignal), ref("cursor"));
});

it("propaga indisponibilidade da sessão e não oferece repetição local", async () => {
  const unavailable = vi.fn();
  render(<IntakeRecoveryPanel service={service({
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
      kind: "failure", failure: "session-unavailable",
    }),
  })} onSessionUnavailable={unavailable} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("sessão não está mais disponível");
  expect(unavailable).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: /consultar novamente/i })).not.toBeInTheDocument();
});

it("aborta e limpa a projeção antiga ao substituir o serviço", async () => {
  const pending = deferred<Awaited<ReturnType<ProviderAuthorizationService["discoverAuthorizationIntakes"]>>>();
  const old = service({ discoverAuthorizationIntakes: vi.fn().mockReturnValue(pending.promise) });
  const fresh = service({ discoverAuthorizationIntakes: vi.fn().mockResolvedValue({
    kind: "success", value: { items: [], nextCursor: null },
  }) });
  const view = render(<IntakeRecoveryPanel service={old} onSessionUnavailable={vi.fn()} />);
  const signal = vi.mocked(old.discoverAuthorizationIntakes).mock.calls[0][0];
  view.rerender(<IntakeRecoveryPanel service={fresh} onSessionUnavailable={vi.fn()} />);
  expect(signal.aborted).toBe(true);
  expect(await screen.findByText(/Um envio incerto ainda pode ser confirmado/i)).toBeInTheDocument();
  await act(async () => pending.resolve({ kind: "success", value: {
    items: [{ commandId: ref("old_command"), progress: progress() }], nextCursor: null,
  } }));
  expect(screen.queryByText(ref("old_command"))).not.toBeInTheDocument();
});

it("mantém região nomeada e não usa storage persistente", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  render(<IntakeRecoveryPanel service={service()} onSessionUnavailable={vi.fn()} />);
  const region = screen.getByRole("heading", { name: "Solicitações já recebidas" }).closest("section");
  expect(region).not.toBeNull();
  expect(await within(region!).findByText(ref("command"))).toBeInTheDocument();
  expect(setItem).not.toHaveBeenCalled();
});
