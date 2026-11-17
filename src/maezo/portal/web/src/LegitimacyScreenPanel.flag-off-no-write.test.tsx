import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { LegitimacyScreenPanel, legitimacyNotEnabledMessage } from "./LegitimacyScreenPanel";
import { createLegitimacyScreenClient } from "./legitimacyScreenClient";
import { fakeTransport, json, posts, serverChecklist } from "./test/legitimacyFixtures";

// E-SF-20 (EP-CORE-15a) guard 3 — flag OFF, no write. Violation: submit enabled after the
// 403 `operation_forbidden` of the GET checklist. The flag `collective_legitimacy_screen`
// ships OFF (owner act, CA-11): the BFF answers 403 (flag) or 503 (unbound slot) and the
// area reads "não habilitada" with zero POST requests.

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

it.each([
  [403, "operation_forbidden"],
  [503, "dependency_unavailable"],
])("checklist %i %s: the area is not enabled and nothing can write", async (status, code) => {
  // Every route of the dark surface refuses, POSTs included: only absence of the attempt proves it.
  const transport = renderPanel(() => json({ code }, status));
  expect(await screen.findByText(legitimacyNotEnabledMessage)).toBeInTheDocument();
  expect(document.querySelectorAll("form, input, select, textarea, button")).toHaveLength(0);
  for (const element of Array.from(document.querySelectorAll("*"))) {
    await userEvent.click(element);
  }
  expect(transport.calls.map((call) => `${call.method} ${call.path}`)).toEqual([
    "GET /api/v1/portal/legitimacy-screens/checklist",
  ]);
  expect(posts(transport.calls)).toHaveLength(0);
});

it("a 403 on a write turns the area off and no second write can leave", async () => {
  const transport = renderPanel((call) =>
    call.method === "POST" ? json({ code: "operation_forbidden" }, 403) : json(serverChecklist()),
  );
  await screen.findByRole("heading", { name: "Legitimidade coletiva" });
  await userEvent.type(screen.getByLabelText("Referência da contratante"), "pj-ref-1");
  await userEvent.type(screen.getByLabelText("Referência da oportunidade"), "op-ref-1");
  const [kind, form] = screen.getAllByRole("combobox");
  await userEvent.selectOptions(kind, "empresarial");
  await userEvent.selectOptions(form, "direta");
  const vinculo = screen.getAllByRole("combobox").find((select) => select.id.endsWith("vinculo_beneficiario_contratante"))!;
  await userEvent.selectOptions(vinculo, "pending");
  await userEvent.click(screen.getByRole("button", { name: "Registrar verificação" }));
  expect(await screen.findByText(legitimacyNotEnabledMessage)).toBeInTheDocument();
  expect(document.querySelectorAll("button, select, input")).toHaveLength(0);
  expect(posts(transport.calls)).toHaveLength(1);
});

it("a session that is gone leaves to the session surface without writing", async () => {
  const onSessionUnavailable = vi.fn();
  const transport = fakeTransport(() => json({ code: "authentication_unavailable" }, 401));
  render(
    <LegitimacyScreenPanel
      client={createLegitimacyScreenClient({ fetcher: transport.fetcher })}
      csrfToken="csrf"
      onSessionUnavailable={onSessionUnavailable}
    />,
  );
  await vi.waitFor(() => expect(onSessionUnavailable).toHaveBeenCalledTimes(1));
  expect(posts(transport.calls)).toHaveLength(0);
});
