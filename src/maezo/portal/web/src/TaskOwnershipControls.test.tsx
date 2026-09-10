import { createHash, webcrypto } from "node:crypto";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TaskOwnershipControls } from "./TaskOwnershipControls";

const digest = "a".repeat(64);
const huge = "9007199254740993";
const commandId = "00000000-0000-4000-8000-000000000001";

function assignmentContext() {
  return {
    schema_version: "portal-assignment-context.v2",
    binding_ref: "binding-opaque", binding_version: "1", binding_digest: digest,
    policy_ref: "policy-opaque", policy_version: "1", policy_digest: digest,
    source_revision: "1", generation_digest: digest,

    task_id: "task-opaque",
    process_definition_key: "SP-OP-AUTH-001",
    process_definition_version: huge,
    process_definition_id: "AUTH:opaque",
    process_definition_digest: digest,
    task_definition_key: "UT_AnaliseMedicoAuditor",
    form_key: "auth_decisao",
    form_version: huge,
    form_digest: digest,
    expected_task_revision: huge,
    expected_evidence_revision: huge,
    expected_evidence_digest: digest,
    expected_membership_revision: huge,
    expected_authority_revision: huge,
    assignee_ref: null,
    allowed_operations: ["claim"],
    valid_until: "2026-09-10T15:05:00Z",
  };
}

function admission() {
  return {
    schema_version: 1,
    transaction_ref: "transaction-opaque",
    command_id: commandId,
    tenant: "tenant-hidden",
    principal_ref: "principal-hidden",
    task_id: "task-opaque",
    workload_ref: "workload-opaque",
    audit_intent_ref: "audit-opaque",
    outbox_ref: "outbox-opaque",
    committed_at: "2026-09-10T15:00:01Z",
    status: "pending",
  };
}

