import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { LegitimacyScreenPanel } from "./LegitimacyScreenPanel";
import { createLegitimacyScreenClient, type LegitimacyChecklist } from "./legitimacyScreenClient";
import { fakeTransport, json, serverChecklist } from "./test/legitimacyFixtures";

// E-SF-20 (EP-CORE-15a) guard 1 — labels verbatim. Violation: a component label hardcoded
// apart from the manifest. The panel must render every criterion and option label byte for
// byte as the BFF manifest serves it, and carry none of them in its own source.

async function renderWith(checklist: LegitimacyChecklist) {
  const { fetcher } = fakeTransport(() => json(checklist));
  render(
    <LegitimacyScreenPanel
      client={createLegitimacyScreenClient({ fetcher })}
      csrfToken="csrf"
      onSessionUnavailable={vi.fn()}
    />,
  );
  await screen.findByRole("heading", { name: "Legitimidade coletiva" });
}

function optionTexts() {
  return Array.from(document.querySelectorAll("option")).map((option) => option.textContent);
}

it("renders the five criteria with the manifest labels, byte for byte", async () => {
  const checklist = serverChecklist();
  await renderWith(checklist);
  expect(checklist.criterios).toHaveLength(5);
  const shown = Array.from(document.querySelectorAll("legend, .legitimacy-structural > p:first-child"))
    .map((node) => node.textContent);
  expect(shown).toEqual(checklist.criterios.map((criterion) => criterion.rotulo));
  for (const criterion of checklist.criterios.filter((candidate) => !candidate.estrutural)) {
    expect(screen.getByRole("group", { name: criterion.rotulo })).toBeInTheDocument();
  }
});

it("renders every option and result label exactly as served", async () => {
  const checklist = serverChecklist();
  await renderWith(checklist);
  const texts = optionTexts();
  for (const option of checklist.criterios.flatMap((criterion) => criterion.opcoes)) {
    expect(texts).toContain(option.rotulo);
  }
  for (const result of checklist.resultados) {
    expect(texts).toContain(result.rotulo);
  }
});

it("follows a manifest whose labels changed: nothing is hardcoded in the component", async () => {
  const served = serverChecklist();
  const changed = structuredClone(served);
  for (const criterion of changed.criterios) {
    criterion.rotulo = `revisado: ${criterion.rotulo}`;
    for (const option of criterion.opcoes) option.rotulo = `revisado: ${option.rotulo}`;
  }
  for (const result of changed.resultados) result.rotulo = `revisado: ${result.rotulo}`;
  await renderWith(changed);
  const legends = Array.from(document.querySelectorAll("legend")).map((node) => node.textContent);
  for (const criterion of served.criterios.filter((candidate) => !candidate.estrutural)) {
    expect(legends).toContain(`revisado: ${criterion.rotulo}`);
    expect(legends).not.toContain(criterion.rotulo);
  }
  const texts = optionTexts();
  for (const result of served.resultados) expect(texts).not.toContain(result.rotulo);
});

it("carries no criterion, option or result label of the manifest in its own source", () => {
  const sources = ["./LegitimacyScreenPanel.tsx", "./legitimacyScreenClient.ts"].map((path) =>
    readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8"),
  );
  const checklist = serverChecklist();
  const labels = [
    ...checklist.criterios.map((criterion) => criterion.rotulo),
    ...checklist.criterios.flatMap((criterion) => criterion.opcoes.map((option) => option.rotulo)),
    ...checklist.resultados.map((result) => result.rotulo),
  ];
  for (const source of sources) {
    for (const label of labels) expect(source).not.toContain(label);
  }
});
