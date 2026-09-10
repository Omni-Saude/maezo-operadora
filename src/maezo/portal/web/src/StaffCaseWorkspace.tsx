import { useCallback, useEffect, useId, useRef, useState } from "react";

import type {
  StaffCaseClient,
  StaffCaseFailure,
  StaffDetail,
  StaffPage,
} from "./staffCaseClient";
import { expiryDelay } from "./taskReadTime";

type ListState =
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "ready"; page: StaffPage; loadingMore: boolean }>
  | Readonly<{ kind: "error"; failure: StaffCaseFailure }>;

type DetailState =
  | Readonly<{ kind: "loading"; caseRef: string }>
  | Readonly<{ kind: "ready"; detail: StaffDetail }>
  | Readonly<{ kind: "error"; caseRef: string; failure: StaffCaseFailure }>;

const errorMessage: Readonly<Record<StaffCaseFailure, string>> = {
  invalid_request: "Confira os dados desta consulta.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Sua sessão atual não autoriza esta consulta.",
  resource_unavailable: "O caso não foi encontrado ou não está autorizado para esta sessão.",
  conflict: "Os casos mudaram durante a consulta. Atualize a lista.",
  dependency_unavailable: "A fonte autorizada de casos está temporariamente indisponível.",
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
              <thead><tr><th scope="col">Tarefa</th><th scope="col">Responsável</th><th scope="col">Criada em</th><th scope="col">Prazo</th><th scope="col">Revisão</th></tr></thead>
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

function joinedPage(previous: StaffPage, next: StaffPage): StaffPage | null {
  const priorLast = previous.items.at(-1)?.case_ref;
  const nextFirst = next.items[0]?.case_ref;
  if (priorLast !== undefined && nextFirst !== undefined && nextFirst <= priorLast) return null;
  return { ...next, items: [...previous.items, ...next.items] };
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
  const [listState, setListState] = useState<ListState>({ kind: "loading" });
  const [detailState, setDetailState] = useState<DetailState | null>(null);
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const resultHeading = useRef<HTMLHeadingElement | null>(null);

  const loadList = useCallback(async (cursor: string | null, append: boolean) => {
    const current = ++epoch.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setDetailState(null);
    setListState((prior) => append && prior.kind === "ready"
      ? { ...prior, loadingMore: true }
      : { kind: "loading" });
    try {
      const result = await service.listCases(cursor, controller.signal);
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      if (result.kind === "failure") {
        setListState({ kind: "error", failure: result.failure });
        if (result.failure === "authentication_unavailable") onSessionUnavailable();
        return;
      }
      setListState((prior) => {
        if (!append || prior.kind !== "ready") return { kind: "ready", page: result.value, loadingMore: false };
        const joined = joinedPage(prior.page, result.value);
        return joined === null
          ? { kind: "error", failure: "invalid-response" }
          : { kind: "ready", page: joined, loadingMore: false };
      });
    } catch (error) {
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      setListState({ kind: "error", failure: "dependency_unavailable" });
    }
  }, [onSessionUnavailable, service]);

  const readCase = useCallback(async (requestedCaseRef: string) => {
    const current = ++epoch.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setDetailState({ kind: "loading", caseRef: requestedCaseRef });
    try {
      const result = await service.readCase(requestedCaseRef, controller.signal);
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      if (result.kind === "success") setDetailState({ kind: "ready", detail: result.value });
      else {
        setDetailState({ kind: "error", caseRef: requestedCaseRef, failure: result.failure });
        if (result.failure === "authentication_unavailable") onSessionUnavailable();
      }
    } catch (error) {
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      setDetailState({ kind: "error", caseRef: requestedCaseRef, failure: "dependency_unavailable" });
    }
  }, [onSessionUnavailable, service]);

  useEffect(() => {
    setCaseRef("");
    void loadList(null, false);
    return () => {
      epoch.current += 1;
      request.current?.abort();
      request.current = null;
    };
  }, [loadList]);

  useEffect(() => {
    if (detailState?.kind === "ready" || detailState?.kind === "error" || listState.kind === "error") resultHeading.current?.focus();
  }, [detailState, listState]);

  useEffect(() => {
    if (detailState?.kind === "ready") {
      const delay = Math.min(detailState.detail.freshness.refresh_after_seconds * 1000, expiryDelay(detailState.detail.freshness.valid_until));
      const timer = window.setTimeout(() => void readCase(detailState.detail.case.case_ref), delay);
      return () => window.clearTimeout(timer);
    }
    if (detailState === null && listState.kind === "ready" && !listState.loadingMore) {
      const delay = Math.min(listState.page.freshness.refresh_after_seconds * 1000, expiryDelay(listState.page.freshness.valid_until));
      const timer = window.setTimeout(() => void loadList(null, false), delay);
      return () => window.clearTimeout(timer);
    }
  }, [detailState, listState, loadList, readCase]);

  if (detailState !== null) {
    return (
      <section aria-labelledby="staff-case-heading">
        <button className="secondary-action" type="button" onClick={() => void loadList(null, false)}>Voltar para casos</button>
        <div aria-live="polite" aria-atomic="false">
          {detailState.kind === "loading" && <p className="queue-message">Consultando o caso autorizado.</p>}
          {detailState.kind === "error" && (
            <section className="queue-message queue-error" aria-labelledby="staff-case-error-heading">
              <h2 id="staff-case-error-heading" ref={resultHeading} tabIndex={-1}>Não foi possível consultar o caso</h2>
              <p role="alert">{errorMessage[detailState.failure]}</p>
              {detailState.failure !== "authentication_unavailable" && <button className="secondary-action" type="button" onClick={() => void readCase(detailState.caseRef)}>Consultar novamente</button>}
            </section>
          )}
          {detailState.kind === "ready" && <div ref={(node) => { resultHeading.current = node?.querySelector("h3") ?? null; }}><StaffCaseDetail detail={detailState.detail} /></div>}
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="staff-case-heading">
      <div className="portal-guidance">
        <p className="eyebrow">Casos autorizados</p>
        <h2 id="staff-case-heading">Casos de autorização</h2>
        <p>A lista mostra somente a página liberada para sua sessão atual, sem totais de casos ocultos.</p>
        <button className="secondary-action" type="button" onClick={() => void loadList(null, false)} disabled={listState.kind === "loading"}>Atualizar casos</button>
        <form className="intake-form" onSubmit={(event) => { event.preventDefault(); void readCase(caseRef.trim()); }}>
          <div className="form-grid">
            <label htmlFor={inputId}>Abrir pela referência exata
              <input id={inputId} name="case-reference" value={caseRef} onChange={(event) => setCaseRef(event.target.value)} autoComplete="off" spellCheck={false} required minLength={16} maxLength={128} aria-describedby={`${inputId}-help`} />
            </label>
            <small id={`${inputId}-help`}>A referência permanece somente nesta tela e não é incluída na URL.</small>
          </div>
          <button className="primary-action" type="submit">Consultar caso</button>
        </form>
      </div>
      <div aria-live="polite" aria-atomic="false">
        {listState.kind === "loading" && <p className="queue-message">Consultando seus casos autorizados.</p>}
        {listState.kind === "error" && (
          <section className="queue-message queue-error" aria-labelledby="staff-case-list-error-heading">
            <h3 id="staff-case-list-error-heading" ref={resultHeading} tabIndex={-1}>Não foi possível carregar os casos</h3>
            <p role="alert">{errorMessage[listState.failure]}</p>
            {listState.failure !== "authentication_unavailable" && <button className="secondary-action" type="button" onClick={() => void loadList(null, false)}>Tentar novamente</button>}
          </section>
        )}
        {listState.kind === "ready" && (
          <section aria-labelledby="staff-case-list-heading">
            <div className="section-heading"><div><h3 id="staff-case-list-heading">Página atual</h3></div><p className="freshness">Consultada em {formatTimestamp(listState.page.freshness.observed_at)}</p></div>
            {listState.page.items.length === 0 ? <p>Nenhum caso autorizado nesta página.</p> : (
              <ul className="case-list">
                {listState.page.items.map((item) => <li key={item.case_ref}>
                  <button type="button" className="secondary-action" onClick={() => void readCase(item.case_ref)}>Abrir caso <span className="exact-value">{item.case_ref}</span></button>
                  <span>{item.state === "active" ? "Ativo" : "Encerrado"} · observado em {formatTimestamp(item.state_observed_at)}</span>
                </li>)}
              </ul>
            )}
            <p className="freshness-detail">Lista válida até {formatTimestamp(listState.page.freshness.valid_until)}.</p>
            {listState.page.next_cursor !== null && <button className="secondary-action" type="button" disabled={listState.loadingMore} onClick={() => void loadList(listState.page.next_cursor, true)}>{listState.loadingMore ? "Carregando…" : "Carregar próxima página"}</button>}
          </section>
        )}
      </div>
    </section>
  );
}
