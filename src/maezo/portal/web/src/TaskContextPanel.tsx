import { useEffect, useId, useRef, useState } from "react";

import { readTaskContext, type ContextFailure, type TaskContext } from "./contextClient";

type PanelState =
  | { kind: "loading" }
  | { kind: "ready"; value: TaskContext }
  | { kind: "error"; failure: ContextFailure };

const failureMessages: Readonly<Record<ContextFailure, string>> = {
  invalid_request: "A solicitação foi recusada. Abra a tarefa novamente.",
  session_unavailable: "Sua sessão não está mais disponível.",
  employee_access_required: "Sua autorização atual não permite ver o contexto deste caso.",
  resource_unavailable: "O contexto deste caso não está mais disponível.",
  refresh_required: "A tarefa mudou desde a consulta. Abra a tarefa novamente.",
  context_unavailable: "O contexto do caso está desligado neste ambiente.",
  read_dependency_unavailable: "Não foi possível carregar o contexto agora. O restante da tarefa continua disponível.",
  "invalid-response": "O portal recusou uma resposta inesperada ao carregar o contexto.",
};

// Only these can be retried by asking again; the others are decisions, not accidents.
const retryable: ReadonlySet<ContextFailure> = new Set(["read_dependency_unavailable", "invalid-response"]);

// LABELS live here and VALUES come from the server. A code with no label is shown as itself, so
// a new routing value is visible on the screen the day it exists and never silently vanishes.
const motivoLabels: Readonly<Record<string, string>> = {
  red_flag_clinico: "Sinal de alerta clínico",
  risco_psicossocial: "Risco psicossocial",
  intencao_clinica: "Pergunta ou intenção clínica",
  solicitacao_humano: "Pediu para falar com uma pessoa",
  falha_tecnica: "Falha técnica da assistente",
};
const severidadeLabels: Readonly<Record<string, string>> = { grave: "Grave", moderada: "Moderada", leve: "Leve" };
const grupoLabels: Readonly<Record<string, string>> = {
  "plantao-clinico": "Plantão clínico",
  "enfermagem-triagem": "Enfermagem — triagem",
  "atendimento-humano": "Atendimento humano",
  "supervisao-atendimento": "Supervisão do atendimento",
};

const relative = new Intl.RelativeTimeFormat("pt-BR", { numeric: "auto" });

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
}

// "em 3 minutos" / "há 2 horas". Under a minute it says so instead of printing a number that is
// already stale by the time it is read.
function distance(target: string, now: number): { text: string; past: boolean } {
  const minutes = Math.round((Date.parse(target) - now) / 60_000);
  const past = minutes < 0;
  if (minutes === 0) return { text: "agora", past: false };
  if (Math.abs(minutes) < 60) return { text: relative.format(minutes, "minute"), past };
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return { text: relative.format(hours, "hour"), past };
  return { text: relative.format(Math.round(hours / 24), "day"), past };
}

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

function Deadline({ label, at, now }: { label: string; at: string; now: number }) {
  const { text, past } = distance(at, now);
  return (
    <div>
      <dt>{label}</dt>
      <dd className={past ? "due-late" : undefined}>
        {formatTimestamp(at)} <span className="context-distance">({past ? `venceu ${text}` : `vence ${text}`})</span>
      </dd>
    </div>
  );
}

export function TaskContextPanel({
  taskId,
  sessionBinding,
  onSessionUnavailable,
}: {
  taskId: string;
  sessionBinding: string;
  onSessionUnavailable: () => void;
}) {
  const [state, setState] = useState<PanelState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const headingId = useId();
  const now = useNow(30_000);
  // The panel must not refetch just because the parent rebuilt its callback.
  const notify = useRef(onSessionUnavailable);
  notify.current = onSessionUnavailable;

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    readTaskContext(taskId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result.kind === "success") {
          setState({ kind: "ready", value: result.value });
          return;
        }
        setState({ kind: "error", failure: result.kind });
        if (result.kind === "session_unavailable") notify.current();
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
        // Never stay on "loading": an unexpected break is a failure the person can see and retry.
        setState({ kind: "error", failure: "read_dependency_unavailable" });
      });
    return () => controller.abort();
  }, [taskId, sessionBinding, attempt]);

  return (
    <section className="task-context" aria-labelledby={headingId}>
      <h3 id={headingId}>Contexto do caso</h3>

      {state.kind === "loading" && <p role="status">Carregando o contexto do caso…</p>}

      {state.kind === "error" && (
        <>
          <p role="alert" className="queue-error">{failureMessages[state.failure]}</p>
          {retryable.has(state.failure) && (
            <button className="secondary-action" type="button" onClick={() => setAttempt((n) => n + 1)}>
              Tentar novamente
            </button>
          )}
        </>
      )}

      {state.kind === "ready" && <ContextBody value={state.value} now={now} />}
    </section>
  );
}

function ContextBody({ value, now }: { value: TaskContext; now: number }) {
  const opened = value.aberto_em === null ? null : distance(value.aberto_em, now);
  const summaryId = useId();
  return (
    <>
      <p className="task-context-lead">
        {value.prioridade !== null && (
          <span className={`priority priority-${value.prioridade.toLowerCase()}`}>{value.prioridade}</span>
        )}
        {value.grupo_atendimento !== null && (
          <strong>{grupoLabels[value.grupo_atendimento] ?? value.grupo_atendimento}</strong>
        )}
        {value.etapa === "supervisao" && <strong>Supervisão — última linha</strong>}
      </p>

      {value.etapa === "supervisao" && (
        <p className="read-only-note">
          Este caso passou do prazo de resolução do grupo e chegou à supervisão. Os prazos do grupo não
          se aplicam mais a esta etapa.
        </p>
      )}

      <dl className="task-facts">
        <div>
          <dt>Motivo do encaminhamento</dt>
          <dd>{value.motivo_categoria === null ? "Não informado" : (motivoLabels[value.motivo_categoria] ?? value.motivo_categoria)}</dd>
        </div>
        <div>
          <dt>Gravidade</dt>
          <dd>{value.severidade === null ? "Não informada" : (severidadeLabels[value.severidade] ?? value.severidade)}</dd>
        </div>
        <div>
          <dt>Aberto</dt>
          <dd>
            {value.aberto_em === null || opened === null ? (
              "Não informado"
            ) : (
              <>
                {formatTimestamp(value.aberto_em)} <span className="context-distance">({opened.text})</span>
              </>
            )}
          </dd>
        </div>
        {value.ack_vence_em !== null && <Deadline label="Prazo de ciência" at={value.ack_vence_em} now={now} />}
        {value.resolucao_vence_em !== null && (
          <Deadline label="Prazo de resolução" at={value.resolucao_vence_em} now={now} />
        )}
      </dl>

      <div className="task-context-summary" role="group" aria-labelledby={summaryId}>
        <p id={summaryId} className="eyebrow">Resumo escrito pela Helena para o atendente</p>
        {value.resumo_contexto === null ? (
          <p>A Helena não gerou um resumo para este caso.</p>
        ) : (
          <blockquote>{value.resumo_contexto}</blockquote>
        )}
      </div>

      <p className="read-only-note">
        Nome, telefone e a conversa não aparecem aqui: a Helena não identifica quem escreve e o portal
        não recebe esses dados.
      </p>
    </>
  );
}
