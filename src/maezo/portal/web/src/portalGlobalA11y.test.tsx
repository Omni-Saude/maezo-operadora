import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// R-089 (forensic audit, onda 6): portal-wide WCAG 2.2 AA items.
// F-104 (2.4.2) title per area · F-156 (4.1.2) aria-controls resolves · F-161 (3.1.2) lang on
// English terms · F-155 download name carries an extension · F-158 admin headings are legible ·
// F-159 (3.2.6) one consistent help mechanism.

import { App } from "./App";
import { downloadFileName } from "./documentFileName";
import { EmployeeQueues } from "./EmployeeQueues";
import { StaffAdminPanel } from "./StaffAdminPanel";
import { createStaffAdminClient } from "./staffAdminClient";
import { taskReadInputs } from "./taskReadBindings";

type Audience = "staff" | "beneficiary" | "provider";

function session(audience: Audience) {
  return {
    schema_version: 1,
    principal_ref: "opaque-principal",
    audience,
    roles: ["authorized-role"],
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    csrf_token: "csrf-secret",
    capabilities: ["identity", "staff_cases", "human"],
    flags: [],
  } as const;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function emptyCasePage(audience: Exclude<Audience, "staff">) {
  return {
    schema: "portal-external-case-page.v1",
    audience,
    items: [],
    next_cursor: null,
    freshness: { observed_at: "2099-09-10T12:00:00.000000Z", valid_until: "2099-09-10T13:00:00.000000Z" },
  } as const;
}

function respond(audience: Audience) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith("/session")) return json(session(audience));
    if (url.includes("/cases") && audience !== "staff") return json(emptyCasePage(audience));
    return json({ code: "dependency_unavailable" }, 503);
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  window.history.replaceState(null, "", "/portal/");
  document.title = "Portal Maezo";
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("F-104: o título do documento descreve o estado e a área ativa (WCAG 2.4.2)", async () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => undefined));
  const view = render(<App />);
  expect(document.title).toBe("Validando sua sessão · Portal Maezo");
  view.unmount();

  respond("staff");
  render(<App />);
  await screen.findByRole("heading", { level: 1, name: "Área de colaboradores" });
  await waitFor(() => expect(document.title).toBe("Visão geral · Área de colaboradores · Portal Maezo"));
  await userEvent.click(screen.getByRole("tab", { name: /Casos/ }));
  expect(document.title).toBe("Casos · Área de colaboradores · Portal Maezo");
  await userEvent.click(screen.getByRole("tab", { name: /Administração/ }));
  expect(document.title).toBe("Administração · Área de colaboradores · Portal Maezo");
});

it("F-104: a área externa nomeia a aba ativa no título", async () => {
  respond("beneficiary");
  render(<App />);
  await screen.findByRole("heading", { level: 1, name: "Área do beneficiário" });
  await waitFor(() => expect(document.title).toBe("Solicitações · Área do beneficiário · Portal Maezo"));
  await userEvent.click(screen.getByRole("tab", { name: "Mensagens" }));
  expect(document.title).toBe("Mensagens · Área do beneficiário · Portal Maezo");
});

it("F-156: todo aria-controls das abas staff aponta para um painel existente (WCAG 4.1.2)", async () => {
  respond("staff");
  render(<App />);
  await screen.findByRole("heading", { level: 1, name: "Área de colaboradores" });
  const tabs = within(screen.getByRole("tablist", { name: "Áreas de trabalho" })).getAllByRole("tab");
  expect(tabs).toHaveLength(7);
  for (const tab of tabs) {
    const controls = tab.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    const panel = document.getElementById(controls!);
    expect(panel, `aria-controls=${controls}`).not.toBeNull();
    expect(panel).toHaveAttribute("role", "tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", tab.id);
  }
  // Only the active panel is exposed; the others stay hidden and empty.
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  await userEvent.click(screen.getByRole("tab", { name: /Operações/ }));
  expect(screen.getByRole("tabpanel")).toHaveAttribute("id", "area-operations");
  expect(document.getElementById("area-overview")).toBeEmptyDOMElement();
});

