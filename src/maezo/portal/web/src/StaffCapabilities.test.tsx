import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "./App";

// Only the fetch boundary is replaced: the real session client, navigation and
// area clients run against wire-shaped bodies.

type Capability = "identity" | "staff_cases" | "human" | "staff_admin_read";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const taskRef = "task_escalation_000000000001";
const commandRef = "command_000000000001";
const digest = "c".repeat(64);

function operationsBasis() {
  return {
    schema_version: "portal-assignment-context.v2",
    task_id: taskRef,
    process_definition_key: "process_escalation_v3",
    process_definition_version: "3",
    process_definition_id: "process_escalation_v3:12:0001",
    process_definition_digest: digest,
    task_definition_key: "UT_TratarEscalonamento",
    form_key: "escalation",
    form_version: "4",
    form_digest: digest,
    expected_task_revision: "5",
    expected_evidence_revision: "9",
    expected_evidence_digest: digest,
    expected_membership_revision: "3",
    expected_authority_revision: "11",
    assignee_ref: "opaque_assignee_000000001",
    allowed_operations: ["claim", "release"],
    valid_until: new Date(Date.now() + 60_000).toISOString(),
    binding_ref: "binding_000000000000001",
    binding_version: "3",
    binding_digest: digest,
    policy_ref: "policy_00000000000001",
    policy_version: "2",
    policy_digest: digest,
    source_revision: "7",
    generation_digest: digest,
  };
}

function operationsDependencies() {
  return {
    schema: "portal-task-context.v1",
    task_id: taskRef,
    etapa: "atendimento",
    motivo_categoria: "autorizacao",
    severidade: "alta",
    prioridade: "P1",
    grupo_atendimento: "grupo-atendimento-1",
    aberto_em: "2026-10-24T12:00:00Z",
    ack_vence_em: "2026-10-24T13:00:00Z",
    resolucao_vence_em: "2026-10-24T15:00:00Z",
    resumo_contexto: "Resumo da colega que abriu o caso.",
    observed_at: new Date(Date.now() + 1_000).toISOString(),
  };
}

function operationsReceipt() {
  return {
    schema_version: "human-public-assignment-receipt.v1",
    tenant: "test-tenant",
    task_id: taskRef,
    command_id: commandRef,
    status: "pending",
    operation: "claim",
    principal_ref: "opaque-principal",
    payload_digest: digest,
    workload_ref: "workload_0000000000001",
    audit_intent_ref: "audit_intent_00000001",
    audit_intent_hash: digest,
    audit_result_ref: null,
    engine_receipt_ref: null,
    engine_recorded_at: null,
    consumed_task_revision: null,
    resulting_task_revision: null,
    technical_code: null,
    command_schema: "human-assignment.v2",
    binding_ref: "binding_000000000000001",
    binding_version: "3",
    binding_digest: digest,
    policy_ref: "policy_00000000000001",
    policy_version: "2",
    policy_digest: digest,
    source_revision: "7",
    generation_digest: digest,
    target_ref: null,
    target_membership_revision: null,
    prior_assignee_ref: null,
    resulting_assignee_ref: null,
    assignment_disposition: null,
  };
}

