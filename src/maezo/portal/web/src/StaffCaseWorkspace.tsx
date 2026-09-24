import { useCallback, useEffect, useId, useRef, useState } from "react";

import type {
  StaffCaseClient,
  StaffCaseFailure,
  StaffDetail,
  StaffPage,
} from "./staffCaseClient";
import { expiryDelay, isCurrent, retainedCeiling } from "./taskReadTime";

type ListState =
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "ready"; page: StaffPage; loadingMore: boolean }>
  | Readonly<{ kind: "error"; failure: StaffCaseFailure }>;

type DetailState =
  | Readonly<{ kind: "loading"; caseRef: string }>
  | Readonly<{ kind: "ready"; detail: StaffDetail }>
  | Readonly<{ kind: "error"; caseRef: string; failure: StaffCaseFailure }>;

const errorMessage: Readonly<Record<StaffCaseFailure, string>> = {
  invalid_request: "Confira a referência: ela tem de 16 a 128 letras, números, _ ou -.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Sua sessão não tem permissão para ver os casos do seu grupo. Peça o vínculo ao administrador do portal.",
  resource_unavailable: "O caso não foi encontrado ou não está liberado para o seu grupo.",
  conflict: "Os casos mudaram durante a consulta. Atualize a lista.",
  dependency_unavailable: "O serviço de casos está indisponível: fora do ar ou não habilitado neste ambiente. Nenhum caso foi exibido.",
  "invalid-response": "O portal recusou uma resposta inesperada do serviço de casos. Nenhum caso foi exibido.",
};

