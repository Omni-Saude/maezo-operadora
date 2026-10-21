import { useEffect, useId, useRef, useState } from "react";

import {
  cancelExecucaoOutcomes,
  completeTask,
  completionOutcomes,
  maxNotes,
  type CancelExecucaoOutcome,
  type CompletionFailure,
  type CompletionOutcome,
  type CompletionResponse,
  type CompletionSubmission,
  type ResumptionRefusalGroup,
} from "./completionClient";

type SubmitState =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "completed"; value: CompletionResponse }
  | { kind: "unknown" }
  | { kind: "error"; failure: CompletionFailure; group?: ResumptionRefusalGroup };

const failureMessages: Readonly<Record<CompletionFailure, string>> = {
  invalid_request: "A solicitação foi recusada. Consulte a tarefa novamente.",
  invalid_completion: "O desfecho ou a instrução não correspondem ao contrato da tarefa.",
  session_unavailable: "Sua sessão não está mais disponível.",
  employee_access_required: "Sua autorização atual não permite concluir esta tarefa.",
  resource_unavailable: "A tarefa não está mais disponível.",
  revision_conflict: "A tarefa mudou desde a consulta. Consulte novamente antes de concluir.",
  completion_unavailable: "A conclusão pelo portal está desligada neste ambiente.",
  completion_dependency_unavailable: "Uma dependência não respondeu.",
  // P3 (16/10/2026): a nota cabe no limite, mas passa dele DEPOIS da pseudonimização (telefone,
  // e-mail e CPF viram marcadores mais longos). Nada foi gravado nem enviado.
  notes_too_long:
    "A instrução ficou longa demais depois que telefones, e-mails e documentos foram ocultados. Encurte o texto e envie de novo.",
  // P5 (16/10/2026): a mesma cerca que a Helena aplica antes de enviar barraria a instrução.
  resumption_refused:
    "O agente não enviaria esta instrução ao beneficiário. Reescreva e envie de novo; nada foi gravado nem enviado.",
  "invalid-response": "O portal recusou uma resposta inesperada.",
  "outcome-unknown": "O resultado do envio é desconhecido.",
};

//: P5: por que a instrução seria barrada, por FAMÍLIA da regra (o servidor nunca manda o trecho).
//: Uma família nova chega como `outra_regra` até ganhar texto aqui.
const resumptionRefusalMessages: Readonly<Record<ResumptionRefusalGroup, string>> = {
  negativa_clinica:
    "Ela diz que não é grave ou que a pessoa não precisa de atendimento: isso é avaliação clínica e o agente não repassa.",
  promessa_de_capacidade:
    "Ela promete que o agente vai encaminhar, registrar, emitir ou agendar algo, e o agente não faz isso por mensagem.",
  promessa_sem_start:
    "Ela promete contato ou retorno da equipe (“entraremos em contato”), mas o caso está sendo devolvido e ninguém ficará com ele.",
  canal_nao_confirmado:
    "Ela cita um aplicativo, portal ou canal que não está confirmado, ou um canal para um assunto que ele não resolve.",
  outra_regra: "Ela foi barrada por uma regra de segurança das mensagens do agente.",
};

//: Rótulos de tela. O VALOR vem do contrato (`completionOutcomes`); só o texto mora aqui, e um
//: desfecho novo aparece com o próprio valor até alguém escrever o rótulo — nunca some da tela.
const outcomeLabels: Readonly<Record<string, string>> = {
  resolvido_humano: "Resolvi o caso",
  devolvido_agente: "Devolvo ao agente, com instrução",
  emergencia_acionada: "Acionei emergência",
  // DL-0111: execução do cancelamento a pedido no cadastro.
  EXECUTADO: "Cancelamento executado no sistema",
  IMPEDIDO: "Impedido (volta para a análise do jurídico)",
};

//: DL-0111: o formulário da execução do cancelamento (`cancel_execucao`): um campo fechado, sem nota.
const CANCEL_EXECUCAO = "cancel_execucao";

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

export function TaskCompletionControls({
  taskId,
  formKey = "escalation",
  csrfToken,
  sessionBinding,
  onSessionUnavailable,
  onCompleted,
}: {
  taskId: string;
  formKey?: string;
  csrfToken: string;
  sessionBinding: string;
  onSessionUnavailable: () => void;
  onCompleted?: () => void;
}) {
  const [outcome, setOutcome] = useState<string>("");
  const execucao = formKey === CANCEL_EXECUCAO;
  const options: readonly string[] = execucao ? cancelExecucaoOutcomes : completionOutcomes;
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
  }, [taskId, sessionBinding, formKey]);

  useEffect(() => () => controller.current?.abort(), []);

  const notesTrimmed = notes.trim();
  const ready = execucao
    ? outcome !== ""
    : outcome !== "" && notesTrimmed.length > 0 && notesTrimmed.length <= maxNotes;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || state.kind === "sending" || state.kind === "completed") return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState({ kind: "sending" });

    let result;
    try {
      const submission: CompletionSubmission = execucao
        ? { execucao_cadastro: outcome as CancelExecucaoOutcome }
        : { resultado: outcome as CompletionOutcome, notas_resolucao: notesTrimmed };
      result = await completeTask(taskId, submission, csrfToken, current.signal);
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
    if (result.kind === "resumption_refused") {
      setState({ kind: "error", failure: result.kind, group: result.group });
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
      {execucao ? (
        <p className="read-only-note">
          Execute o cancelamento no sistema antes de concluir. <strong>Cancelamento executado</strong>{" "}
          encerra o pedido e o beneficiário é avisado; <strong>impedido</strong> devolve o caso ao jurídico.
        </p>
      ) : (
        <p className="read-only-note">
          A conclusão encerra a tarefa no engine. Só <strong>devolver ao agente</strong> devolve a
          conversa ao beneficiário; os outros dois desfechos encerram o caso.{" "}
          <span data-testid="aviso-retorno-ao-beneficiario">
            A pessoa foi avisada de que pode receber retorno por este canal: se você encerrar sem devolver
            ao agente, nada é enviado a ela.
          </span>
        </p>
      )}
      <form onSubmit={(event) => void submit(event)}>
        <fieldset disabled={state.kind === "sending"}>
          <legend>Desfecho</legend>
          {options.map((value) => (
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
        {!execucao && (
        <>
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
          Até {maxNotes} caracteres, contados depois de ocultar telefones, e-mails e documentos
          (cada um ocupa mais espaço depois de ocultado).
        </p>
        <p className="field-hint" data-testid="notas-contador" aria-live="polite">
          {notes.length}/{maxNotes}
        </p>
        </>
        )}
        {outcome === "devolvido_agente" && (
          <p className="field-hint" data-testid="devolucao-filtro">
            Por segurança, o sistema <strong>não envia</strong> uma instrução que prometa contato ou
            retorno (“entraremos em contato”), cite valores em R$ ou anuncie cancelamento ou
            suspensão. Nesses casos a pessoa recebe uma mensagem genérica. Escreva o que ela deve
            fazer e onde, por exemplo: “Seu boleto vence dia 10; use a 2ª via no portal do plano.”
          </p>
        )}
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
      {state.kind === "error" && (
        <p role="alert" className="queue-error">
          {failureMessages[state.failure]}
          {state.group !== undefined && <> {resumptionRefusalMessages[state.group]}</>}
        </p>
      )}
    </section>
  );
}
