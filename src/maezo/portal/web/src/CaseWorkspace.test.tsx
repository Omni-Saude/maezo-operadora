import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { CaseWorkspace, ReceiptsView } from "./CaseWorkspace";
import { acceptedCommand, executedCommand, workspaceFixture } from "./test/caseExperienceFixtures";

it("apresenta dossiê, proveniência, cronologia, pedido, documento, mensagem e recibo", () => {
  render(<CaseWorkspace view={workspaceFixture} />);
  expect(screen.getByRole("heading", { name: "Solicitação de autorização" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Dossiê autorizado" })).toBeInTheDocument();
  expect(screen.getByText("Fonte: Guia informada pelo prestador")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Histórico cronológico" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Pedidos de documentos" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Documentos" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Mensagens" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Solicitações e recibos" })).toBeInTheDocument();
});

it("só oferece abertura quando a projeção autoriza o documento", async () => {
  const onDownload = vi.fn();
  render(<CaseWorkspace view={workspaceFixture} onDownloadDocument={onDownload} />);
  await userEvent.click(screen.getByRole("button", { name: "Abrir documento" }));
  expect(onDownload).toHaveBeenCalledWith("document_synthetic_1");
  expect(screen.queryByText("document_synthetic_1")).not.toBeInTheDocument();
});

it("diferencia comando recebido de execução comprovada por recibo", () => {
  render(<ReceiptsView state={{ kind: "ready" }} commands={[acceptedCommand, executedCommand]} />);
  const accepted = screen.getByText("Envio da solicitação").closest("li");
  const executed = screen.getByText("Resposta ao pedido de documentos").closest("li");
  expect(accepted).not.toBeNull();
  expect(executed).not.toBeNull();
  expect(within(accepted!).getByText(/execução ainda não foi confirmada/i)).toBeInTheDocument();
  expect(within(accepted!).queryByText("receipt-synthetic-2")).not.toBeInTheDocument();
  expect(within(executed!).getByText("receipt-synthetic-2")).toBeInTheDocument();
  expect(within(executed!).getByText("Resposta processada")).toBeInTheDocument();
});

it("apresenta ausência e indisponibilidade como estados diferentes", () => {
  const view = {
    ...workspaceFixture,
    dossier: { state: { kind: "empty" as const, message: "Nenhum dado liberado." }, sections: [] },
    chronology: {
      state: { kind: "unavailable" as const, message: "Histórico temporariamente indisponível." },
      events: [],
    },
  };
  render(<CaseWorkspace view={view} />);
  expect(screen.getByText("Nenhum dado liberado.")).toBeInTheDocument();
  expect(screen.getByText("Histórico temporariamente indisponível.")).toHaveClass("resource-error");
});
