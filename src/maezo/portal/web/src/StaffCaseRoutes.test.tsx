import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("./EmployeeQueues", () => ({
  EmployeeQueues: () => <section aria-label="Filas de colaboradores" />,
}));
vi.mock("./StaffOverview", () => ({
  StaffOverview: () => <section aria-label="Visão geral" />,
}));

import { App } from "./App";
import { staffCaseDetail, staffCasePage, staffCaseRefs } from "./test/staffCaseFixtures";

// Only the fetch boundary is replaced: the real session client, staff case
// client, validators and router run against wire-shaped bodies.

function session() {
  return {
    schema_version: 1,
    principal_ref: "opaque-principal",
    audience: "staff",
    roles: ["authorized-role"],
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    csrf_token: "csrf-secret",
  } as const;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Route = (path: string) => Response | undefined;

function serve(route: Route) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/v1/portal/session") return json(session());
    return route(path) ?? json({ code: "dependency_unavailable" }, 503);
  }));
}

function casesList(body: () => Response): Route {
  return (path) => (path.startsWith("/api/v1/portal/cases?") ? body() : undefined);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

it("abre /portal/cases com a fila do grupo e só os campos que o contrato traz", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage())));
  render(<App />);

  const table = await screen.findByRole("table", { name: /casos de autorização liberados/i });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(1 + staffCaseRefs.length);
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "Caso", "Situação", "Situação observada em", "Revisão",
  ]);
  expect(within(rows[1]).getByText("Em andamento")).toBeInTheDocument();
  expect(within(rows[3]).getByText("Encerrado")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /^Casos/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(/válida por mais \d+ s/i)).toBeInTheDocument();
  expect(screen.queryByText(/prioridade|SLA|motivo/i)).not.toBeInTheDocument();
  const [path, init] = vi.mocked(fetch).mock.calls.find(([p]) => String(p).startsWith("/api/v1/portal/cases?"))!;
  expect(path).toBe("/api/v1/portal/cases?kind=authorization&limit=25");
  expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
});

it("200 vazio diz que não há caso para o grupo, sem tabela", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage(Date.now(), []))));
  render(<App />);

  expect(await screen.findByText("Nenhum caso para o seu grupo.")).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("403 explica a falta de permissão e não mostra lista", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json({ code: "operation_forbidden" }, 403)));
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(/não tem permissão para ver os casos do seu grupo/i);
  expect(screen.getByRole("heading", { name: "Não foi possível carregar os casos" })).toHaveFocus();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("503 (serviço fora do ar ou capacidade staff desligada) é honesto e sem dado simulado", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json({ code: "dependency_unavailable" }, 503)));
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Serviço de casos indisponível" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(/fora do ar ou não habilitado neste ambiente/i);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText(/nenhum caso para o seu grupo/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
});

it("abre o detalhe pela linha, escreve /portal/cases/:ref e volta pelo histórico", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  const ref = staffCaseRefs[0];
  serve((path) => {
    if (path.startsWith("/api/v1/portal/cases?")) return json(staffCasePage());
    if (path === `/api/v1/portal/cases/${ref}`) return json(staffCaseDetail(ref));
    return undefined;
  });
  render(<App />);

  await userEvent.click(await screen.findByRole("link", { name: new RegExp(ref) }));
  expect(window.location.pathname).toBe(`/portal/cases/${ref}`);
  expect(await screen.findByRole("heading", { name: "Caso de autorização" })).toHaveFocus();
  const tasks = screen.getByRole("table", { name: /tarefas ativas do caso/i });
  expect(within(tasks).getByText("UT_AnaliseMedicoAuditor")).toBeInTheDocument();
  expect(within(tasks).getByText(/vencido/i)).toBeInTheDocument();
  expect(within(tasks).getByText("Sem responsável")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /decidir|atribuir|concluir/i })).not.toBeInTheDocument();

  window.history.back();
  expect(await screen.findByRole("table", { name: /casos de autorização liberados/i })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/portal/cases");
});

it("entrada direta em /portal/cases/:ref lê só aquele caso", async () => {
  const ref = staffCaseRefs[1];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json(staffCaseDetail(ref)) : undefined));
  render(<App />);

  expect(await screen.findByText(ref)).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([p]) => String(p).startsWith("/api/v1/portal/cases?"))).toBe(false);
});

it("detalhe com 404 diz que o caso não está liberado para o grupo", async () => {
  const ref = staffCaseRefs[2];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json({ code: "resource_unavailable" }, 404) : undefined));
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(/não está liberado para o seu grupo/i);
});

it("trocar de área sai do endereço de casos", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage())));
  render(<App />);
  await screen.findByRole("table", { name: /casos de autorização liberados/i });

  await userEvent.click(screen.getByRole("tab", { name: /Meu trabalho/ }));
  expect(window.location.pathname).toBe("/portal/");
});
