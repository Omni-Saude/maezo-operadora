import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { StaffPortalWorkspace } from "./StaffPortalWorkspace";

function panels() {
  return {
    overview: <h2>Resumo autorizado</h2>,
    "my-work": <h2>Minhas tarefas</h2>,
    "team-queues": <h2>Tarefas da equipe</h2>,
    cases: <h2>Casos autorizados</h2>,
    documents: <h2>Documentos autorizados</h2>,
    operations: <h2>Operações autorizadas</h2>,
    administration: <h2>Administração governada</h2>,
  } as const;
}

it("oferece as sete áreas de colaboradores sem criar atalhos de decisão em lote", () => {
  render(<StaffPortalWorkspace panels={panels()} />);
  const tabs = screen.getAllByRole("tab");
  expect(tabs).toHaveLength(7);
  expect(tabs.map((tab) => tab.textContent)).toEqual([
    expect.stringContaining("Visão geral"),
    expect.stringContaining("Meu trabalho"),
    expect.stringContaining("Filas da equipe"),
    expect.stringContaining("Casos"),
    expect.stringContaining("Documentos"),
    expect.stringContaining("Operações"),
    expect.stringContaining("Administração"),
  ]);
  expect(screen.queryByRole("button", { name: /aprovar.*lote/i })).not.toBeInTheDocument();
});

it("navega pelas áreas com setas, Home e End mantendo um único tab stop", async () => {
  const user = userEvent.setup();
  render(<StaffPortalWorkspace panels={panels()} initialArea="my-work" />);
  const mine = screen.getByRole("tab", { name: /Meu trabalho/ });
  mine.focus();

  await user.keyboard("{ArrowRight}");
  const team = screen.getByRole("tab", { name: /Filas da equipe/ });
  expect(team).toHaveFocus();
  expect(team).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel")).toHaveTextContent("Tarefas da equipe");

  await user.keyboard("{End}");
  expect(screen.getByRole("tab", { name: /Administração/ })).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveTextContent("Administração governada");

  await user.keyboard("{Home}");
  expect(screen.getByRole("tab", { name: /Visão geral/ })).toHaveFocus();
  expect(screen.getAllByRole("tab").filter((tab) => tab.tabIndex === 0)).toHaveLength(1);
});
