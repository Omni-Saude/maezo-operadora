import { useEffect, useId, useRef, useState } from "react";

import {
  completeTask,
  completionOutcomes,
  maxNotes,
  type CompletionFailure,
  type CompletionOutcome,
  type CompletionResponse,
} from "./completionClient";

type SubmitState =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "completed"; value: CompletionResponse }
  | { kind: "unknown" }
  | { kind: "error"; failure: CompletionFailure };

const failureMessages: Readonly<Record<CompletionFailure, string>> = {
  invalid_request: "A solicitação foi recusada. Consulte a tarefa novamente.",
  invalid_completion: "O desfecho ou a instrução não correspondem ao contrato da tarefa.",
  session_unavailable: "Sua sessão não está mais disponível.",
  employee_access_required: "Sua autorização atual não permite concluir esta tarefa.",
  resource_unavailable: "A tarefa não está mais disponível.",
  revision_conflict: "A tarefa mudou desde a consulta. Consulte novamente antes de concluir.",
  completion_unavailable: "A conclusão pelo portal está desligada neste ambiente.",
  completion_dependency_unavailable: "Uma dependência não respondeu.",
  "invalid-response": "O portal recusou uma resposta inesperada.",
  "outcome-unknown": "O resultado do envio é desconhecido.",
};

//: Rótulos de tela. O VALOR vem do contrato (`completionOutcomes`); só o texto mora aqui, e um
//: desfecho novo aparece com o próprio valor até alguém escrever o rótulo — nunca some da tela.
const outcomeLabels: Readonly<Record<string, string>> = {
  resolvido_humano: "Resolvi o caso",
  devolvido_agente: "Devolvo ao agente, com instrução",
  emergencia_acionada: "Acionei emergência",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

export function TaskCompletionControls({
  taskId,
  csrfToken,
  sessionBinding,
  onSessionUnavailable,
  onCompleted,
}: {
  taskId: string;
  csrfToken: string;
  sessionBinding: string;
  onSessionUnavailable: () => void;
  onCompleted?: () => void;
}) {
  const [outcome, setOutcome] = useState<CompletionOutcome | "">("");
  const [notes, setNotes] = useState("");
  const [state, setState] = useState<SubmitState>({ kind: "idle" });
  const controller = useRef<AbortController | null>(null);
  const fieldId = useId();

  // A troca de tarefa ou de sessão descarta o rascunho: um desfecho digitado para uma tarefa
  // nunca pode ser enviado para outra.
  useEffect(() => {
    setOutcome("");
    setNotes("");
    setState({ kind: "idle" });
  }, [taskId, sessionBinding]);

  useEffect(() => () => controller.current?.abort(), []);

  const notesTrimmed = notes.trim();
  const ready = outcome !== "" && notesTrimmed.length > 0 && notesTrimmed.length <= maxNotes;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || state.kind === "sending" || state.kind === "completed") return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState({ kind: "sending" });

    let result;
    try {
      result = await completeTask(
        taskId,
        { resultado: outcome as CompletionOutcome, notas_resolucao: notesTrimmed },
        csrfToken,
        current.signal,
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      // Relancar aqui deixava o formulario preso em "enviando" para sempre: o campo e o
      // botao ficam desabilitados e a tela nao volta sozinha. E a falha tambem nao prova
      // que a tarefa continuou aberta, entao o desfecho honesto e DESCONHECIDO.
      setState({ kind: "unknown" });
      return;
    }
    if (current.signal.aborted) return;

    if (result.kind === "success") {
      setState({ kind: "completed", value: result.value });
      onCompleted?.();
      return;
    }
    if (result.kind === "session_unavailable") {
      setState({ kind: "error", failure: result.kind });
      onSessionUnavailable();
      return;
    }
    setState(result.kind === "outcome-unknown" ? { kind: "unknown" } : { kind: "error", failure: result.kind });
  }

  if (state.kind === "completed") {
    const value = state.value;
    return (
      <section className="task-completion" aria-labelledby={`${fieldId}-done`}>
        <h3 id={`${fieldId}-done`}>Tarefa concluída</h3>
        <p role="status">
          Desfecho registrado: <strong>{outcomeLabels[value.resultado] ?? value.resultado}</strong>.
        </p>
        <dl className="task-facts">
          <div><dt>Concluída em</dt><dd>{formatTimestamp(value.completed_at)}</dd></div>
          <div><dt>Revisão consumida</dt><dd className="exact-value">{value.consumed_task_revision}</dd></div>
          <div><dt>Registro de intenção</dt><dd className="exact-value">{value.audit_intent_ref}</dd></div>
          <div><dt>Registro de resultado</dt><dd className="exact-value">{value.audit_result_ref}</dd></div>
        </dl>
        <p className="read-only-note">
          Os dois registros são elos da mesma cadeia de auditoria do tenant — é por eles que a
          conclusão pelo portal e pela via do engine podem ser comparadas.
        </p>
      </section>
    );
  }

  return (
    <section className="task-completion" aria-labelledby={`${fieldId}-heading`}>
      <h3 id={`${fieldId}-heading`}>Concluir a tarefa</h3>
      <p className="read-only-note">
        A conclusão encerra a tarefa no engine. Só <strong>devolver ao agente</strong> devolve a
        conversa ao beneficiário; os outros dois desfechos encerram o caso.
      </p>
      <form onSubmit={(event) => void submit(event)}>
        <fieldset disabled={state.kind === "sending"}>
          <legend>Desfecho</legend>
          {completionOutcomes.map((value) => (
            <label key={value} htmlFor={`${fieldId}-${value}`}>
              <input
                id={`${fieldId}-${value}`}
                type="radio"
                name={`${fieldId}-outcome`}
                value={value}
                checked={outcome === value}
                onChange={() => setOutcome(value)}
              />
              {outcomeLabels[value] ?? value}
            </label>
          ))}
        </fieldset>
        <label htmlFor={`${fieldId}-notes`}>
          Instrução / notas de resolução
          <textarea
            id={`${fieldId}-notes`}
            rows={4}
            value={notes}
            maxLength={maxNotes}
            required
            disabled={state.kind === "sending"}
            onChange={(event) => setNotes(event.target.value)}
          />
        </label>
        <p className="field-hint">
          Obrigatória. Em “devolvo ao agente”, é o texto que o agente repassa ao beneficiário.
          Até {maxNotes} caracteres.
        </p>
        <button className="primary-action" type="submit" disabled={!ready || state.kind === "sending"}>
          {state.kind === "sending" ? "Concluindo…" : "Concluir tarefa"}
        </button>
      </form>
      {state.kind === "unknown" && (
        <p role="alert" className="queue-error">
          {failureMessages["outcome-unknown"]} A tarefa pode ter sido concluída. Consulte a fila
          antes de enviar de novo — reenviar pode registrar um segundo desfecho.
        </p>
      )}
      {state.kind === "error" && <p role="alert" className="queue-error">{failureMessages[state.failure]}</p>}
    </section>
  );
}