function serve(capabilities: Capability[], flags: readonly string[] = []) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
        flags: [...flags],
      });
    }
    if (path.startsWith("/api/v1/portal/cases/") && path.endsWith("/documents")) {
      return json({ case_ref: path.split("/")[5], documents: [] });
    }
    if (path.startsWith("/api/v1/portal/cases/") && path.endsWith("/document-requests")) {
      return json({ case_ref: path.split("/")[5], requests: [] });
    }
    if (path.startsWith("/api/v1/portal/tasks/") && path.endsWith("/assignment-context")) {
      return json(operationsBasis());
    }
    if (path.startsWith("/api/v1/portal/tasks/") && path.endsWith("/context")) {
      return json(operationsDependencies());
    }
    if (path.startsWith("/api/v1/portal/commands/") && path.includes("/receipt")) {
      return json(operationsReceipt());
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

// As leituras do painel de operações, isoladas das leituras de fila da visão geral.
function operationsRequests(fetcher: ReturnType<typeof serve>) {
  return areaRequests(fetcher).filter(
    (path) => path.endsWith("/assignment-context") || path.endsWith("/context") || path.includes("/commands/"),
  );
}

beforeEach(() => window.history.replaceState(null, "", "/portal/"));
afterEach(() => vi.unstubAllGlobals());

it("marks areas this environment has not enabled and never calls their API", async () => {
  const fetcher = serve(["identity"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  for (const name of ["Meu trabalho", "Filas da equipe", "Casos", "Documentos", "Administração"]) {
    expect(within(tabs).getByRole("tab", { name: new RegExp(`^${name}\s*Em breve`) })).toBeInTheDocument();
  }
  expect(within(tabs).getByRole("tab", { name: /^Visão geral/ })).not.toHaveTextContent("Em breve");

  for (const name of ["Meu trabalho", "Filas da equipe", "Casos", "Documentos", "Administração"]) {
    await user.click(within(tabs).getByRole("tab", { name: new RegExp(`^${name}`) }));
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("Esta área ainda não está habilitada neste ambiente.");
    expect(panel).not.toHaveTextContent("temporariamente indisponível");
    expect(within(panel).queryByRole("button", { name: /Tentar novamente/ })).toBeNull();
  }
  expect(areaRequests(fetcher)).toEqual([]);
});

it("keeps a real 503 as a temporary failure when the capability is enabled", async () => {
  // D-40: with `staff_admin_read` named too, NO staff area carries the "Em breve" marker —
  // administration is a gated area exactly like the others now.
  const fetcher = serve(["identity", "staff_cases", "human", "staff_admin_read"]);
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

it("mantém o stub enviado na área Documentos enquanto o sinal shadow está desligado", async () => {
  // Sem `human`: a visão geral não monta as filas, então a única superfície em prova é o gate.
  const fetcher = serve(["identity", "staff_cases"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  await user.click(within(tabs).getByRole("tab", { name: /^Documentos/ }));
  const panel = screen.getByRole("tabpanel");
  expect(
    within(panel).getByText(
      "A consulta e o envio de documentos ainda não estão disponíveis nesta versão do portal.",
    ),
  ).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("Documentos do caso");
  // Stub é mera superfície: nenhuma consulta de documentos sai do navegador.
  expect(areaRequests(fetcher)).toEqual([]);
});

it("renderiza o painel read-only quando o ambiente liga o sinal shadow", async () => {
  // Sem `human`: as filas não montam e a prova fica só sobre o gate e o painel.
  const fetcher = serve(["identity", "staff_cases"], ["staff_documents_panel"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  // `staff_cases` presente: Documentos não leva o marcador "Em breve" (as áreas `human`, sim).
  expect(within(tabs).getByRole("tab", { name: /^Documentos/ })).not.toHaveTextContent("Em breve");
  await user.click(within(tabs).getByRole("tab", { name: /^Documentos/ }));
  const panel = screen.getByRole("tabpanel");
  expect(
    await within(panel).findByRole("heading", { name: "Documentos do caso" }),
  ).toBeInTheDocument();
  expect(panel).not.toHaveTextContent(
    "A consulta e o envio de documentos ainda não estão disponíveis",
  );
  // Nada é consultado sem o gesto do colaborador: o painel não dispara leitura sozinho.
  expect(areaRequests(fetcher)).toEqual([]);

  await user.type(
    within(panel).getByLabelText("Consultar pela referência exata do caso"),
    "case_staff_abcdefghijklmnop",
  );
  await user.click(within(panel).getByRole("button", { name: "Consultar documentos" }));
  expect(areaRequests(fetcher).sort()).toEqual([
    "/api/v1/portal/cases/case_staff_abcdefghijklmnop/document-requests",
    "/api/v1/portal/cases/case_staff_abcdefghijklmnop/documents",
  ]);
  for (const [path, options] of fetcher.mock.calls) {
    // A leitura de sessão tem o seu próprio contrato de fetch; aqui só as rotas do painel.
    if (!String(path).startsWith("/api/v1/portal/cases/case_staff_abcdefghijklmnop/")) continue;
    expect(options).toMatchObject({ method: "GET", redirect: "error" });
    expect(JSON.stringify(options)).not.toMatch(/"body"|csrf/i);
  }
  expect(
    await within(panel).findByText("Nenhum documento verificado neste caso."),
  ).toBeInTheDocument();
  expect(
    within(panel).getByText("Nenhum pedido de documento para este caso."),
  ).toBeInTheDocument();
  expect(
    within(panel).getByText(
      "Somente leitura. O envio de documentos ainda não está disponível nesta versão do portal.",
    ),
  ).toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: /enviar|upload|responder/i })).toBeNull();
});

it("não renderiza o painel com o sinal ligado quando a capability falta", async () => {
  serve(["identity"], ["staff_documents_panel"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  expect(within(tabs).getByRole("tab", { name: /^Documentos\s*Em breve/ })).toBeInTheDocument();
  await user.click(within(tabs).getByRole("tab", { name: /^Documentos/ }));
  const panel = screen.getByRole("tabpanel");
  expect(panel).toHaveTextContent("Esta área ainda não está habilitada neste ambiente.");
  expect(panel).not.toHaveTextContent("Documentos do caso");
});

it("mantém o stub enviado na área Operações enquanto o sinal shadow está desligado", async () => {
  // Com `human`: as filas montam na visão geral, então a prova de ausência é sobre as
  // leituras do painel, isoladas das leituras de fila.
  const fetcher = serve(["identity", "human"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  await user.click(within(tabs).getByRole("tab", { name: /^Operações/ }));
  const panel = screen.getByRole("tabpanel");
  expect(within(panel).getByRole("heading", { name: "Comandos por tarefa" })).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("Operações da tarefa");
  // Stub é mera superfície: nenhuma consulta de operações sai do navegador.
  expect(operationsRequests(fetcher)).toEqual([]);
});

it("renderiza o painel read-only de operações quando o ambiente liga o sinal shadow", async () => {
  const fetcher = serve(["identity", "human"], ["staff_operations_panel"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  // `human` presente: Operações não leva o marcador "Em breve".
  expect(within(tabs).getByRole("tab", { name: /^Operações/ })).not.toHaveTextContent("Em breve");
  await user.click(within(tabs).getByRole("tab", { name: /^Operações/ }));
  const panel = screen.getByRole("tabpanel");
  expect(
    await within(panel).findByRole("heading", { name: "Operações da tarefa" }),
  ).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("Comandos por tarefa");
  // Nada é consultado sem o gesto do colaborador: o painel não dispara leitura sozinho.
  expect(operationsRequests(fetcher)).toEqual([]);

  await user.type(
    within(panel).getByLabelText("Consultar pela referência exata da tarefa"),
    taskRef,
  );
  await user.type(within(panel).getByLabelText("Protocolo do comando (opcional)"), commandRef);
  await user.click(within(panel).getByRole("button", { name: "Consultar operações" }));
  expect(operationsRequests(fetcher).sort()).toEqual([
    `/api/v1/portal/commands/${commandRef}/receipt?task_id=${taskRef}`,
    `/api/v1/portal/tasks/${taskRef}/assignment-context`,
    `/api/v1/portal/tasks/${taskRef}/context`,
  ]);
  for (const [path, options] of fetcher.mock.calls) {
    // A leitura de sessão e as filas têm seus próprios contratos; aqui só as rotas do painel.
    if (!String(path).includes(taskRef) && !String(path).includes(commandRef)) continue;
    expect(options).toMatchObject({ method: "GET", redirect: "error" });
    expect(JSON.stringify(options)).not.toMatch(/"body"|csrf/i);
  }
  expect(
    await within(panel).findByText("opaque_assignee_000000001"),
  ).toBeInTheDocument();
  expect(within(panel).getByText("P1")).toBeInTheDocument();
  expect(
    within(panel).getByRole("table", { name: "Recibo público do comando consultado" }),
  ).toHaveTextContent(commandRef);
  expect(
    within(panel).getByText(
      "Somente leitura. Nenhum comando é enviado por esta tela; os comandos continuam em Meu trabalho e Filas da equipe.",
    ),
  ).toBeInTheDocument();
  expect(
    within(panel).queryByRole("button", { name: /assumir|liberar|reatribuir|concluir|decidir|enviar|cancelar/i }),
  ).toBeNull();
});

it("não renderiza o painel de operações com o sinal ligado quando a capability falta", async () => {
  const fetcher = serve(["identity"], ["staff_operations_panel"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  expect(within(tabs).getByRole("tab", { name: /^Operações\s*Em breve/ })).toBeInTheDocument();
  await user.click(within(tabs).getByRole("tab", { name: /^Operações/ }));
  const panel = screen.getByRole("tabpanel");
  expect(panel).toHaveTextContent("Esta área ainda não está habilitada neste ambiente.");
  expect(panel).not.toHaveTextContent("Operações da tarefa");
  expect(areaRequests(fetcher)).toEqual([]);
});

// --- D-40: área Administration — roster read-only de vínculos, atrás de flag + capability.

// Sem `human`: as filas não montam na visão geral e a prova fica só sobre o gate e o painel.
function serveAdmin(capabilities: Capability[], flags: readonly string[]) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
        flags: [...flags],
      });
    }
    if (path === "/api/v1/portal/administration/memberships") {
      return json({
        schema_version: 1,
        generated_at: new Date(Date.now() + 1_000).toISOString(),
        count: 1,
        vinculos: [
          {
            principal_ref: "opaque-principal",
            membership_revision: 3,
            memberships: [{ membership_ref: "review-1", roles: ["staff"], groups: ["medico-auditor"] }],
            subject_bindings: [],
            reviewed_until: new Date(Date.now() + 86_400_000).toISOString(),
            revoked: false,
            state: "active",
          },
        ],
      });
    }
    return json({ code: "admin_read_unavailable" }, 503);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

it("mantém o stub enviado na área Administração enquanto o sinal shadow está desligado", async () => {
  // `staff_admin_read` presente (a capability não é a dúvida aqui; o sinal é): sem o sinal,
  // a área mantém o stub exato de hoje e nenhuma leitura de vínculos sai do navegador.
  const fetcher = serveAdmin(["identity", "staff_admin_read"], []);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  expect(within(tabs).getByRole("tab", { name: /^Administração/ })).not.toHaveTextContent("Em breve");
  await user.click(within(tabs).getByRole("tab", { name: /^Administração/ }));
  const panel = screen.getByRole("tabpanel");
  expect(
    within(panel).getByText("Nenhuma função administrativa está disponível para esta sessão."),
  ).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("Vínculos e permissões governados");
  expect(areaRequests(fetcher)).toEqual([]);
});

it("renderiza o painel read-only de administração quando o ambiente liga o sinal shadow", async () => {
  const fetcher = serveAdmin(["identity", "staff_admin_read"], ["staff_admin_panel"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  expect(within(tabs).getByRole("tab", { name: /^Administração/ })).not.toHaveTextContent("Em breve");
  await user.click(within(tabs).getByRole("tab", { name: /^Administração/ }));
  const panel = screen.getByRole("tabpanel");
  expect(
    await within(panel).findByRole("heading", { name: "Vínculos e permissões governados" }),
  ).toBeInTheDocument();
  expect(panel).not.toHaveTextContent("Nenhuma função administrativa está disponível");
  // Nada é consultado sem o gesto do colaborador.
  expect(areaRequests(fetcher)).toEqual([]);

  await user.click(within(panel).getByRole("button", { name: "Consultar vínculos" }));
  expect(areaRequests(fetcher)).toEqual(["/api/v1/portal/administration/memberships"]);
  const [path, options] = fetcher.mock.calls.find(([input]) => String(input).includes("administration"))!;
  expect(path).toBe("/api/v1/portal/administration/memberships");
  expect(options).toMatchObject({ method: "GET", redirect: "error" });
  expect(JSON.stringify(options)).not.toMatch(/"body"|csrf/i);
  expect(await within(panel).findByText("opaque-principal")).toBeInTheDocument();
  expect(
    within(panel).getByText(
      "Somente leitura. Nenhuma alteração de vínculo é enviada por esta tela; a administração de vínculos continua na conexão administrativa própria, fora do portal.",
    ),
  ).toBeInTheDocument();
  expect(
    within(panel).queryByRole("button", { name: /conceder|revogar|renovar|revisar|criar|salvar|editar|excluir/i }),
  ).toBeNull();
});

it("não renderiza o painel de administração com o sinal ligado quando a capability falta", async () => {
  const fetcher = serveAdmin(["identity"], ["staff_admin_panel"]);
  const user = userEvent.setup();
  render(<App />);

  const tabs = await screen.findByRole("tablist", { name: "Áreas de trabalho" });
  expect(within(tabs).getByRole("tab", { name: /^Administração\s*Em breve/ })).toBeInTheDocument();
  await user.click(within(tabs).getByRole("tab", { name: /^Administração/ }));
  const panel = screen.getByRole("tabpanel");
  expect(panel).toHaveTextContent("Esta área ainda não está habilitada neste ambiente.");
  expect(panel).not.toHaveTextContent("Vínculos e permissões governados");
  expect(areaRequests(fetcher)).toEqual([]);
});
