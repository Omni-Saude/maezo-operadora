import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { StaffOperationsPanel } from "./StaffOperationsPanel";
import type {
  StaffCaseDependencies,
  StaffCommandBasis,
  StaffCommandReceipt,
  StaffOperationsClient,
  StaffOperationsResult,
} from "./staffOperationsClient";

const taskRef = "task_escalation_000000000001";
const commandRef = "command_000000000001";
const digest = "b".repeat(64);

// O dublê é digitado pela própria interface do cliente: só os três métodos de leitura
// existem, então o painel não tem como disparar comando — a prova é de tipo e de chamada.
function client(
  basis: StaffOperationsResult<StaffCommandBasis>,
  dependencies: StaffOperationsResult<StaffCaseDependencies>,
  receipt: StaffOperationsResult<StaffCommandReceipt> | null,
): StaffOperationsClient {
  return {
    readCommandBasis: vi.fn(() => Promise.resolve(basis)),
    readCaseDependencies: vi.fn(() => Promise.resolve(dependencies)),
    readCommandReceipt: vi.fn(() =>
      Promise.resolve(receipt ?? { kind: "failure", failure: "invalid_request" } as const),
    ),
  };
}

function basis(): StaffCommandBasis {
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

function dependencies(): StaffCaseDependencies {
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
    observed_at: "2026-10-24T18:00:00Z",
  };
}

function receipt(): StaffCommandReceipt {
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

async function consult(
  user: ReturnType<typeof userEvent.setup>,
  service: StaffOperationsClient,
  command = commandRef,
) {
  render(<StaffOperationsPanel client={service} onSessionUnavailable={vi.fn()} />);
  await user.type(screen.getByLabelText("Consultar pela referência exata da tarefa"), taskRef);
  if (command !== "") {
    await user.type(screen.getByLabelText("Protocolo do comando (opcional)"), command);
  }
  await user.click(screen.getByRole("button", { name: "Consultar operações" }));
}

afterEach(() => {
  vi.clearAllMocks();
});

it("consulta as três leituras e exibe base, dependências e recibo", async () => {
  const user = userEvent.setup();
  const service = client({ kind: "success", value: basis() }, { kind: "success", value: dependencies() }, {
    kind: "success",
    value: receipt(),
  });
  await consult(user, service);

  expect(service.readCommandBasis).toHaveBeenCalledWith(taskRef, expect.any(AbortSignal));
  expect(service.readCaseDependencies).toHaveBeenCalledWith(taskRef, expect.any(AbortSignal));
  expect(service.readCommandReceipt).toHaveBeenCalledWith(taskRef, commandRef, expect.any(AbortSignal));
  expect(screen.getByRole("heading", { name: "Operações da tarefa" })).toBeInTheDocument();
  expect(screen.getAllByText(taskRef).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText("opaque_assignee_000000001")).toBeInTheDocument();
  expect(screen.getAllByText("Assumir responsabilidade").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText("Liberar responsabilidade")).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Dependências que qualquer comando desta tarefa carrega" })).toHaveTextContent("binding_000000000000001");
  expect(screen.getByText("P1")).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Recibo público do comando consultado" })).toHaveTextContent(commandRef);
  expect(screen.getByText("Pendente")).toBeInTheDocument();
  expect(screen.getByText("Ainda não registrado")).toBeInTheDocument();
  expect(
    screen.getByText(
      "Somente leitura. Nenhum comando é enviado por esta tela; os comandos continuam em Meu trabalho e Filas da equipe.",
    ),
  ).toBeInTheDocument();
});

it("consulta sem protocolo e mantém o recibo como orientação", async () => {
  const user = userEvent.setup();
  const service = client({ kind: "success", value: basis() }, { kind: "success", value: dependencies() }, null);
  await consult(user, service, "");

  expect(service.readCommandReceipt).not.toHaveBeenCalled();
  expect(
    screen.getByText("Informe o protocolo do comando para consultar o recibo público dele."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Recibo público do comando consultado" })).toBeNull();
});

it("não oferece comando, decisão, conclusão ou link de download", async () => {
  const user = userEvent.setup();
  const service = client({ kind: "success", value: basis() }, { kind: "success", value: dependencies() }, {
    kind: "success",
    value: receipt(),
  });
  await consult(user, service);
  expect(
    screen.queryByRole("button", { name: /assumir|liberar|reatribuir|concluir|decidir|enviar|cancelar/i }),
  ).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});

it("exibe erro fechado da base sem quebrar e oferece repetir", async () => {
  const user = userEvent.setup();
  const service = client(
    { kind: "failure", failure: "operation_forbidden" },
    { kind: "success", value: dependencies() },
    null,
  );
  await consult(user, service);
  expect(
    screen.getByRole("heading", { name: "Não foi possível consultar as operações" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Sua sessão não tem permissão para ver as operações desta tarefa.",
  );
  expect(screen.getByRole("button", { name: "Consultar novamente" })).toBeInTheDocument();
  expect(screen.queryByText("Dependências do caso")).toBeNull();
});

it("encaminha sessão indisponível sem oferecer repetir", async () => {
  const user = userEvent.setup();
  const onSessionUnavailable = vi.fn();
  render(
    <StaffOperationsPanel
      client={client(
        { kind: "failure", failure: "authentication_unavailable" },
        { kind: "success", value: dependencies() },
        null,
      )}
      onSessionUnavailable={onSessionUnavailable}
    />,
  );
  await user.type(screen.getByLabelText("Consultar pela referência exata da tarefa"), taskRef);
  await user.click(screen.getByRole("button", { name: "Consultar operações" }));
  expect(onSessionUnavailable).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Consultar novamente" })).toBeNull();
});

it("dependência indisponível não derruba a consulta", async () => {
  const user = userEvent.setup();
  const service = client(
    { kind: "success", value: basis() },
    { kind: "failure", failure: "context_unavailable" },
    { kind: "success", value: receipt() },
  );
  await consult(user, service);
  expect(screen.getByRole("heading", { name: "Comandos permitidos e base do comando" })).toBeInTheDocument();
  expect(screen.getByText("O contexto de dependências não está habilitado neste ambiente.")).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Não foi possível consultar as operações" }),
  ).toBeNull();
  expect(screen.getByRole("table", { name: "Recibo público do comando consultado" })).toBeInTheDocument();
});

it("exibe só a etapa e a observação quando o contexto não trouxe dependências opcionais", async () => {
  const user = userEvent.setup();
  const emptyDependencies = { ...dependencies() };
  for (const key of [
    "motivo_categoria",
    "severidade",
    "prioridade",
    "grupo_atendimento",
    "aberto_em",
    "ack_vence_em",
    "resolucao_vence_em",
    "resumo_contexto",
  ] as const) {
    (emptyDependencies as Record<typeof key, null>)[key] = null;
  }
  const service = client(
    { kind: "success", value: basis() },
    { kind: "success", value: emptyDependencies },
    null,
  );
  await consult(user, service);
  expect(screen.getByText("Atendimento")).toBeInTheDocument();
  expect(screen.queryByText("autorizacao")).toBeNull();
  expect(screen.getByText(/Contexto observado em/)).toBeInTheDocument();
  expect(screen.queryByText("Resumo da colega que abriu o caso.")).toBeNull();
});
