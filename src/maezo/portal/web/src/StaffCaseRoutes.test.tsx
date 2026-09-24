import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("./EmployeeQueues", () => ({
  EmployeeQueues: () => <section aria-label="Filas de colaboradores" />,
}));
vi.mock("./StaffOverview", () => ({
  StaffOverview: () => <section aria-label="Visão geral" />,
}));

import { readFileSync } from "node:fs";

import { App } from "./App";
import { reasonLabels, taskLabels } from "./StaffCaseWorkspace";
import {
  escalatedStaffCaseDetail,
  escalatedStaffCasePage,
  staffCaseDetail,
  staffCasePage,
  staffCaseRefs,
  wireInstant,
} from "./test/staffCaseFixtures";

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
  expect(screen.getByText(/^Atualizado às [0-9]{2}:[0-9]{2}$/)).toBeInTheDocument();
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

it("tarefa conhecida ganha rótulo PT-BR; id fora do mapa aparece cru, nunca some", async () => {
  const ref = staffCaseRefs[0];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json(staffCaseDetail(ref)) : undefined));
  render(<App />);

  const tasks = await screen.findByRole("table", { name: /tarefas ativas do caso/i });
  const [, first, second] = within(tasks).getAllByRole("row");
  expect(within(first).getByRole("rowheader")).toHaveTextContent("Análise do médico auditorUT_AnaliseMedicoAuditor");
  expect(within(second).getByRole("rowheader")).toHaveTextContent(/^UT_ConferenciaDocumental$/);
});

it("o mapa de rótulos cobre exatamente as userTasks do BPMN de autorização", () => {
  const bpmn = readFileSync("../../../../spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn", "utf8");
  const ids = [...bpmn.matchAll(/<bpmn:userTask id="([^"]+)"/g)].map((m) => m[1]);
  expect(Object.keys(taskLabels).sort()).toEqual([...new Set(ids)].sort());
});

it("páginas internas usam o cabeçalho compacto; a casa mantém o hero", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage())));
  const view = render(<App />);
  await screen.findByRole("table", { name: /casos de autorização liberados/i });
  expect(screen.getByRole("heading", { level: 1 }).closest("section")).toHaveClass("portal-session-compact");
  expect(screen.queryByText(/cada recurso é consultado/i)).not.toBeInTheDocument();
  expect(screen.getByText("Validade desta sessão")).toBeInTheDocument();
  view.unmount();

  window.history.replaceState(null, "", "/portal/");
  render(<App />);
  expect(await screen.findByText(/cada recurso é consultado/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1 }).closest("section")).not.toHaveClass("portal-session-compact");
});

it("com escalonamento: colunas Prioridade/Prazo/Motivo/Guia, ordem por prazo, sem prazo no fim", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(escalatedStaffCasePage())));
  render(<App />);

  const table = await screen.findByRole("table", { name: /casos de autorização liberados/i });
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "Caso", "Prioridade", "Prazo", "Motivo", "Guia", "Situação", "Situação observada em", "Revisão",
  ]);
  const [, first, second, third] = within(table).getAllByRole("row");
  expect(within(first).getByRole("rowheader")).toHaveTextContent(staffCaseRefs[1]);
  expect(first).toHaveTextContent("P1");
  expect(first).toHaveTextContent(/Vencido há 20 min/);
  expect(first).toHaveTextContent("Sinal de alerta clínico");
  expect(first).toHaveTextContent("***7731");
  expect(second).toHaveTextContent(/Vence em 3 h/);
  expect(second).toHaveTextContent("Pediu atendimento humano");
  expect(within(third).getByRole("rowheader")).toHaveTextContent(staffCaseRefs[2]);
  expect(third).toHaveTextContent("Sem prazo definido");
  expect(table).not.toHaveTextContent("20260918007731");
});

it("motivo fora do mapa da DMN aparece cru; mapa bate com a DMN", async () => {
  const bodies = escalatedStaffCasePage();
  bodies.items[0].escalation = { ...bodies.items[0].escalation!, reason_code: "motivo_novo_engine" };
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(bodies)));
  render(<App />);
  expect(await screen.findByText("motivo_novo_engine")).toBeInTheDocument();

  const dmn = readFileSync("../../../../spec/processes/dmn/escalation_routing.dmn", "utf8");
  const [inputs] = dmn.split("<rule");
  expect(inputs).toContain("motivo_categoria");
  const codes = [...dmn.matchAll(/<rule[\s\S]*?<inputEntry[^>]*><text>"([^"]+)"<\/text>/g)].map((m) => m[1]);
  expect(Object.keys(reasonLabels).sort()).toEqual([...new Set(codes)].sort());
});

it("engine antigo (sem escalation) esconde as colunas sem erro", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage())));
  render(<App />);
  const table = await screen.findByRole("table", { name: /casos de autorização liberados/i });
  expect(within(table).queryByRole("columnheader", { name: "Prioridade" })).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("detalhe mostra guia inteira, motivo, prioridade e os dois prazos", async () => {
  const ref = staffCaseRefs[1];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json(escalatedStaffCaseDetail(ref)) : undefined));
  render(<App />);

  const section = await screen.findByRole("region", { name: "Escalonamento" });
  expect(section).toHaveTextContent("20260918007731");
  expect(section).toHaveTextContent("Sinal de alerta clínico");
  expect(section).toHaveTextContent("P1");
  expect(within(section).getByText("Prazo para assumir").nextSibling).toHaveTextContent(/Vencido há 50 min/);
  expect(within(section).getByText("Prazo para resolver").nextSibling).toHaveTextContent(/Vencido há 20 min/);
});

