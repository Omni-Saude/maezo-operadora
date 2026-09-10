import { useCallback, useEffect, useId, useRef, useState } from "react";

import type {
  StaffCaseClient,
  StaffCaseFailure,
  StaffDetail,
} from "./staffCaseClient";
import { expiryDelay } from "./taskReadTime";

type WorkspaceState =
  | Readonly<{ kind: "idle" }>
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "ready"; detail: StaffDetail }>
  | Readonly<{ kind: "error"; failure: StaffCaseFailure }>;

const errorMessage: Readonly<Record<StaffCaseFailure, string>> = {
  invalid_request: "Confira a referência exata do caso.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Sua sessão atual não autoriza esta consulta.",
  resource_unavailable: "O caso não foi encontrado ou não está autorizado para esta sessão.",
  conflict: "O caso mudou durante a consulta. Consulte novamente.",
  dependency_unavailable: "A consulta de casos está temporariamente indisponível.",
  "invalid-response": "O portal recusou uma resposta inesperada do serviço de casos.",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function StaffCaseDetail({ detail }: { detail: StaffDetail }) {
  return (
    <section className="staff-case-detail" aria-labelledby="staff-case-detail-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Projeção autorizada do caso</p>
          <h3 id="staff-case-detail-heading" tabIndex={-1}>Caso de autorização</h3>
        </div>
        <p className="freshness">Consultado em {formatTimestamp(detail.freshness.observed_at)}</p>
      </div>
      <p>
        Esta consulta mostra a identidade do processo e as tarefas ativas autorizadas. Dossiê,
        histórico e documentos exigem projeções próprias.
      </p>
      <dl className="task-facts">
        <div><dt>Referência do caso</dt><dd className="exact-value">{detail.case.case_ref}</dd></div>
        <div><dt>Situação</dt><dd>{detail.case.state === "active" ? "Ativo" : "Encerrado"}</dd></div>
        <div><dt>Revisão do caso</dt><dd className="exact-value">{detail.case.record_revision}</dd></div>
        <div><dt>Estado observado em</dt><dd>{formatTimestamp(detail.case.state_observed_at)}</dd></div>
        <div><dt>Processo</dt><dd>{detail.identity.process_definition_key}</dd></div>
        <div><dt>Versão do processo</dt><dd className="exact-value">{detail.identity.process_definition_version}</dd></div>
        <div><dt>Instância do processo</dt><dd className="exact-value">{detail.identity.process_instance_ref}</dd></div>
        <div><dt>Fonte observada em</dt><dd>{formatTimestamp(detail.freshness.source_observed_at)}</dd></div>
        <div><dt>Consulta válida até</dt><dd>{formatTimestamp(detail.freshness.valid_until)}</dd></div>
      </dl>
      <section className="staff-case-tasks" aria-labelledby="staff-case-tasks-heading">
        <h4 id="staff-case-tasks-heading">Tarefas ativas</h4>
        {detail.active_tasks.length === 0 ? (
          <p>Nenhuma tarefa ativa foi liberada nesta consulta.</p>
        ) : (
          <div className="table-scroll" tabIndex={0} aria-label="Tabela de tarefas ativas do caso">
            <table>
              <caption className="visually-hidden">Tarefas ativas autorizadas para este caso</caption>
              <thead>
                <tr>
                  <th scope="col">Tarefa</th>
                  <th scope="col">Responsável</th>
                  <th scope="col">Criada em</th>
                  <th scope="col">Prazo</th>
                  <th scope="col">Revisão</th>
                </tr>
              </thead>
              <tbody>
                {detail.active_tasks.map((task) => (
                  <tr key={task.task_id}>
                    <th scope="row">{task.task_definition_key}</th>
                    <td>{task.assignee_ref ?? "Sem responsável"}</td>
                    <td>{formatTimestamp(task.created_at)}</td>
                    <td>{task.due_at === null ? "Sem prazo informado" : formatTimestamp(task.due_at)}</td>
                    <td className="exact-value">{task.task_revision}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="freshness-detail">A resposta confirmou a página completa de tarefas desta consulta.</p>
      </section>
    </section>
  );
}

export function StaffCaseWorkspace({
  service,
  onSessionUnavailable,
}: {
  service: StaffCaseClient;
  onSessionUnavailable: () => void;
}) {
  const inputId = useId();
  const [caseRef, setCaseRef] = useState("");
  const [state, setState] = useState<WorkspaceState>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const resultHeading = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    epoch.current += 1;
    request.current?.abort();
    request.current = null;
    setCaseRef("");
    setState({ kind: "idle" });
    return () => {
      epoch.current += 1;
      request.current?.abort();
      request.current = null;
    };
  }, [service]);

  useEffect(() => {
    if (state.kind === "ready" || state.kind === "error") resultHeading.current?.focus();
  }, [state]);

  const readCase = useCallback(async (requestedCaseRef: string) => {
    const current = ++epoch.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState({ kind: "loading" });
    try {
      const result = await service.readCase(requestedCaseRef, controller.signal);
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      if (result.kind === "success") {
        setState({ kind: "ready", detail: result.value });
      } else {
        setState({ kind: "error", failure: result.failure });
        if (result.failure === "authentication_unavailable") onSessionUnavailable();
      }
    } catch (error) {
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      setState({ kind: "error", failure: "dependency_unavailable" });
    }
  }, [onSessionUnavailable, service]);

  useEffect(() => {
    if (state.kind !== "ready") return;
    const delay = Math.min(
      state.detail.freshness.refresh_after_seconds * 1000,
      expiryDelay(state.detail.freshness.valid_until),
    );
    const timer = window.setTimeout(() => {
      void readCase(state.detail.case.case_ref);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [readCase, state]);

  return (
    <section aria-labelledby="staff-case-heading">
      <div className="portal-guidance">
        <p className="eyebrow">Consulta autorizada</p>
        <h2 id="staff-case-heading">Consultar um caso pela referência exata</h2>
        <p>
          Informe a referência recebida em um fluxo autorizado. A consulta não pesquisa outros
          casos e não amplia as permissões desta sessão.
        </p>
        <form className="intake-form" onSubmit={(event) => { event.preventDefault(); void readCase(caseRef.trim()); }}>
          <div className="form-grid">
            <label htmlFor={inputId}>
              Referência exata do caso
              <input
                id={inputId}
                name="case-reference"
                value={caseRef}
                onChange={(event) => setCaseRef(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                required
                minLength={16}
                maxLength={128}
                aria-describedby={`${inputId}-help`}
              />
            </label>
            <small id={`${inputId}-help`}>A referência permanece somente nesta tela e não é incluída na URL.</small>
          </div>
          <button className="primary-action" type="submit" disabled={state.kind === "loading"}>
            {state.kind === "loading" ? "Consultando…" : "Consultar caso"}
          </button>
        </form>
      </div>
      <div aria-live="polite" aria-atomic="false">
        {state.kind === "loading" && <p className="queue-message">Consultando o caso autorizado.</p>}
        {state.kind === "error" && (
          <section className="queue-message queue-error" aria-labelledby="staff-case-error-heading">
            <h3 id="staff-case-error-heading" ref={resultHeading} tabIndex={-1}>Não foi possível consultar o caso</h3>
            <p role="alert">{errorMessage[state.failure]}</p>
            {state.failure !== "authentication_unavailable" && (
              <button className="secondary-action" type="button" onClick={() => void readCase(caseRef.trim())}>
                Consultar novamente
              </button>
            )}
          </section>
        )}
        {state.kind === "ready" && (
          <div ref={(node) => { resultHeading.current = node?.querySelector("h3") ?? null; }}>
            <StaffCaseDetail detail={state.detail} />
          </div>
        )}
      </div>
    </section>
  );
}