it("F-161: termos em inglês exibidos ao colaborador carregam lang=\"en\" (WCAG 3.1.2)", async () => {
  respond("staff");
  render(<App />);
  await screen.findByRole("heading", { level: 1, name: "Área de colaboradores" });
  await userEvent.click(screen.getByRole("tab", { name: /Meu trabalho/ }));
  const eyebrow = await screen.findByText("Workspace");
  expect(eyebrow.closest("[lang]")).toHaveAttribute("lang", "en");
  expect(eyebrow.closest(".eyebrow")).toHaveTextContent("Workspace operacional");
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function taskFreshness() {
  return {
    state: "current",
    observed_at: "2099-09-10T12:00:01Z",
    source_observed_at: "2099-09-10T12:00:00Z",
    valid_until: "2099-09-10T13:00:00Z",
    refresh_after_seconds: 10,
  } as const;
}

function oneItemQueue() {
  return {
    schema: "portal-task-queue.v1",
    queue: "mine",
    items: [{
      task_id: "task-1",
      process_definition_key: "SP-OP-AUTH-001",
      task_definition_key: "UT_AnaliseMedicoAuditor",
      task_revision: "1",
      ownership: "self",
      engine_due_at: null,
      snapshot_at: "2099-09-10T12:00:00Z",
    }],
    next_cursor: null,
    freshness: taskFreshness(),
  };
}

function taskSnapshot() {
  const digest = "b".repeat(64);
  return {
    schema: "portal-task-read.v1",
    task: {
      schema_version: 1,
      snapshot_at: "2099-09-10T12:00:00Z",
      task_id: "task-1",
      process_definition_key: "SP-OP-AUTH-001",
      process_definition_version: "1",
      process_definition_id: "AUTH:opaque",
      process_definition_digest: digest,
      task_definition_key: "UT_AnaliseMedicoAuditor",
      form_key: "auth_decisao",
      form_version: "1",
      form_digest: digest,
      form_source_status: "BPMN_FORMDATA",
      task_revision: "1",
      assignee_ref: null,
      eligible_candidate_groups: ["medico-auditor"],
      evidence_revision: "1",
      evidence_digest: digest,
      engine_due_at: null,
      allowed_actions: [],
      allowed_inputs: [...taskReadInputs.auth_decisao],
      read_only_evidence: null,
    },
    freshness: taskFreshness(),
  };
}

it("F-161: \"Snapshot\" no detalhe da tarefa carrega lang=\"en\" ao carregar e ao exibir (WCAG 3.1.2)", async () => {
  const detail = deferred<Response>();
  vi.mocked(fetch)
    .mockResolvedValueOnce(json(oneItemQueue()))
    .mockReturnValueOnce(detail.promise);
  render(<EmployeeQueues sessionBinding="session-a" onSessionUnavailable={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Ver detalhes: Analise do medico auditor" }));

  // Loading state: "Consultando o snapshot autorizado da tarefa."
  await screen.findByRole("heading", { name: "Carregando detalhes" });
  const loadingLine = screen.getByText(/autorizado da tarefa/);
  expect(loadingLine).toHaveTextContent("Consultando o snapshot autorizado da tarefa.");
  expect(loadingLine.querySelector('[lang="en"]')).toHaveTextContent(/^snapshot$/);
  for (const term of screen.getAllByText(/^snapshot$/i)) {
    expect(term.closest("[lang]")).toHaveAttribute("lang", "en");
  }

  // Ready state: eyebrow "Snapshot somente leitura".
  detail.resolve(json(taskSnapshot()));
  const region = await screen.findByRole("region", { name: "Analise do medico auditor" });
  const eyebrow = within(region).getByText(/somente leitura$/);
  expect(eyebrow).toHaveTextContent("Snapshot somente leitura");
  expect(eyebrow.querySelector('[lang="en"]')).toHaveTextContent(/^Snapshot$/);
  expect(screen.getByText("Snapshot").closest("[lang]")).toHaveAttribute("lang", "en");
  // "Prazo do engine": the English term "engine" is wrapped too (WCAG 3.1.2).
  const engineTerm = within(region).getByText("engine");
  expect(engineTerm.closest("[lang]")).toHaveAttribute("lang", "en");
  expect(engineTerm.closest("dt")).toHaveTextContent("Prazo do engine");
  // No visible "snapshot" text anywhere escapes a lang="en" wrapper.
  for (const term of screen.getAllByText(/^snapshot$/i)) {
    expect(term.closest("[lang]")).toHaveAttribute("lang", "en");
  }
});

it("F-159: um mecanismo de ajuda aparece no mesmo lugar em todo estado do portal (WCAG 3.2.6)", async () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => undefined));
  const loading = render(<App />);
  const loadingHelp = screen.getByRole("contentinfo");
  expect(within(loadingHelp).getByRole("heading", { name: "Ajuda" })).toBeInTheDocument();
  expect(loading.container.lastElementChild).toBe(loadingHelp);
  loading.unmount();

  for (const audience of ["staff", "beneficiary", "provider"] as const) {
    respond(audience);
    const view = render(<App />);
    await screen.findByRole("heading", { level: 1 });
    const help = screen.getByRole("contentinfo");
    expect(within(help).getByRole("heading", { name: "Ajuda" })).toBeInTheDocument();
    expect(help).toHaveTextContent(audience === "staff" ? /coordenação da sua equipe/ : /canais oficiais/);
    // Same relative order: the help region closes the page, after the main content.
    expect(view.container.querySelector(".app-shell")?.lastElementChild).toBe(help);
    view.unmount();
  }
});

it("F-155: o nome do download carrega uma extensão coerente com o conteúdo", async () => {
  const pdf = new Blob(["%PDF-1.7\nsynthetic"], { type: "application/octet-stream" });
  const png = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0])]);
  const jpeg = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0])]);
  const unknown = new Blob(["synthetic bytes"]);
  expect(await downloadFileName(pdf)).toBe("documento.pdf");
  expect(await downloadFileName(png)).toBe("documento.png");
  expect(await downloadFileName(jpeg)).toBe("documento.jpg");
  expect(await downloadFileName(unknown)).toBe("documento.bin");
  for (const name of await Promise.all([pdf, png, jpeg, unknown].map(downloadFileName))) {
    expect(name).toMatch(/^documento\.[a-z]+$/);
  }
});

