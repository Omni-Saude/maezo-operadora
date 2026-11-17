import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { LegitimacyScreenPanel } from "./LegitimacyScreenPanel";
import { createLegitimacyScreenClient, type LegitimacyChecklist } from "./legitimacyScreenClient";
import { fakeTransport, json, posts, screenRecord, serverChecklist } from "./test/legitimacyFixtures";

// E-SF-20 (EP-CORE-15a) guard 2 — art. 29 §1º is not a field. Violation: a manifest/UI that
// produces an input for the art. 29 §1º prohibition. The structural criterion is text only:
// no form control for it, even when a hostile manifest hands it fields and options, and no
// key for it in what the panel sends.

const CONTROLS = "input, select, textarea, button, [role='checkbox'], [role='radio'], [role='switch']";

function structural(checklist: LegitimacyChecklist) {
  const found = checklist.criterios.filter((criterion) => criterion.estrutural);
  expect(found).toHaveLength(1);
  return found[0];
}

async function renderWith(checklist: LegitimacyChecklist) {
  const transport = fakeTransport((call) =>
    call.method === "POST" ? json(screenRecord(), 201) : json(checklist),
  );
  render(
    <LegitimacyScreenPanel
      client={createLegitimacyScreenClient({ fetcher: transport.fetcher })}
      csrfToken="csrf"
      onSessionUnavailable={vi.fn()}
    />,
  );
  await screen.findByRole("heading", { name: "Legitimidade coletiva" });
  return transport;
}

function assertTextNeverControl(rotulo: string) {
  const region = screen.getByRole("region", { name: rotulo });
  expect(within(region).getByText(rotulo)).toBeInTheDocument();
  expect(region.querySelectorAll(CONTROLS)).toHaveLength(0);
  for (const control of Array.from(document.querySelectorAll(CONTROLS))) {
    expect(control.getAttribute("aria-label") ?? "").not.toContain("29, § 1º");
    const labelled = control.id ? document.querySelector(`label[for="${control.id}"]`) : null;
    expect(labelled?.textContent ?? "").not.toContain("29, § 1º");
    expect(control.closest(".legitimacy-structural")).toBeNull();
  }
}

it("shows the art. 29 §1º prohibition as text and never as a control", async () => {
  const checklist = serverChecklist();
  await renderWith(checklist);
  const criterion = structural(checklist);
  expect(criterion.norm_anchor).toBe("RN 557/2022, art. 29, § 1º");
  assertTextNeverControl(criterion.rotulo);
});

it("a hostile manifest that gives the prohibition fields and options still yields no control", async () => {
  const hostile = serverChecklist();
  const criterion = structural(hostile);
  criterion.campos = [{ campo_id: "vinculo_beneficiario_contratante", norm_anchor: criterion.norm_anchor }];
  criterion.opcoes = [{ valor: "proven", rotulo: "confirmo a vedação" }];
  await renderWith(hostile);
  assertTextNeverControl(criterion.rotulo);
  expect(screen.queryByText("confirmo a vedação")).toBeNull();
  // Exactly one select per non-structural manifest field the wire can carry.
  const fields = hostile.criterios
    .filter((candidate) => !candidate.estrutural)
    .flatMap((candidate) => candidate.campos);
  expect(document.querySelectorAll("select")).toHaveLength(fields.length);
});

it("sends no key for the structural criterion", async () => {
  const hostile = serverChecklist();
  structural(hostile).campos = [{ campo_id: "documentos_art9", norm_anchor: "RN 557/2022, art. 29, § 1º" }];
  const transport = await renderWith(hostile);
  await userEvent.type(screen.getByLabelText("Referência da contratante"), "pj-ref-1");
  await userEvent.type(screen.getByLabelText("Referência da oportunidade"), "op-ref-1");
  const kind = screen.getAllByRole("combobox")[0];
  const form = screen.getAllByRole("combobox")[1];
  await userEvent.selectOptions(kind, "empresarial");
  await userEvent.selectOptions(form, "direta");
  const results = screen.getAllByRole("combobox").filter((select) => select.id.endsWith("vinculo_beneficiario_contratante"));
  await userEvent.selectOptions(results[0], "proven");
  await userEvent.click(screen.getByRole("button", { name: "Registrar verificação" }));
  await screen.findByRole("status");
  const sent = posts(transport.calls);
  expect(sent).toHaveLength(1);
  const body = sent[0].body as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(
    ["contract_kind", "contracting_form", "legal_entity_ref", "opportunity_ref", "schema_version", "vinculo_beneficiario_contratante"],
  );
  expect(body).not.toHaveProperty("documentos_art9");
  expect(JSON.stringify(body)).not.toContain("vedacao");
});