function receipt(status: "pending" | "committed" = "pending") {
  const committed = status === "committed";
  return {
    schema_version: "human-public-assignment-receipt.v1",
    command_schema: "human-assignment.v2", operation: "claim",
    binding_ref: "binding-opaque", binding_version: "1", binding_digest: digest,
    policy_ref: "policy-opaque", policy_version: "1", policy_digest: digest,
    source_revision: "1", generation_digest: digest,
    target_ref: null, target_membership_revision: null,
    prior_assignee_ref: null, resulting_assignee_ref: committed ? "principal-hidden" : null,
    assignment_disposition: committed ? "changed" : null,

    tenant: "tenant-hidden",
    task_id: "task-opaque",
    command_id: commandId,
    payload_digest: digest,
    principal_ref: "principal-hidden",
    workload_ref: "workload-opaque",
    status,
    audit_intent_ref: "audit-opaque",
    audit_intent_hash: digest,
    audit_result_ref: committed ? digest : null,
    engine_receipt_ref: committed ? "engine-opaque" : null,
    engine_recorded_at: committed ? "2026-09-10T15:00:02Z" : null,
    consumed_task_revision: committed ? huge : null,
    resulting_task_revision: committed ? huge : null,
    technical_code: null,
  };
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.setSystemTime(new Date("2026-09-10T15:00:00Z"));
  vi.stubGlobal("fetch", vi.fn());
  vi.spyOn(crypto, "randomUUID").mockReturnValue(commandId);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("mostra somente operações autorizadas e separa admissão pendente de recibo executado", async () => {
  const onCommitted = vi.fn();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(assignmentContext()))
    .mockResolvedValueOnce(jsonResponse(admission(), 202))
    .mockResolvedValueOnce(jsonResponse(receipt("pending")))
    .mockResolvedValueOnce(jsonResponse(receipt("committed")));
  render(
    <TaskOwnershipControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-a"
      onSessionUnavailable={vi.fn()}
      onCommitted={onCommitted}
    />,
  );

  expect(screen.queryByRole("button", { name: "Assumir responsabilidade" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  const claim = await screen.findByRole("button", { name: "Assumir responsabilidade" });
  expect(screen.queryByRole("button", { name: "Liberar responsabilidade" })).not.toBeInTheDocument();

  await userEvent.click(claim);
  expect(await screen.findByRole("status")).toHaveTextContent("recebida e pendente");
  expect(screen.queryByText(/executada e confirmada/)).not.toBeInTheDocument();
  expect(onCommitted).not.toHaveBeenCalled();

  const submission = JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body));
  expect(submission.command).toMatchObject({
    command_id: commandId,
    operation: "claim",
    expected_task_revision: huge,
    expected_evidence_revision: huge,
    expected_evidence_digest: digest,
    expected_membership_revision: huge,
    expected_authority_revision: huge,
  });
  expect(JSON.stringify(submission)).not.toMatch(/tenant|principal|actor/);

  await userEvent.click(screen.getByRole("button", { name: "Consultar comando" }));
  expect(await screen.findByRole("status")).toHaveTextContent("recebida e pendente");
  expect(onCommitted).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Consultar recibo" }));
  expect(await screen.findByRole("status")).toHaveTextContent("executada e confirmada");
  expect(onCommitted).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Atualizar fila" }));
  expect(onCommitted).toHaveBeenCalledOnce();
  expect(screen.queryByText("tenant-hidden")).not.toBeInTheDocument();
  expect(screen.queryByText("principal-hidden")).not.toBeInTheDocument();
});

it("recupera resultado desconhecido consultando ou reenviando exatamente o mesmo comando", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(assignmentContext()))
    .mockRejectedValueOnce(new TypeError("network"))
    .mockResolvedValueOnce(jsonResponse(admission(), 202));
  render(
    <TaskOwnershipControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-a"
      onSessionUnavailable={vi.fn()}
      onCommitted={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  await userEvent.click(await screen.findByRole("button", { name: "Assumir responsabilidade" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Resultado desconhecido");
  await userEvent.click(screen.getByRole("button", { name: "Reenviar o mesmo comando" }));
  expect(await screen.findByRole("status")).toHaveTextContent("recebida e pendente");
  const firstBody = vi.mocked(fetch).mock.calls[1][1]?.body;
  const retryBody = vi.mocked(fetch).mock.calls[2][1]?.body;
  expect(retryBody).toBe(firstBody);
  expect(JSON.parse(String(retryBody)).command.command_id).toBe(commandId);
});

it("descarta a autorização anterior ao mudar de tarefa ou sessão", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(assignmentContext()));
  const view = render(
    <TaskOwnershipControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-a"
      onSessionUnavailable={vi.fn()}
      onCommitted={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  expect(await screen.findByRole("button", { name: "Assumir responsabilidade" })).toBeInTheDocument();
  view.rerender(
    <TaskOwnershipControls
      taskId="task-other"
      csrfToken="csrf-secret"
      sessionBinding="session-b"
      onSessionUnavailable={vi.fn()}
      onCommitted={vi.fn()}
    />,
  );
  await waitFor(() => expect(screen.queryByRole("button", { name: "Assumir responsabilidade" })).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Consultar responsabilidade" })).toBeInTheDocument();
});

it("aborta um envio pendente e remove o protocolo ao mudar de sessão", async () => {
  const pending = deferred<Response>();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(assignmentContext()))
    .mockReturnValueOnce(pending.promise);
  const view = render(
    <TaskOwnershipControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-a"
      onSessionUnavailable={vi.fn()}
      onCommitted={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  await userEvent.click(await screen.findByRole("button", { name: "Assumir responsabilidade" }));
  const signal = vi.mocked(fetch).mock.calls[1][1]?.signal;
  expect(signal?.aborted).toBe(false);
  view.rerender(
    <TaskOwnershipControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-b"
      onSessionUnavailable={vi.fn()}
      onCommitted={vi.fn()}
    />,
  );
  expect(signal?.aborted).toBe(true);
  expect(screen.queryByText(`Protocolo: ${commandId}`)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Consultar responsabilidade" })).toBeInTheDocument();
});

it("propaga a perda de sessão e remove controles autorizados", async () => {
  const onSessionUnavailable = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({
    schema_version: "portal-decision-error.v1",
    code: "authentication_unavailable",
  }, 401));
  render(
    <TaskOwnershipControls
      taskId="task-opaque"
      csrfToken="csrf-secret"
      sessionBinding="session-a"
      onSessionUnavailable={onSessionUnavailable}
      onCommitted={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  await waitFor(() => expect(onSessionUnavailable).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: "Assumir responsabilidade" })).not.toBeInTheDocument();
});

async function ownershipClick(name: string) {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
}

it.each(["pending", "unknown", "sending"] as const)("mantém identidade %s durante expiry e renovação independente da autorização", async (kind) => {
  vi.useFakeTimers();
  const late = deferred<Response>();
  const first = { ...assignmentContext(), valid_until: "2026-09-10T15:00:01Z" };
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(first));
  if (kind === "sending") vi.mocked(fetch).mockReturnValueOnce(late.promise);
  else vi.mocked(fetch).mockResolvedValueOnce(kind === "pending" ? jsonResponse(admission(), 202) : jsonResponse({ schema_version: "portal-decision-error.v1", code: "admission_unavailable" }, 503));
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(assignmentContext()));
  render(<TaskOwnershipControls taskId="task-opaque" csrfToken="csrf-secret" sessionBinding="session-a" onSessionUnavailable={vi.fn()} onCommitted={vi.fn()} />);
  await ownershipClick("Consultar responsabilidade");
  await ownershipClick("Assumir responsabilidade");
  const post = vi.mocked(fetch).mock.calls[1][1]!;
  await act(async () => { await vi.advanceTimersByTimeAsync(1001); });
  await ownershipClick("Atualizar autorização");
  expect(post.signal?.aborted).toBe(false);
  expect(screen.getByText(`Protocolo: ${commandId}`)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Assumir responsabilidade" })).toBeDisabled();
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  if (kind === "sending") {
    await act(async () => { late.resolve(jsonResponse(admission(), 202)); });
    expect(screen.getByText(/Solicitação recebida e pendente/)).toBeInTheDocument();
  }
  if (kind === "unknown") {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ schema_version: "portal-decision-error.v1", code: "revision_conflict" }, 409));
    await ownershipClick("Reenviar o mesmo comando");
    expect(vi.mocked(fetch).mock.calls[3][1]?.body).toBe(post.body);
    expect(screen.getByText(`Protocolo: ${commandId}`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reenviar o mesmo comando" })).toBeInTheDocument();
  }
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(receipt("committed")));
  await ownershipClick("Consultar recibo");
  expect(screen.getByText(/Alteração executada e confirmada/)).toBeInTheDocument();
  expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
});

it("seleciona apenas candidato autorizado, preserva alvo no reenvio e rejeita recibo de outra operação", async () => {
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(crypto, "randomUUID").mockReturnValue(commandId);
  const basis = { ...assignmentContext(), assignee_ref: "previous", allowed_operations: ["reassign"] };
  const candidates = [{ target_membership_revision: huge, target_ref: "target-opaque" }];
  const page = { schema_version: "portal-assignment-candidates.v1", context: basis, candidates,
    candidate_count: "1", candidate_digest: createHash("sha256").update(JSON.stringify(candidates)).digest("hex"), valid_until: basis.valid_until };
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(basis)).mockResolvedValueOnce(jsonResponse(page))
    .mockResolvedValueOnce(jsonResponse({ schema_version: "portal-decision-error.v1", code: "operation_forbidden" }, 403))
    .mockResolvedValueOnce(jsonResponse(admission(), 202)).mockResolvedValueOnce(jsonResponse(receipt("committed")));
  const onCommitted = vi.fn();
  render(<TaskOwnershipControls taskId="task-opaque" csrfToken="csrf" sessionBinding="session-a" onSessionUnavailable={vi.fn()} onCommitted={onCommitted} />);
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  expect(await screen.findByRole("button", { name: "Reatribuir responsabilidade" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Assumir responsabilidade" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Consultar destinatários autorizados" }));
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Destinatário autorizado" }), "target-opaque");
  await userEvent.click(screen.getByRole("button", { name: "Reatribuir responsabilidade" }));
  expect(await screen.findByText(/Resultado desconhecido/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Reenviar o mesmo comando" }));
  expect(await screen.findByText(/Solicitação recebida e pendente/)).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls[3][1]?.body).toBe(vi.mocked(fetch).mock.calls[2][1]?.body);
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body)).command).toMatchObject({ target_ref: "target-opaque", expected_target_membership_revision: huge, expected_assignee_ref: "previous" });
  await userEvent.click(screen.getByRole("button", { name: "Consultar recibo" }));
  expect(await screen.findByText(/resposta inesperada/)).toBeInTheDocument();
  expect(screen.queryByText(/Alteração executada e confirmada/)).not.toBeInTheDocument();
  expect(onCommitted).not.toHaveBeenCalled();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ...receipt("committed"), operation: "reassign",
    target_ref: "target-opaque", target_membership_revision: huge,
    prior_assignee_ref: "previous", resulting_assignee_ref: "target-opaque" }));
  await userEvent.click(screen.getByRole("button", { name: "Consultar recibo" }));
  expect(await screen.findByText(/Alteração executada e confirmada/)).toBeInTheDocument();
});
it("descarta candidatos tardios depois de mudança de sessão", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const late = deferred<Response>();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ...assignmentContext(), allowed_operations: ["reassign"] })).mockReturnValueOnce(late.promise);
  const props = { taskId: "task-opaque", csrfToken: "csrf", onSessionUnavailable: vi.fn(), onCommitted: vi.fn() };
  const view = render(<TaskOwnershipControls {...props} sessionBinding="session-a" />);
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  await userEvent.click(await screen.findByRole("button", { name: "Consultar destinatários autorizados" }));
  const signal = vi.mocked(fetch).mock.calls[1][1]?.signal;
  view.rerender(<TaskOwnershipControls {...props} sessionBinding="session-b" />);
  expect(signal?.aborted).toBe(true);
  await act(async () => { late.resolve(jsonResponse({})); });
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Consultar responsabilidade" })).toBeInTheDocument();
});

it("oculta protocolo após POST401 e retém o mesmo comando se a sessão atual volta a autorizar", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(assignmentContext()))
    .mockResolvedValueOnce(jsonResponse({ schema_version: "portal-decision-error.v1", code: "authentication_unavailable" }, 401))
    .mockResolvedValueOnce(jsonResponse(assignmentContext())).mockResolvedValueOnce(jsonResponse(admission(), 202));
  const unavailable = vi.fn();
  render(<TaskOwnershipControls taskId="task-opaque" csrfToken="csrf" sessionBinding="session-a" onSessionUnavailable={unavailable} onCommitted={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  await userEvent.click(await screen.findByRole("button", { name: "Assumir responsabilidade" }));
  await waitFor(() => expect(unavailable).toHaveBeenCalledOnce());
  expect(screen.queryByText(`Protocolo: ${commandId}`)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Consultar responsabilidade" }));
  expect(await screen.findByText(/Resultado desconhecido/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Reenviar o mesmo comando" }));
  expect(await screen.findByText(/Solicitação recebida e pendente/)).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls[3][1]?.body).toBe(vi.mocked(fetch).mock.calls[1][1]?.body);
  expect(crypto.randomUUID).toHaveBeenCalledOnce();
});
