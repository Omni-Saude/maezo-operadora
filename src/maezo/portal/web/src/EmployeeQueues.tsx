import { useCallback, useEffect, useRef, useState } from "react";

import {
  listTaskQueue,
  readTask,
  type PublicTaskSnapshot,
  type QueueName,
  type ReadErrorCode,
  type TaskQueueItem,
  type TaskQueuePage,
} from "./taskReadClient";

const REFRESH_MS = 10_000;

type FailureKind = ReadErrorCode | "invalid-response";
type QueueState =
  | { kind: "loading" }
  | { kind: "ready"; items: TaskQueueItem[]; page: TaskQueuePage; refreshing: boolean }
  | { kind: "error"; error: FailureKind };
type DetailState =
  | { kind: "none" }
  | { kind: "loading" }
  | { kind: "ready"; task: PublicTaskSnapshot; observedAt: string }
  | { kind: "error"; error: FailureKind };

const ownershipLabels = {
  self: "Minha responsabilidade",
  unassigned: "Sem responsável",
  other: "Outro responsável",
} as const;

const errorMessages: Record<FailureKind, string> = {
  invalid_request: "A solicitação da fila foi recusada. Atualize a fila e tente novamente.",
  session_unavailable: "Sua sessão não está mais disponível.",
  employee_access_required: "Seu acesso de colaborador não autoriza esta fila.",
  resource_unavailable: "O recurso solicitado não está mais disponível.",
  refresh_required: "A fila mudou. Atualize para consultar o estado atual.",
  read_dependency_unavailable: "A fila está temporariamente indisponível.",
  "invalid-response": "O portal recusou uma resposta inesperada da fila.",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function QueueError({ error, retry }: { error: FailureKind; retry: () => void }) {
  return (
    <section className="queue-message queue-error" aria-labelledby="queue-error-heading">
      <h3 id="queue-error-heading">Não foi possível mostrar esta fila</h3>
      <p role="alert">{errorMessages[error]}</p>
      {error !== "session_unavailable" && (
        <button className="secondary-action" type="button" onClick={retry}>
          {error === "refresh_required" ? "Atualizar fila" : "Tentar novamente"}
        </button>
      )}
    </section>
  );
}

function QueueTable({
  items,
  onRead,
}: {
  items: TaskQueueItem[];
  onRead: (taskId: string) => void;
}) {
  if (items.length === 0) {
    return <p className="queue-message">Nenhuma tarefa elegível nesta consulta.</p>;
  }
  return (
    <div className="table-scroll" tabIndex={0} aria-label="Tabela de tarefas, com rolagem horizontal">
      <table>
        <caption className="visually-hidden">Tarefas elegíveis da fila selecionada</caption>
        <thead>
          <tr>
            <th scope="col">Tarefa</th>
            <th scope="col">Processo</th>
            <th scope="col">Responsabilidade</th>
            <th scope="col">Prazo</th>
            <th scope="col">Revisão</th>
            <th scope="col"><span className="visually-hidden">Abrir tarefa</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.task_id}>
              <th scope="row">{item.task_definition_key}</th>
              <td>{item.process_definition_key}</td>
              <td>{ownershipLabels[item.ownership]}</td>
              <td>{item.engine_due_at === null ? "Sem prazo informado" : formatTimestamp(item.engine_due_at)}</td>
              <td className="exact-value">{item.task_revision}</td>
              <td>
                <button
                  className="table-action"
                  type="button"
                  onClick={() => onRead(item.task_id)}
                  aria-label={`Abrir detalhes de ${item.task_definition_key}`}
                >
                  Ver detalhes
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BooleanValue({ value }: { value: boolean }) {
  return <>{value ? "Sim" : "Não"}</>;
}

function TaskDetail({ state, refreshQueue }: { state: DetailState; refreshQueue: () => void }) {
  if (state.kind === "none") return null;
  if (state.kind === "loading") {
    return (
      <section className="task-detail" aria-live="polite">
        <h2>Carregando detalhes</h2>
        <p>Consultando o snapshot autorizado da tarefa.</p>
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section className="task-detail queue-error" aria-labelledby="detail-error-heading">
        <h2 id="detail-error-heading">Não foi possível abrir a tarefa</h2>
        <p role="alert">{errorMessages[state.error]}</p>
        {state.error === "refresh_required" && (
          <button className="secondary-action" type="button" onClick={refreshQueue}>
            Atualizar fila
          </button>
        )}
      </section>
    );
  }

  const task = state.task;
  const evidence = task.read_only_evidence;
  return (
    <section className="task-detail" aria-labelledby="task-detail-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Snapshot somente leitura</p>
          <h2 id="task-detail-heading">{task.task_definition_key}</h2>
        </div>
        <p className="freshness">Consultado em {formatTimestamp(state.observedAt)}</p>
      </div>
      <p className="read-only-note">
        Este recorte não oferece claim, release nem decisão. As ações humanas dependem das interfaces
        de mutação qualificadas.
      </p>
      <dl className="task-facts">
        <div><dt>Processo</dt><dd>{task.process_definition_key}</dd></div>
        <div><dt>Versão do processo</dt><dd className="exact-value">{task.process_definition_version}</dd></div>
        <div><dt>Formulário</dt><dd>{task.form_key}</dd></div>
        <div><dt>Versão do formulário</dt><dd className="exact-value">{task.form_version}</dd></div>
        <div><dt>Revisão da tarefa</dt><dd className="exact-value">{task.task_revision}</dd></div>
        <div><dt>Revisão da evidência</dt><dd className="exact-value">{task.evidence_revision}</dd></div>
        <div><dt>Responsável atual</dt><dd>{task.assignee_ref ?? "Sem responsável"}</dd></div>
        <div><dt>Prazo do engine</dt><dd>{task.engine_due_at === null ? "Sem prazo informado" : formatTimestamp(task.engine_due_at)}</dd></div>
      </dl>
      {evidence !== null && (
        <section className="evidence-card" aria-labelledby="evidence-heading">
          <h3 id="evidence-heading">Evidência de admissibilidade</h3>
          <p>A evidência é informativa e não confirma obrigação nem autoriza pagamento.</p>
          <dl className="task-facts">
            <div><dt>Valor em centavos</dt><dd className="exact-value">{evidence.valor_pagamento_cents}</dd></div>
            <div><dt>Dados válidos</dt><dd><BooleanValue value={evidence.dados_pagamento_validos} /></dd></div>
            <div><dt>Lastro confirmado</dt><dd><BooleanValue value={evidence.lastro_confirmado} /></dd></div>
            <div><dt>Duplicidade suspeita</dt><dd><BooleanValue value={evidence.duplicidade_suspeita} /></dd></div>
            <div><dt>Origem do lastro</dt><dd>{evidence.lastro_origem ?? "Não informada"}</dd></div>
            <div><dt>Referência do decisor</dt><dd>{evidence.lastro_decisor_id || "Não informada"}</dd></div>
          </dl>
        </section>
      )}
    </section>
  );
}

export function EmployeeQueues({
  sessionBinding,
  onSessionUnavailable,
}: {
  sessionBinding: string;
  onSessionUnavailable: () => void;
}) {
  const [queue, setQueue] = useState<QueueName>("mine");
  const [queueState, setQueueState] = useState<QueueState>({ kind: "loading" });
  const [detailState, setDetailState] = useState<DetailState>({ kind: "none" });
  const queueEpoch = useRef(0);
  const detailEpoch = useRef(0);
  const queueRequest = useRef<AbortController | null>(null);
  const detailRequest = useRef<AbortController | null>(null);

  const clearForSessionFailure = useCallback(() => {
    queueEpoch.current += 1;
    detailEpoch.current += 1;
    queueRequest.current?.abort();
    detailRequest.current?.abort();
    setQueueState({ kind: "error", error: "session_unavailable" });
    setDetailState({ kind: "none" });
    onSessionUnavailable();
  }, [onSessionUnavailable]);

  const loadFirstPage = useCallback(async () => {
    const epoch = ++queueEpoch.current;
    queueRequest.current?.abort();
    const controller = new AbortController();
    queueRequest.current = controller;
    setQueueState((current) =>
      current.kind === "ready" ? { ...current, refreshing: true } : { kind: "loading" },
    );
    try {
      const result = await listTaskQueue(queue, null, controller.signal);
      if (controller.signal.aborted || epoch !== queueEpoch.current) return;
      queueRequest.current = null;
      if (result.kind === "success") {
        setQueueState({ kind: "ready", items: [...result.value.items], page: result.value, refreshing: false });
      } else if (result.kind === "session_unavailable") {
        clearForSessionFailure();
      } else {
        setDetailState({ kind: "none" });
        setQueueState({ kind: "error", error: result.kind });
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError") && epoch === queueEpoch.current) {
        setDetailState({ kind: "none" });
        setQueueState({ kind: "error", error: "read_dependency_unavailable" });
      }
    }
  }, [clearForSessionFailure, queue]);

  const loadNextPage = useCallback(async () => {
    if (queueState.kind !== "ready" || queueState.page.next_cursor === null) return;
    const cursor = queueState.page.next_cursor;
    const existing = queueState.items;
    const epoch = ++queueEpoch.current;
    queueRequest.current?.abort();
    const controller = new AbortController();
    queueRequest.current = controller;
    setQueueState({ ...queueState, refreshing: true });
    try {
      const result = await listTaskQueue(queue, cursor, controller.signal);
      if (controller.signal.aborted || epoch !== queueEpoch.current) return;
      queueRequest.current = null;
      if (result.kind === "success") {
        const ids = new Set(existing.map((item) => item.task_id));
        if (result.value.items.some((item) => ids.has(item.task_id))) {
          setDetailState({ kind: "none" });
          setQueueState({ kind: "error", error: "invalid-response" });
          return;
        }
        setQueueState({
          kind: "ready",
          items: [...existing, ...result.value.items],
          page: result.value,
          refreshing: false,
        });
      } else if (result.kind === "session_unavailable") {
        clearForSessionFailure();
      } else {
        setDetailState({ kind: "none" });
        setQueueState({ kind: "error", error: result.kind });
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError") && epoch === queueEpoch.current) {
        setDetailState({ kind: "none" });
        setQueueState({ kind: "error", error: "read_dependency_unavailable" });
      }
    }
  }, [clearForSessionFailure, queue, queueState]);

  const openTask = useCallback(
    async (taskId: string) => {
      const epoch = ++detailEpoch.current;
      detailRequest.current?.abort();
      const controller = new AbortController();
      detailRequest.current = controller;
      setDetailState({ kind: "loading" });
      try {
        const result = await readTask(taskId, controller.signal);
        if (controller.signal.aborted || epoch !== detailEpoch.current) return;
        detailRequest.current = null;
        if (result.kind === "success") {
          setDetailState({
            kind: "ready",
            task: result.value.task,
            observedAt: result.value.freshness.observed_at,
          });
        } else if (result.kind === "session_unavailable") {
          clearForSessionFailure();
        } else {
          setDetailState({ kind: "error", error: result.kind });
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError") && epoch === detailEpoch.current) {
          setDetailState({ kind: "error", error: "read_dependency_unavailable" });
        }
      }
    },
    [clearForSessionFailure],
  );

  useEffect(() => {
    setDetailState({ kind: "none" });
    void loadFirstPage();
    return () => {
      queueEpoch.current += 1;
      queueRequest.current?.abort();
    };
  }, [loadFirstPage, sessionBinding]);

  useEffect(() => {
    const timer = window.setInterval(() => void loadFirstPage(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [loadFirstPage, sessionBinding]);

  useEffect(
    () => () => {
      detailEpoch.current += 1;
      detailRequest.current?.abort();
    },
    [sessionBinding],
  );

  const chooseQueue = (next: QueueName) => {
    if (next === queue) return;
    detailEpoch.current += 1;
    detailRequest.current?.abort();
    setDetailState({ kind: "none" });
    setQueue(next);
  };

  return (
    <section className="queue-workspace" id="work" aria-labelledby="queue-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Workspace operacional</p>
          <h2 id="queue-heading">Tarefas elegíveis</h2>
        </div>
        {queueState.kind === "ready" && (
          <p className="freshness" role="status" aria-live="polite">
            {queueState.refreshing ? "Atualizando fila" : `Atualizada em ${formatTimestamp(queueState.page.freshness.observed_at)}`}
          </p>
        )}
      </div>

      <nav className="queue-nav" aria-label="Filas de trabalho">
        <button type="button" aria-current={queue === "mine" ? "page" : undefined} onClick={() => chooseQueue("mine")}>
          Meu trabalho
        </button>
        <button type="button" aria-current={queue === "team" ? "page" : undefined} onClick={() => chooseQueue("team")}>
          Filas da equipe
        </button>
      </nav>

      {queueState.kind === "loading" && <p className="queue-message" role="status">Carregando fila autorizada…</p>}
      {queueState.kind === "error" && <QueueError error={queueState.error} retry={() => void loadFirstPage()} />}
      {queueState.kind === "ready" && (
        <>
          <p className="freshness-detail">
            Fonte observada em {formatTimestamp(queueState.page.freshness.source_observed_at)}. Validade informada até {formatTimestamp(queueState.page.freshness.valid_until)}.
          </p>
          <QueueTable items={queueState.items} onRead={(taskId) => void openTask(taskId)} />
          <div className="queue-actions">
            <button className="secondary-action" type="button" onClick={() => void loadFirstPage()} disabled={queueState.refreshing}>
              Atualizar agora
            </button>
            {queueState.page.next_cursor !== null && (
              <button className="secondary-action" type="button" onClick={() => void loadNextPage()} disabled={queueState.refreshing}>
                Carregar mais
              </button>
            )}
          </div>
        </>
      )}

      <TaskDetail state={detailState} refreshQueue={() => void loadFirstPage()} />
    </section>
  );
}