it("detalhe com escalation_state=unresolved diz sem prazo definido", async () => {
  const ref = staffCaseRefs[2];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json(escalatedStaffCaseDetail(ref)) : undefined));
  render(<App />);
  const section = await screen.findByRole("region", { name: "Escalonamento" });
  expect(within(section).getAllByText("Sem prazo definido")).toHaveLength(2);
  expect(section).toHaveTextContent("20260920002209");
});

it("detalhe sem escalation (engine antigo) não mostra a seção", async () => {
  const ref = staffCaseRefs[0];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json(staffCaseDetail(ref)) : undefined));
  render(<App />);
  await screen.findByRole("heading", { name: "Caso de autorização" });
  expect(screen.queryByRole("region", { name: "Escalonamento" })).not.toBeInTheDocument();
});

it("urgência: vencidos primeiro, depois prioridade, depois prazo; sem prazo no fim; ordem explícita", async () => {
  const now = Date.now();
  const refs = ["aut_ord_a_0000000001", "aut_ord_b_0000000002", "aut_ord_c_0000000003", "aut_ord_d_0000000004", "aut_ord_e_0000000005"];
  const base = staffCasePage(now, refs);
  const esc = (priority: string | null, dueOffset: number | null) => ({
    escalation_state: priority === null ? "unresolved" as const : "resolved" as const,
    guide_number: "***1234",
    reason_code: priority === null ? null : "falha_tecnica",
    priority,
    ack_due_at: dueOffset === null ? null : wireInstant(now + dueOffset - 60_000),
    resolution_due_at: dueOffset === null ? null : wireInstant(now + dueOffset),
  });
  const plan = [esc("P2", -600_000), esc("P1", -300_000), esc("P1", 3_600_000), esc("P3", 1_800_000), esc(null, null)];
  const page = { ...base, items: base.items.map((item, i) => ({ ...item, escalation: plan[i] })) };
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(page)));
  render(<App />);

  const table = await screen.findByRole("table", { name: /casos de autorização liberados/i });
  const order = within(table).getAllByRole("rowheader").map((cell) => cell.textContent?.replace("Abrir caso ", ""));
  expect(order).toEqual([refs[1], refs[0], refs[2], refs[3], refs[4]]);
  expect(screen.getByText(/ordenado por urgência/i)).toBeInTheDocument();
  const overdue = within(table).getAllByText(/Vencido há/);
  expect(overdue).toHaveLength(2);
  overdue.forEach((node) => expect(node.querySelector("svg")).not.toBeNull());
});

it("mobile: cartões com prioridade e prazo na 1ª linha, dt/dd, ID truncado e link real para o caso", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width"), media: query,
    addEventListener: () => undefined, removeEventListener: () => undefined,
  }));
  window.history.replaceState(null, "", "/portal/cases");
  const ref = staffCaseRefs[1];
  serve((path) => {
    if (path.startsWith("/api/v1/portal/cases?")) return json(escalatedStaffCasePage());
    if (path === `/api/v1/portal/cases/${ref}`) return json(escalatedStaffCaseDetail(ref));
    return undefined;
  });
  render(<App />);

  const list = await screen.findByRole("list", { name: "Casos do seu grupo" });
  expect(screen.queryByRole("table", { name: /casos de autorização liberados/i })).not.toBeInTheDocument();
  const [first] = within(list).getAllByRole("listitem");
  expect(first.querySelector(".case-card-lead")).toHaveTextContent(/^P1 · Vencido há 20 min$/);
  expect(within(first).getByText("Motivo").tagName).toBe("DT");
  expect(within(first).getByText("***7731").tagName).toBe("DD");
  expect(within(first).getByText("aut_2026…02nzpa")).toHaveAttribute("aria-hidden", "true");
  await userEvent.click(within(first).getByRole("link", { name: `Abrir caso ${ref}` }));
  expect(window.location.pathname).toBe(`/portal/cases/${ref}`);
});

it("detalhe: Copiar guia manda a guia inteira à área de transferência e confirma, sem logar", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const log = vi.spyOn(console, "log");
  const ref = staffCaseRefs[1];
  window.history.replaceState(null, "", `/portal/cases/${ref}`);
  serve((path) => (path === `/api/v1/portal/cases/${ref}` ? json(escalatedStaffCaseDetail(ref)) : undefined));
  render(<App />);

  await userEvent.click(await screen.findByRole("button", { name: "Copiar guia" }));
  expect(writeText).toHaveBeenCalledWith("20260918007731");
  expect(await screen.findByText("Guia copiada")).toBeInTheDocument();
  expect(log).not.toHaveBeenCalled();
});

it("menu mobile e link de pular para o conteúdo existem e controlam a navegação", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage())));
  render(<App />);
  await screen.findByRole("table", { name: /casos de autorização liberados/i });

  expect(screen.getByRole("link", { name: "Pular para o conteúdo" })).toHaveAttribute("href", "#conteudo");
  expect(document.getElementById("conteudo")).toHaveAttribute("tabindex", "-1");
  const toggle = screen.getByRole("button", { name: /^Menu/ });
  expect(toggle).toHaveTextContent("Casos");
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  await userEvent.click(screen.getByRole("tab", { name: /Meu trabalho/ }));
  expect(toggle).toHaveAttribute("aria-expanded", "false");
});

it("trocar de área sai do endereço de casos", async () => {
  window.history.replaceState(null, "", "/portal/cases");
  serve(casesList(() => json(staffCasePage())));
  render(<App />);
  await screen.findByRole("table", { name: /casos de autorização liberados/i });

  await userEvent.click(screen.getByRole("tab", { name: /Meu trabalho/ }));
  expect(window.location.pathname).toBe("/portal/");
});
