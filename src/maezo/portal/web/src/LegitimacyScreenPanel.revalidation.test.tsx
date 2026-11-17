import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { LegitimacyScreenPanel } from "./LegitimacyScreenPanel";
import { createLegitimacyScreenClient } from "./legitimacyScreenClient";
import { fakeTransport, json, posts, screenRecord, serverChecklist } from "./test/legitimacyFixtures";

// E-SF-20 (EP-CORE-15a) acceptance, flag ON in a local test rig only: revalidation sends the
// KNOWN revision and shows the new `screen_revision` the server minted; a stale revision is
// the server's 409, shown as such, never retried with a guessed number.

const LATEST = "/api/v1/portal/legitimacy-screens/contratante/pj-ref-1";
const REVALIDATE = `/api/v1/portal/legitimacy-screens/${screenRecord().screen_ref}`;

async function fillAndSubmit(button: string) {
  await userEvent.type(screen.getByLabelText("Referência da oportunidade"), "op-ref-1");
  const [kind, form] = screen.getAllByRole("combobox");
  await userEvent.selectOptions(kind, "empresarial");
  await userEvent.selectOptions(form, "direta");
  const vinculo = screen.getAllByRole("combobox").find((select) => select.id.endsWith("vinculo_beneficiario_contratante"))!;
  await userEvent.selectOptions(vinculo, "proven");
  await userEvent.click(screen.getByRole("button", { name: button }));
}

function renderPanel(answer: Parameters<typeof fakeTransport>[0]) {
  const transport = fakeTransport(answer);
  render(
    <LegitimacyScreenPanel
      client={createLegitimacyScreenClient({ fetcher: transport.fetcher })}
      csrfToken="csrf"
      onSessionUnavailable={vi.fn()}
    />,
  );
  return transport;
}

it("revalidates with the known revision and shows the revision the server minted", async () => {
  const transport = renderPanel((call) => {
    if (call.path === LATEST) return json(screenRecord({ screen_revision: 4, status: "pending" }));
    if (call.method === "POST") return json(screenRecord({ screen_revision: 5 }), 201);
    return json(serverChecklist());
  });
  await screen.findByRole("heading", { name: "Legitimidade coletiva" });
  await userEvent.type(screen.getByLabelText("Referência da contratante"), "pj-ref-1");
  await userEvent.click(screen.getByRole("button", { name: "Consultar última verificação" }));
  expect(await screen.findByText(/Verificação atual: revisão 4, situação pendente/)).toBeInTheDocument();
  await fillAndSubmit("Revalidar verificação");
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Verificação registrada pelo servidor: revisão 5, situação válida.",
  );
  const sent = posts(transport.calls);
  expect(sent.map((call) => call.path)).toEqual([REVALIDATE]);
  expect((sent[0].body as Record<string, unknown>).screen_revision).toBe(4);
});

it("a stale revision is the server's refusal, shown and never retried", async () => {
  const transport = renderPanel((call) => {
    if (call.path === LATEST) return json(screenRecord({ screen_revision: 2 }));
    if (call.method === "POST") return json({ code: "stale_revision" }, 409);
    return json(serverChecklist());
  });
  await screen.findByRole("heading", { name: "Legitimidade coletiva" });
  await userEvent.type(screen.getByLabelText("Referência da contratante"), "pj-ref-1");
  await userEvent.click(screen.getByRole("button", { name: "Consultar última verificação" }));
  await screen.findByText(/Verificação atual: revisão 2/);
  await fillAndSubmit("Revalidar verificação");
  expect(await screen.findByRole("status")).toHaveTextContent("A verificação mudou desde a consulta");
  expect(posts(transport.calls)).toHaveLength(1);
});

it("without a current screen the first record is a create, not a revalidation", async () => {
  const transport = renderPanel((call) => {
    if (call.path === LATEST) return json({ code: "resource_unavailable" }, 404);
    if (call.method === "POST") return json(screenRecord(), 201);
    return json(serverChecklist());
  });
  await screen.findByRole("heading", { name: "Legitimidade coletiva" });
  await userEvent.type(screen.getByLabelText("Referência da contratante"), "pj-ref-1");
  await userEvent.click(screen.getByRole("button", { name: "Consultar última verificação" }));
  await screen.findByText("Nenhuma verificação registrada para esta contratante.");
  await fillAndSubmit("Registrar verificação");
  expect(await screen.findByRole("status")).toHaveTextContent("revisão 1, situação válida");
  const sent = posts(transport.calls);
  expect(sent.map((call) => call.path)).toEqual(["/api/v1/portal/legitimacy-screens"]);
  expect(sent[0].body).not.toHaveProperty("screen_revision");
});