it("F-158: o título de cada vínculo é legível e o identificador fica rotulado e mascarado", async () => {
  vi.mocked(fetch).mockResolvedValue(json({
    schema_version: 1,
    generated_at: "2026-10-25T12:00:00Z",
    count: 1,
    vinculos: [{
      principal_ref: "human-internal-1",
      membership_revision: 3,
      memberships: [{ membership_ref: "review-1", roles: ["staff"], groups: [] }],
      subject_bindings: [],
      reviewed_until: "2026-10-26T12:00:00Z",
      revoked: false,
      state: "active",
    }],
  }));
  render(<StaffAdminPanel client={createStaffAdminClient()} onSessionUnavailable={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Consultar vínculos" }));
  const heading = await screen.findByRole("heading", { level: 3, name: "Vínculo 1 de 1: Ativo" });
  expect(heading).not.toHaveTextContent("human-internal-1");
  const region = screen.getByRole("region", { name: "Vínculo 1 de 1: Ativo" });
  const term = within(region).getByText("Referência do colaborador");
  expect(term.tagName).toBe("DT");
  // DPO 31/10/2026 (#1082, opção b): só os 4 últimos caracteres; o valor inteiro nunca aparece.
  expect(term.nextElementSibling).toHaveTextContent("••••terminada em al-1");
  expect(region).not.toHaveTextContent("human-internal-1");
});