const outcomeLabel: Readonly<Record<NonNullable<StaffDetail["outcome"]>["desfecho"], string>> = {
  aprovada_automatica: "Aprovada automaticamente",
  aprovada_auditor: "Aprovada pelo auditor",
  negada_auditor: "Negada pelo auditor",
  nao_requer_autorizacao: "Não requer autorização",
  cancelada_pendencia: "Cancelada por pendência",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function StatePill({ state }: { state: "active" | "ended" }) {
  return (
    <span className={`case-state case-state-${state}`}>
      {state === "active" ? "Em andamento" : "Encerrado"}
    </span>
  );
}

// The contract hands every read a closed validity window. This strip is that
// window: it drains until valid_until, when the screen retires the data.
function ValidityWindow({ observedAt, validUntil, label }: {
  observedAt: string;
  validUntil: string;
  label: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const start = Date.parse(observedAt);
  const end = Date.parse(validUntil);
  const total = Math.max(1, end - start);
  const remaining = Math.min(total, Math.max(0, end - now));
  const seconds = Math.ceil(remaining / 1000);
  return (
    <div className="validity-window">
      <p>
        {label} em {formatTimestamp(observedAt)}.{" "}
        <span>Válida por mais {seconds} s; depois o portal consulta de novo.</span>
      </p>
      <span className="validity-track" aria-hidden="true">
        <span className="validity-fill" style={{ transform: `scaleX(${remaining / total})` }} />
      </span>
    </div>
  );
}

function dueLabel(dueAt: string | null, observedAt: string) {
  if (dueAt === null) return { text: "Sem prazo informado", late: false };
  const late = Date.parse(dueAt) < Date.parse(observedAt);
  return { text: formatTimestamp(dueAt), late };
}

function StaffCaseDetail({ detail }: { detail: StaffDetail }) {
  return (
    <section className="staff-case-detail" aria-labelledby="staff-case-detail-heading">
      <div className="case-detail-heading">
        <div>
          <h3 id="staff-case-detail-heading" tabIndex={-1}>Caso de autorização</h3>
          <p className="exact-value case-detail-ref">{detail.case.case_ref}</p>
        </div>
        <StatePill state={detail.case.state} />
      </div>
      <ValidityWindow
        observedAt={detail.freshness.observed_at}
        validUntil={detail.freshness.valid_until}
        label="Consultado"
      />
      {detail.outcome !== null && (
        <p className="case-outcome">
          Desfecho: <strong>{outcomeLabel[detail.outcome.desfecho]}</strong>
          {detail.outcome.authorization_ref !== null && (
            <>, autorização nº <span className="exact-value">{detail.outcome.authorization_ref}</span></>
          )}
        </p>
      )}
      <section className="staff-case-tasks" aria-labelledby="staff-case-tasks-heading">
        <h4 id="staff-case-tasks-heading">Tarefas ativas</h4>
        {detail.active_tasks.length === 0 ? (
          <p>{detail.case.state === "ended" ? "O caso está encerrado e não tem tarefas ativas." : "Nenhuma tarefa ativa neste momento."}</p>
        ) : (
          <div className="table-scroll" tabIndex={0} aria-label="Tabela de tarefas ativas do caso">
            <table>
              <caption className="visually-hidden">Tarefas ativas do caso, com responsável e prazo</caption>
              <thead><tr><th scope="col">Tarefa</th><th scope="col">Prazo</th><th scope="col">Responsável</th><th scope="col">Criada em</th><th scope="col">Revisão</th></tr></thead>
              <tbody>
                {detail.active_tasks.map((task) => {
                  const due = dueLabel(task.due_at, detail.freshness.observed_at);
                  return (
                    <tr key={task.task_id}>
                      <th scope="row">{task.task_definition_key}</th>
                      <td className={due.late ? "due-late" : undefined}>
                        {due.text}{due.late && <strong className="due-flag"> Vencido</strong>}
                      </td>
                      <td>{task.assignee_ref ?? "Sem responsável"}</td>
                      <td>{formatTimestamp(task.created_at)}</td>
                      <td className="exact-value">{task.task_revision}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="case-identity" aria-labelledby="staff-case-identity-heading">
        <h4 id="staff-case-identity-heading">Processo e revisão</h4>
        <dl className="task-facts">
          <div><dt>Processo</dt><dd>{detail.identity.process_definition_key}</dd></div>
          <div><dt>Versão do processo</dt><dd className="exact-value">{detail.identity.process_definition_version}</dd></div>
          <div><dt>Instância do processo</dt><dd className="exact-value">{detail.identity.process_instance_ref}</dd></div>
          <div><dt>Revisão do caso</dt><dd className="exact-value">{detail.case.record_revision}</dd></div>
          <div><dt>Situação observada em</dt><dd>{formatTimestamp(detail.case.state_observed_at)}</dd></div>
          <div><dt>Fonte observada em</dt><dd>{formatTimestamp(detail.freshness.source_observed_at)}</dd></div>
        </dl>
      </section>
      <p className="freshness-detail">Somente leitura. Para agir numa tarefa, abra-a em Meu trabalho ou Filas da equipe.</p>
    </section>
  );
}

function joinedPage(previous: StaffPage, next: StaffPage): StaffPage | null {
  const priorLast = previous.items.at(-1)?.case_ref;
  const nextFirst = next.items[0]?.case_ref;
  if (priorLast !== undefined && nextFirst !== undefined && nextFirst <= priorLast) return null;
  if (!isCurrent(previous.freshness.valid_until) || !isCurrent(next.freshness.valid_until)) return null;
  return {
    ...next,
    items: [...previous.items, ...next.items],
    freshness: {
      ...next.freshness,
      observed_at: retainedCeiling(previous.freshness.observed_at, next.freshness.observed_at),
      source_observed_at: retainedCeiling(previous.freshness.source_observed_at, next.freshness.source_observed_at),
      valid_until: retainedCeiling(previous.freshness.valid_until, next.freshness.valid_until),
    },
  };
}

export function StaffCaseWorkspace({
  service,
  onSessionUnavailable,
  selectedCaseRef,
  onSelectCase,
}: {
  service: StaffCaseClient;
  onSessionUnavailable: () => void;
  /** Routed selection: when onSelectCase is given, the URL owns which case is open. */
  selectedCaseRef?: string | null;
  onSelectCase?: (caseRef: string | null) => void;
}) {
  const inputId = useId();
  const [caseRef, setCaseRef] = useState("");
  const [localSelection, setLocalSelection] = useState<string | null>(null);
  const routed = onSelectCase !== undefined;
  const selected = routed ? selectedCaseRef ?? null : localSelection;
  const select = useCallback((next: string | null) => {
    if (onSelectCase !== undefined) onSelectCase(next);
    else setLocalSelection(next);
  }, [onSelectCase]);
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

  // One effect owns every read: the selection (local or routed) decides
  // whether the list or one case is consulted, and any change aborts the other.
  useEffect(() => {
    if (selected === null) {
      setCaseRef("");
      void loadList(null, false);
    } else {
      void readCase(selected);
    }
    return () => {
      epoch.current += 1;
      request.current?.abort();
      request.current = null;
    };
  }, [loadList, readCase, selected]);

  useEffect(() => {
    if (detailState?.kind === "ready" || detailState?.kind === "error" || listState.kind === "error") resultHeading.current?.focus();
  }, [detailState, listState]);

  useEffect(() => {
    if (detailState?.kind === "ready") {
      const delay = Math.min(detailState.detail.freshness.refresh_after_seconds * 1000, expiryDelay(detailState.detail.freshness.valid_until));
      const timer = window.setTimeout(() => void readCase(detailState.detail.case.case_ref), delay);
      return () => window.clearTimeout(timer);
    }
  }, [detailState, readCase]);

  // The displayed page keeps its retirement timer while pagination is pending.
  // loadingMore changes do not replace the retained page or extend its deadline.
  const visiblePage = selected === null && detailState === null && listState.kind === "ready" ? listState.page : null;
  useEffect(() => {
    if (visiblePage === null) return;
    const delay = Math.min(visiblePage.freshness.refresh_after_seconds * 1000, expiryDelay(visiblePage.freshness.valid_until));
    const timer = window.setTimeout(() => void loadList(null, false), delay);
    return () => window.clearTimeout(timer);
  }, [visiblePage, loadList]);

  if (selected !== null) {
    return (
      <section className="staff-cases" aria-label="Detalhe do caso">
        <button className="secondary-action compact-action" type="button" onClick={() => select(null)}>Voltar para casos</button>
        <div aria-live="polite" aria-atomic="false">
          {(detailState === null || detailState.kind === "loading") && <p className="queue-message">Consultando o caso.</p>}
          {detailState?.kind === "error" && (
            <section className="queue-message queue-error" aria-labelledby="staff-case-error-heading">
              <h2 id="staff-case-error-heading" ref={resultHeading} tabIndex={-1}>Não foi possível consultar o caso</h2>
              <p role="alert">{errorMessage[detailState.failure]}</p>
              {detailState.failure !== "authentication_unavailable" && <button className="secondary-action" type="button" onClick={() => void readCase(detailState.caseRef)}>Consultar novamente</button>}
            </section>
          )}
        </div>
        {detailState?.kind === "ready" && <div ref={(node) => { resultHeading.current = node?.querySelector("h3") ?? null; }}><StaffCaseDetail detail={detailState.detail} /></div>}
      </section>
    );
  }

  return (
    <section className="staff-cases" aria-labelledby="staff-case-heading">
      <div className="case-queue-heading">
        <div>
          <h2 id="staff-case-heading">Casos de autorização</h2>
          <p>Casos liberados para o seu grupo. A lista não mostra totais de casos que você não pode ver.</p>
        </div>
        <button className="secondary-action compact-action" type="button" onClick={() => void loadList(null, false)} disabled={listState.kind === "loading"}>Atualizar casos</button>
      </div>
      <div aria-live="polite" aria-atomic="false">
        {listState.kind === "loading" && <p className="queue-message">Consultando seus casos autorizados.</p>}
        {listState.kind === "error" && (
          <section className="queue-message queue-error" aria-labelledby="staff-case-list-error-heading">
            <h3 id="staff-case-list-error-heading" ref={resultHeading} tabIndex={-1}>
              {listState.failure === "dependency_unavailable" ? "Serviço de casos indisponível" : "Não foi possível carregar os casos"}
            </h3>
            <p role="alert">{errorMessage[listState.failure]}</p>
            {listState.failure !== "authentication_unavailable" && <button className="secondary-action" type="button" onClick={() => void loadList(null, false)}>Tentar novamente</button>}
          </section>
        )}
      </div>
      {listState.kind === "ready" && (
        <section aria-labelledby="staff-case-list-heading">
          <h3 id="staff-case-list-heading" className="visually-hidden">Casos do seu grupo</h3>
          <ValidityWindow
            observedAt={listState.page.freshness.observed_at}
            validUntil={listState.page.freshness.valid_until}
            label="Lista consultada"
          />
          {listState.page.items.length === 0 ? (
            <div className="case-empty">
              <p className="case-empty-title">Nenhum caso para o seu grupo.</p>
              <p>Quando um caso de autorização for liberado para você, ele aparece aqui. A lista se atualiza sozinha.</p>
            </div>
          ) : (
            <div className="table-scroll case-table" tabIndex={0} aria-label="Tabela de casos do seu grupo">
              <table>
                <caption className="visually-hidden">Casos de autorização liberados para o seu grupo</caption>
                <thead><tr><th scope="col">Caso</th><th scope="col">Situação</th><th scope="col">Situação observada em</th><th scope="col">Revisão</th></tr></thead>
                <tbody>
                  {listState.page.items.map((item) => (
                    <tr key={item.case_ref}>
                      <th scope="row">
                        <a
                          className="table-action case-link"
                          href={casePath(item.case_ref)}
                          onClick={(event) => {
                            if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
                            event.preventDefault();
                            select(item.case_ref);
                          }}
                        >
                          <span className="visually-hidden">Abrir caso </span><span className="exact-value">{item.case_ref}</span>
                        </a>
                      </th>
                      <td><StatePill state={item.state} /></td>
                      <td>{formatTimestamp(item.state_observed_at)}</td>
                      <td className="exact-value">{item.record_revision}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {listState.page.next_cursor !== null && <button className="secondary-action" type="button" disabled={listState.loadingMore} onClick={() => void loadList(listState.page.next_cursor, true)}>{listState.loadingMore ? "Carregando…" : "Carregar próxima página"}</button>}
        </section>
      )}
      <form className="case-lookup" onSubmit={(event) => { event.preventDefault(); select(caseRef.trim()); }}>
        <label htmlFor={inputId}>Abrir pela referência exata</label>
        <div className="case-lookup-row">
          <input id={inputId} name="case-reference" value={caseRef} onChange={(event) => setCaseRef(event.target.value)} autoComplete="off" spellCheck={false} required minLength={16} maxLength={128} aria-describedby={`${inputId}-help`} />
          <button className="primary-action" type="submit">Consultar caso</button>
        </div>
        <small id={`${inputId}-help`}>A referência completa tem de 16 a 128 caracteres.</small>
      </form>
    </section>
  );
}

export const caseRoutePrefix = "/portal/cases";

export function casePath(caseRef: string | null) {
  return caseRef === null ? caseRoutePrefix : `${caseRoutePrefix}/${encodeURIComponent(caseRef)}`;
}
