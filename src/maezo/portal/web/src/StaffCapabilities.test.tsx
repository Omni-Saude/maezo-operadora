import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "./App";

// Only the fetch boundary is replaced: the real session client, navigation and
// area clients run against wire-shaped bodies.

type Capability = "identity" | "staff_cases" | "human";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(capabilities: Capability[]) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/v1/portal/session") {
      return json({
        schema_version: 1,
        principal_ref: "opaque-principal",
        audience: "staff",
        roles: ["authorized-role"],
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        csrf_token: "csrf-secret",
        capabilities,
      });
    }
    if (path.startsWith("/api/v1/portal/tasks")) {
      return json({ schema: "portal-read-error.v1", code: "read_dependency_unavailable" }, 503);
    }
    return json({ code: "dependency_unavailable" }, 503);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function areaRequests(fetcher: ReturnType<typeof serve>) {
  return fetcher.mock.calls.map(([input]) => String(input)).filter((path) => path !== "/api/v1/portal/session");
}

beforeEach(() => window.history.replaceState(null, "", "/portal/"));
afterEach(() => vi.unstubAllGlobals());

it("marks areas this environment has not enabled and never calls their API", async () => {
  const fetcher = serve(["identity"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  for (const name of ["Meu trabalho", "Filas da equipe", "Casos", "Documentos"]) {
    expect(within(tabs).getByRole("tab", { name: new RegExp(`^${name}\s*Em breve`) })).toBeInTheDocument();
  }
  expect(within(tabs).getByRole("tab", { name: /^Visão geral/ })).not.toHaveTextContent("Em breve");

  for (const name of ["Meu trabalho", "Filas da equipe", "Casos", "Documentos"]) {
    await user.click(within(tabs).getByRole("tab", { name: new RegExp(`^${name}`) }));
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("Esta área ainda não está habilitada neste ambiente.");
    expect(panel).not.toHaveTextContent("temporariamente indisponível");
    expect(within(panel).queryByRole("button", { name: /Tentar novamente/ })).toBeNull();
  }
  expect(areaRequests(fetcher)).toEqual([]);
});

it("keeps a real 503 as a temporary failure when the capability is enabled", async () => {
  const fetcher = serve(["identity", "staff_cases", "human"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  expect(tabs).not.toHaveTextContent("Em breve");
  await user.click(within(tabs).getByRole("tab", { name: /^Meu trabalho/ }));
  const panel = screen.getByRole("tabpanel");
  expect(await within(panel).findByText("A fila está temporariamente indisponível.")).toBeInTheDocument();
  expect(within(panel).getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("ainda não está habilitada");
  expect(areaRequests(fetcher).some((path) => path.startsWith("/api/v1/portal/tasks"))).toBe(true);
});

it("refuses a session that names a capability outside the contract", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({
    schema_version: 1,
    principal_ref: "opaque-principal",
    audience: "staff",
    roles: [],
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    csrf_token: "csrf-secret",
    capabilities: ["identity", "phi"],
  })));
  render(<App />);
  expect(await screen.findByText("O portal recusou uma resposta inesperada. Nenhum acesso foi concedido.")).toBeInTheDocument();
  expect(screen.queryByRole("tablist", { name: "Áreas de trabalho" })).toBeNull();
});
