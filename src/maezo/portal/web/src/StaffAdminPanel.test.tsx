import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { StaffAdminPanel } from "./StaffAdminPanel";
import { createStaffAdminClient } from "./staffAdminClient";

// D-40: the administration panel is READ-ONLY — one consult gesture, one GET, zero
// mutation affordance, closed errors, and session loss leaves to the session surface.

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function vinculo() {
  return {
    principal_ref: "human-internal-1",
    membership_revision: 3,
    memberships: [{ membership_ref: "review-1", roles: ["staff"], groups: ["medico-auditor"] }],
    subject_bindings: [],
    reviewed_until: "2026-10-26T12:00:00Z",
    revoked: false,
    state: "active",
  };
}

function revoked() {
  return {
    principal_ref: "human-internal-2",
    membership_revision: 1,
    memberships: [{ membership_ref: "review-2", roles: ["supervisor"], groups: [] }],
    subject_bindings: [{ kind: "vendor", resource_ref: "resource-vendor-1" }],
    reviewed_until: "2026-10-20T12:00:00Z",
    revoked: true,
    state: "revoked",
  };
}

function roster() {
  return { schema_version: 1, generated_at: "2026-10-25T12:00:00Z", count: 2, vinculos: [vinculo(), revoked()] };
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

async function consult(panel: HTMLElement) {
  await userEvent.click(within(panel).getByRole("button", { name: "Consultar vínculos" }));
}

it("não consulta nada sem o gesto do colaborador", async () => {
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={vi.fn()} />);
  expect(screen.getByRole("region", { name: "Vínculos e permissões governados" })).toBeInTheDocument();
  // Stub é mera superfície: nenhuma leitura de vínculos sai do navegador sozinha.
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});

it("consulta o roster com um GET sem corpo e renderiza os fatos revisados", async () => {
  vi.mocked(fetch).mockResolvedValue(json(roster()));
  const onSessionUnavailable = vi.fn();
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={onSessionUnavailable} />);
  const panel = screen.getByRole("region", { name: "Vínculos e permissões governados" });
  await consult(panel);
  expect(vi.mocked(fetch)).toHaveBeenCalledWith(
    "/api/v1/portal/administration/memberships",
    expect.objectContaining({ method: "GET", redirect: "error", credentials: "same-origin" }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/"body"|csrf/i);
  expect(await within(panel).findByText("human-internal-1")).toBeInTheDocument();
  expect(within(panel).getByText("human-internal-2")).toBeInTheDocument();
  expect(within(panel).getAllByText("Revogado").length).toBeGreaterThan(0);
  expect(within(panel).getByText(/resource-vendor-1/)).toBeInTheDocument();
  expect(within(panel).getByText("2 vínculos revisados.")).toBeInTheDocument();
  expect(within(panel).getByText("medico-auditor")).toBeInTheDocument();
  expect(onSessionUnavailable).not.toHaveBeenCalled();
});

it("renderiza roster vazio como fato, nunca como erro", async () => {
  vi.mocked(fetch).mockResolvedValue(
    json({ schema_version: 1, generated_at: "2026-10-25T12:00:00Z", count: 0, vinculos: [] }),
  );
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={vi.fn()} />);
  const panel = screen.getByRole("region", { name: "Vínculos e permissões governados" });
  await consult(panel);
  expect(await within(panel).findByText("Nenhum vínculo administrativo revisado neste ambiente.")).toBeInTheDocument();
});

it("oferece erro fechado com retry e nenhuma affordance de mutação", async () => {
  vi.mocked(fetch).mockResolvedValue(json({ code: "admin_read_unavailable" }, 503));
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={vi.fn()} />);
  const panel = screen.getByRole("region", { name: "Vínculos e permissões governados" });
  await consult(panel);
  expect(
    await within(panel).findByText("Não foi possível consultar os vínculos"),
  ).toBeInTheDocument();
  expect(
    within(panel).getByText("A leitura dos vínculos não está disponível neste ambiente. Nada foi exibido."),
  ).toBeInTheDocument();
  expect(within(panel).getByRole("button", { name: "Consultar novamente" })).toBeInTheDocument();

  vi.mocked(fetch).mockResolvedValue(json(roster()));
  await userEvent.click(within(panel).getByRole("button", { name: "Consultar novamente" }));
  expect(await within(panel).findByText("human-internal-1")).toBeInTheDocument();

  // A tela inteira não carrega nenhum comando administrativo: conceder, revogar, renovar,
  // re-revisar ou criar vínculo não existe aqui — só a consulta e a repetição dela.
  const buttons = within(panel).getAllByRole("button").map((button) => button.textContent);
  expect(buttons.every((text) => /Consultar/.test(text ?? ""))).toBe(true);
  expect(within(panel).queryByRole("button", { name: /conceder|revogar|renovar|revisar|criar|salvar|editar|excluir/i })).toBeNull();
  expect(within(panel).getByText(/Somente leitura\. Nenhuma alteração de vínculo/)).toBeInTheDocument();
});

it("403 fecha sem retry e sessão perdida sai para a superfície de sessão", async () => {
  const onSessionUnavailable = vi.fn();
  vi.mocked(fetch).mockResolvedValue(json({ code: "authentication_unavailable" }, 401));
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={onSessionUnavailable} />);
  const panel = screen.getByRole("region", { name: "Vínculos e permissões governados" });
  await consult(panel);
  expect(await within(panel).findByText("Sua sessão não está mais disponível.")).toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "Consultar novamente" })).toBeNull();
  expect(onSessionUnavailable).toHaveBeenCalledOnce();
});

it("resposta fora do contrato é recusada como invalid-response", async () => {
  vi.mocked(fetch).mockResolvedValue(json({ surpresa: true }));
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={vi.fn()} />);
  const panel = screen.getByRole("region", { name: "Vínculos e permissões governados" });
  await consult(panel);
  expect(
    await within(panel).findByText("O portal recusou uma resposta inesperada do serviço de administração. Nada foi exibido."),
  ).toBeInTheDocument();
});
