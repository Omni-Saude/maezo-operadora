import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  sessionLevelFailures,
  type StaffCaseDependencies,
  type StaffCommandBasis,
  type StaffCommandReceipt,
  type StaffOperationsClient,
  type StaffOperationsFailure,
  type StaffOperationsResult,
} from "./staffOperationsClient";

// W5 CLNT 5.5: the staff `operations` area, READ-ONLY. The panel consults the three GET
// routes the BFF already mounts (governed assignment basis and public command receipt in
// `decisions.py`, escalation task context in `tasks.py`) and renders status only: no claim,
// release, reassign, decision or completion exists on this surface — commands stay on the
// queues plane, where the governed submission discipline already lives.

type ConsultState =
  | Readonly<{ kind: "idle" }>
  | Readonly<{ kind: "loading"; taskRef: string }>
  | Readonly<{
      kind: "ready";
      taskRef: string;
      basis: StaffCommandBasis;
      dependencies: StaffOperationsResult<StaffCaseDependencies>;
      receipt: StaffOperationsResult<StaffCommandReceipt> | null;
    }>
  | Readonly<{ kind: "error"; taskRef: string; failure: StaffOperationsFailure }>;

const errorMessage: Readonly<Record<StaffOperationsFailure, string>> = {
  invalid_request: "Confira as referências: tarefa e protocolo seguem o formato opaco do portal.",
  invalid_decision: "O pedido não corresponde ao contrato atual da tarefa.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  session_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Sua sessão não tem permissão para ver as operações desta tarefa.",
  employee_access_required: "Sua sessão não tem permissão para ver as operações desta tarefa.",
  resource_unavailable: "A tarefa não foi encontrada ou não está liberada para a sua sessão.",
  revision_conflict: "A tarefa mudou durante a consulta. Consulte de novo.",
  refresh_required: "A tarefa mudou durante a consulta. Consulte de novo.",
  authority_unavailable: "A autorização atual não pôde ser confirmada.",
  task_unavailable: "A tarefa não está mais disponível.",
  form_projection_unavailable: "A projeção atual da tarefa não está disponível.",
  form_contract_unavailable: "O contrato atual da tarefa não está disponível.",
  admission_unavailable: "O serviço de comandos não confirmou este protocolo.",
  credential_scope_mismatch: "A credencial atual não autoriza esta consulta.",
  production_capabilities_unavailable: "Esta consulta não está habilitada neste ambiente.",
  context_unavailable: "O contexto de dependências não está habilitado neste ambiente.",
  dependency_unavailable: "Uma dependência não respondeu. Nada foi exibido.",
  read_dependency_unavailable: "Uma dependência não respondeu. Nada foi exibido.",
  "invalid-response": "O portal recusou uma resposta inesperada do serviço de operações. Nada foi exibido.",
};

// Closed maps over the contracts' own literals. An unknown token is shown raw, never hidden.
const operationLabels: Readonly<Record<string, string>> = {
  claim: "Assumir responsabilidade",
  release: "Liberar responsabilidade",
  reassign: "Reatribuir responsabilidade",
  decision: "Decisão",
};

const receiptStatusLabels: Readonly<Record<string, string>> = {
  pending: "Pendente",
  committed: "Efetivado",
  conflict: "Em conflito",
};

const stageLabels: Readonly<Record<string, string>> = {
  atendimento: "Atendimento",
  supervision: "Supervisão",
};

