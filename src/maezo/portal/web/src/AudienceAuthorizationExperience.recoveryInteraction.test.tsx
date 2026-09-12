import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AudienceAuthorizationExperience } from "./AudienceAuthorizationExperience";
import { createCaseExperienceService } from "./caseExperienceServiceFactory";
import { casePageFixture, intakeFormFixture } from "./test/caseExperienceFixtures";

const commandId = "command_original_abcdefghijklmnop";
const intakeRef = "intake_original_abcdefghijklmnop";
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

function fixture() {
  const bodies: string[] = [];
  const lookups: { signal: AbortSignal; resolve: (response: Response) => void }[] = [];
  const nextCommand = vi.fn(() => commandId);
  const fetcher = vi.fn(async (path: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (init?.method === "POST") {
      bodies.push(String(init.body));
      if (bodies.length === 1) throw new TypeError("synthetic lost acknowledgement");
      return reply({ schema_version: 1, command_id: commandId, intake_ref: intakeRef,
        revision: "0", disposition: "admitted", case_ref: null, start_receipt_ref: null }, 202);
    }
    if (String(path).includes("/intake-recovery/commands/")) {
      const signal = init?.signal;
      if (!signal) throw new Error("signal missing");
      // Deliberately tolerate abort to test late-result suppression too.
      return new Promise<Response>((resolve) => { lookups.push({ signal, resolve }); });
    }
    throw new Error("unexpected synthetic fetch path");
  });
  const actual = createCaseExperienceService({ audience: "provider", csrfToken: "synthetic",
    fetcher, commandId: nextCommand,
    intakeFormProvider: { read: async () => ({ kind: "success", value: intakeFormFixture }) } });
  const service = { ...actual,
    submitAuthorization: vi.fn(actual.submitAuthorization),
    listCases: vi.fn().mockResolvedValue({ kind: "success", value: { ...casePageFixture, items: [] } }),
    discoverAuthorizationIntakes: vi.fn().mockResolvedValue({ kind: "success", value: { items: [], nextCursor: null } }),
  };
  const communications = { listCommunications: vi.fn(), listHistory: vi.fn(), publishCommunication: vi.fn() };
  return { service, communications, bodies, lookups, nextCommand };
}

async function fillAndLoseAcknowledgement() {
  await screen.findByRole("heading", { name: "Enviar nova solicitação" });
  for (const [label, value] of [
    ["Beneficiário", "beneficiary_synthetic_1"], ["Prestador solicitante", "provider_synthetic_1"],
    ["Guia TISS protegida", "guide_ref_synthetic_1"], ["Categoria do procedimento", "consulta"],
    ["Caráter do atendimento", "eletivo"], ["Código do procedimento TUSS", "10101012"],
  ]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.change(screen.getByLabelText(/^Valor estimado em centavos/), { target: { value: "12500" } });
  await userEvent.click(screen.getByRole("button", { name: "Enviar solicitação" }));
  return screen.findByRole("button", { name: "Verificar comando original" });
}

it.each(["not-observed", "unavailable"])("preserva consulta %s contra clique e submit por teclado, liberando repetição exata", async (outcome) => {
  const h = fixture();
  render(<AudienceAuthorizationExperience audience="provider" service={h.service}
    communicationsClient={h.communications} onSessionUnavailable={vi.fn()} />);
  const observe = await fillAndLoseAcknowledgement();
  await userEvent.click(observe);
  await waitFor(() => expect(h.lookups).toHaveLength(1));
  const submit = screen.getByRole("button", { name: "Enviar solicitação" });
  expect(submit).toBeDisabled();
  await userEvent.click(submit);
  // Direct submit models Enter/requestSubmit and double-event paths, which
  // cannot rely only on the disabled button to serialize operation ownership.
  fireEvent.submit(submit.closest("form")!);
  fireEvent.submit(submit.closest("form")!);
  expect(h.service.submitAuthorization).toHaveBeenCalledTimes(1);
  expect(h.bodies).toHaveLength(1);
  expect(h.lookups[0].signal.aborted).toBe(false);
  await act(async () => h.lookups[0].resolve(outcome === "not-observed"
    ? reply({ schema_version: 1, command_id: commandId, observation: "not_observed", intake_ref: null })
    : reply({ code: "dependency_unavailable" }, 503)));
  await waitFor(() => expect(submit).toBeEnabled());
  expect(screen.getByRole("button", { name: outcome === "not-observed"
    ? "Consultar comando novamente" : "Verificar comando original" })).toBeEnabled();
  expect(h.bodies).toHaveLength(1);
  // Only this deliberate user action sends again, with the original body/ID.
  await userEvent.click(submit);
  await screen.findByText("Acompanhamento do envio");
  expect(h.bodies).toHaveLength(2);
  expect(h.bodies[1]).toBe(h.bodies[0]);
  expect(h.nextCommand).toHaveBeenCalledTimes(1);
});

it("aborta consulta no novo contexto e seu finally antigo não libera a consulta nova", async () => {
  const old = fixture();
  const callback = vi.fn();
  const view = render(<AudienceAuthorizationExperience audience="provider" service={old.service}
    communicationsClient={old.communications} onSessionUnavailable={callback} />);
  await userEvent.click(await fillAndLoseAcknowledgement());
  await waitFor(() => expect(old.lookups).toHaveLength(1));
  const current = fixture();
  view.rerender(<AudienceAuthorizationExperience audience="provider" service={current.service}
    communicationsClient={current.communications} onSessionUnavailable={callback} />);
  await screen.findByRole("button", { name: "Enviar solicitação" });
  expect(old.lookups[0].signal.aborted).toBe(true);
  await userEvent.click(await fillAndLoseAcknowledgement());
  await waitFor(() => expect(current.lookups).toHaveLength(1));
  await act(async () => old.lookups[0].resolve(reply({ schema_version: 1, command_id: commandId,
    observation: "not_observed", intake_ref: null })));
  expect(screen.getByRole("button", { name: "Consultando comando original…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Enviar solicitação" })).toBeDisabled();
  expect(current.lookups[0].signal.aborted).toBe(false);
  expect(current.bodies).toHaveLength(1);
  view.unmount();
  expect(current.lookups[0].signal.aborted).toBe(true);
  await act(async () => current.lookups[0].resolve(reply({ code: "dependency_unavailable" }, 503)));
});