function label(value: string | null | undefined, labels: Readonly<Record<string, string>>) {
  if (value === null || value === undefined) return null;
  return Object.hasOwn(labels, value) ? labels[value] : value;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function OperationList({ operations }: { operations: readonly string[] }) {
  return (
    <ul className="document-type-list">
      {operations.map((operation) => (
        <li key={operation} className="exact-value">
          {label(operation, operationLabels)}
        </li>
      ))}
    </ul>
  );
}

function BasisSection({ basis }: { basis: StaffCommandBasis }) {
  const dependencies: readonly Readonly<{ term: string; value: string }>[] = [
    { term: "Vínculo governado", value: `${basis.binding_ref} (revisão ${basis.binding_version})` },
    { term: "Política governada", value: `${basis.policy_ref} (revisão ${basis.policy_version})` },
    { term: "Revisão da fonte", value: basis.source_revision },
    { term: "Revisão esperada da tarefa", value: basis.expected_task_revision },
    { term: "Revisão esperada da evidência", value: basis.expected_evidence_revision },
    { term: "Revisão esperada do vínculo", value: basis.expected_membership_revision },
    { term: "Revisão esperada da autoridade", value: basis.expected_authority_revision },
  ];
  return (
    <section aria-labelledby="staff-operations-basis-heading">
      <h3 id="staff-operations-basis-heading">Comandos permitidos e base do comando</h3>
      <dl className="dependency-detail">
        <div>
          <dt>Tarefa</dt>
          <dd className="exact-value">{basis.task_id}</dd>
        </div>
        <div>
          <dt>Responsável atual</dt>
          <dd className="exact-value">{basis.assignee_ref ?? "Nenhum responsável atribuído"}</dd>
        </div>
        <div>
          <dt>Comandos permitidos agora</dt>
          <dd>
            <OperationList operations={basis.allowed_operations} />
          </dd>
        </div>
      </dl>
      <div className="table-scroll" tabIndex={0} aria-label="Tabela de dependências do comando da tarefa">
        <table>
          <caption className="visually-hidden">Dependências que qualquer comando desta tarefa carrega</caption>
          <thead><tr><th scope="col">Dependência</th><th scope="col">Valor exato</th></tr></thead>
          <tbody>
            {dependencies.map((dependency) => (
              <tr key={dependency.term}>
                <th scope="row">{dependency.term}</th>
                <td className="exact-value">{dependency.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="freshness-detail">
        Base válida até {formatTimestamp(basis.valid_until)}; consulte de novo depois desse momento.
      </p>
    </section>
  );
}

function DependenciesSection({
  dependencies,
}: {
  dependencies: StaffOperationsResult<StaffCaseDependencies>;
}) {
  if (dependencies.kind === "failure") {
    return (
      <section aria-labelledby="staff-operations-dependencies-heading">
        <h3 id="staff-operations-dependencies-heading">Dependências do caso</h3>
        <p className="queue-message" role="status">{errorMessage[dependencies.failure]}</p>
      </section>
    );
  }
  const context = dependencies.value;
  const stages: readonly Readonly<{ term: string; value: string | null; timestamp?: boolean }>[] = [
    { term: "Etapa", value: label(context.etapa, stageLabels) },
    { term: "Motivo (categoria)", value: context.motivo_categoria },
    { term: "Severidade", value: context.severidade },
    { term: "Prioridade", value: context.prioridade },
    { term: "Grupo de atendimento", value: context.grupo_atendimento },
    { term: "Aberto em", value: context.aberto_em, timestamp: true },
    { term: "Reconhecimento vence em", value: context.ack_vence_em, timestamp: true },
    { term: "Resolução vence em", value: context.resolucao_vence_em, timestamp: true },
  ];
  const presented = stages.filter((stage) => stage.value !== null);
  // `etapa` is required on the wire, so this list is never empty: a case without optional
  // dependencies still shows its stage and the observation time, and nothing else.
  return (
    <section aria-labelledby="staff-operations-dependencies-heading">
      <h3 id="staff-operations-dependencies-heading">Dependências do caso</h3>
      <dl className="dependency-detail">
        {presented.map((stage) => (
          <div key={stage.term}>
            <dt>{stage.term}</dt>
            <dd className="exact-value">
              {stage.timestamp ? formatTimestamp(stage.value!) : stage.value}
            </dd>
          </div>
        ))}
      </dl>
      {context.resumo_contexto !== null && (
        <p className="dependency-summary">{context.resumo_contexto}</p>
      )}
      <p className="freshness-detail">Contexto observado em {formatTimestamp(context.observed_at)}.</p>
    </section>
  );
}

function ReceiptSection({
  receipt,
}: {
  receipt: StaffOperationsResult<StaffCommandReceipt> | null;
}) {
  if (receipt === null) {
    return (
      <section aria-labelledby="staff-operations-receipt-heading">
        <h3 id="staff-operations-receipt-heading">Recibo do comando</h3>
        <p>Informe o protocolo do comando para consultar o recibo público dele.</p>
      </section>
    );
  }
  if (receipt.kind === "failure") {
    return (
      <section aria-labelledby="staff-operations-receipt-heading">
        <h3 id="staff-operations-receipt-heading">Recibo do comando</h3>
        <p className="queue-message" role="status">{errorMessage[receipt.failure]}</p>
      </section>
    );
  }
  const value = receipt.value;
  const status = label(value.status, receiptStatusLabels) ?? value.status;
  // The legacy v1 receipt carries no operation on the wire (the server strips it); the
  // governed v2 and the decision v2 both name theirs.
  const operation = "operation" in value ? label(value.operation, operationLabels) : null;
  return (
    <section aria-labelledby="staff-operations-receipt-heading">
      <h3 id="staff-operations-receipt-heading">Recibo do comando</h3>
      <div className="table-scroll" tabIndex={0} aria-label="Tabela do recibo público do comando">
        <table>
          <caption className="visually-hidden">Recibo público do comando consultado</caption>
          <tbody>
            <tr><th scope="row">Protocolo</th><td className="exact-value">{value.command_id}</td></tr>
            <tr><th scope="row">Operação</th><td className="exact-value">{operation ?? "—"}</td></tr>
            <tr><th scope="row">Situação</th><td className="exact-value">{status}</td></tr>
            <tr>
              <th scope="row">Registrado pelo motor</th>
              <td className="exact-value">
                {value.engine_recorded_at == null ? "Ainda não registrado" : formatTimestamp(value.engine_recorded_at)}
              </td>
            </tr>
            <tr>
              <th scope="row">Referência do motor</th>
              <td className="exact-value">{value.engine_receipt_ref ?? "—"}</td>
            </tr>
            {value.technical_code != null && (
              <tr><th scope="row">Resultado técnico</th><td className="exact-value">{value.technical_code}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function StaffOperationsPanel({
  client,
  onSessionUnavailable,
}: {
  client: StaffOperationsClient;
  onSessionUnavailable: () => void;
}) {
  const taskFieldId = useId();
  const commandFieldId = useId();
  const [taskRef, setTaskRef] = useState("");
  const [commandRef, setCommandRef] = useState("");
  const [consult, setConsult] = useState<ConsultState>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const resultHeading = useRef<HTMLHeadingElement | null>(null);

  const consultTask = useCallback(async (requestedTaskRef: string, requestedCommandRef: string) => {
    const current = ++epoch.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setConsult({ kind: "loading", taskRef: requestedTaskRef });
    try {
      // All three reads ride the mounted GET routes; none carries a body — the panel has no
      // way to mutate anything.
      const [basis, dependencies, receipt] = await Promise.all([
        client.readCommandBasis(requestedTaskRef, controller.signal),
        client.readCaseDependencies(requestedTaskRef, controller.signal),
        requestedCommandRef === ""
          ? Promise.resolve(null)
          : client.readCommandReceipt(requestedTaskRef, requestedCommandRef, controller.signal),
      ]);
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      if (basis.kind === "failure") {
        setConsult({ kind: "error", taskRef: requestedTaskRef, failure: basis.failure });
        if (sessionLevelFailures.includes(basis.failure)) onSessionUnavailable();
        return;
      }
      const escalated =
        (dependencies.kind === "failure" && sessionLevelFailures.includes(dependencies.failure)) ||
        (receipt?.kind === "failure" && sessionLevelFailures.includes(receipt.failure));
      if (escalated) onSessionUnavailable();
      setConsult({
        kind: "ready",
        taskRef: requestedTaskRef,
        basis: basis.value,
        dependencies,
        receipt,
      });
    } catch (error) {
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      setConsult({ kind: "error", taskRef: requestedTaskRef, failure: "dependency_unavailable" });
    }
  }, [client, onSessionUnavailable]);

  useEffect(() => () => {
    epoch.current += 1;
    request.current?.abort();
    request.current = null;
  }, []);

  useEffect(() => {
    if (consult.kind === "error") resultHeading.current?.focus();
  }, [consult]);

  return (
    <section className="staff-operations" aria-labelledby="staff-operations-heading">
      <div className="case-queue-heading">
        <div>
          <h2 id="staff-operations-heading">Operações da tarefa</h2>
          <p>
            Consulta somente leitura dos comandos permitidos, das dependências e do recibo de uma
            tarefa liberada para a sua sessão.
          </p>
        </div>
      </div>
      <form
        className="case-lookup"
        onSubmit={(event) => {
          event.preventDefault();
          void consultTask(taskRef.trim(), commandRef.trim());
        }}
      >
        <label htmlFor={taskFieldId}>Consultar pela referência exata da tarefa</label>
        <div className="case-lookup-row">
          <input
            id={taskFieldId}
            name="task-reference"
            value={taskRef}
            onChange={(event) => setTaskRef(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            required
            minLength={1}
            maxLength={512}
            aria-describedby={`${taskFieldId}-help`}
          />
          <label htmlFor={commandFieldId}>Protocolo do comando (opcional)</label>
          <input
            id={commandFieldId}
            name="command-reference"
            value={commandRef}
            onChange={(event) => setCommandRef(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            maxLength={512}
            aria-describedby={`${taskFieldId}-help`}
          />
          <button className="primary-action" type="submit" disabled={consult.kind === "loading"}>
            {consult.kind === "loading" ? "Consultando…" : "Consultar operações"}
          </button>
        </div>
        <small id={`${taskFieldId}-help`}>
          O protocolo do comando vem do recibo exibido em Meu trabalho ou Filas da equipe.
        </small>
      </form>
      <div aria-live="polite" aria-atomic="false">
        {consult.kind === "loading" && <p className="queue-message">Consultando as operações da tarefa.</p>}
        {consult.kind === "error" && (
          <section className="queue-message queue-error" aria-labelledby="staff-operations-error-heading">
            <h3 id="staff-operations-error-heading" ref={resultHeading} tabIndex={-1}>
              Não foi possível consultar as operações
            </h3>
            <p role="alert">{errorMessage[consult.failure]}</p>
            {!sessionLevelFailures.includes(consult.failure) && (
              <button
                className="secondary-action"
                type="button"
                onClick={() => void consultTask(consult.taskRef, commandRef.trim())}
              >
                Consultar novamente
              </button>
            )}
          </section>
        )}
      </div>
      {consult.kind === "ready" && (
        <>
          <p className="exact-value case-detail-ref">{consult.taskRef}</p>
          <BasisSection basis={consult.basis} />
          <DependenciesSection dependencies={consult.dependencies} />
          <ReceiptSection receipt={consult.receipt} />
          <p className="freshness-detail">
            Somente leitura. Nenhum comando é enviado por esta tela; os comandos continuam em Meu
            trabalho e Filas da equipe.
          </p>
        </>
      )}
    </section>
  );
}
