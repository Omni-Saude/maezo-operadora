import { useCallback, useEffect, useRef, useState } from "react";

import {
  makeAssignmentSubmission,
  readAssignmentContext,
  readAssignmentCandidates,
  readAssignmentReceipt,
  submitAssignment,
  type AssignmentContext,
  type AssignmentCandidates,
  type AssignmentFailure,
  type AssignmentOperation,
  type AssignmentReceipt,
  type AssignmentSubmission,
} from "./assignmentClient";
import { expiryDelay, isCurrent } from "./taskReadTime";

type ContextState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; value: AssignmentContext }
  | { kind: "expired" }
  | { kind: "error"; failure: AssignmentFailure };

type CommandState =
  | { kind: "none" }
  | { kind: "sending"; submission: AssignmentSubmission }
  | { kind: "pending"; submission: AssignmentSubmission }
  | { kind: "unknown"; submission: AssignmentSubmission }
  | { kind: "receipt"; submission: AssignmentSubmission; receipt: AssignmentReceipt };

const failureMessages: Readonly<Record<AssignmentFailure, string>> = {
  invalid_request: "A solicitação foi recusada. Atualize a autorização da tarefa.",
  invalid_decision: "A operação não corresponde ao contrato atual da tarefa.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Sua autorização atual não permite esta operação.",
  revision_conflict: "A tarefa mudou. Consulte novamente antes de agir.",
  authority_unavailable: "A autorização atual não pôde ser confirmada.",
  task_unavailable: "A tarefa não está mais disponível.",
  form_projection_unavailable: "A projeção atual da tarefa não está disponível.",
  form_contract_unavailable: "O contrato atual da tarefa não está disponível.",
  admission_unavailable: "A admissão não pôde ser confirmada. Consulte o mesmo protocolo.",
  credential_scope_mismatch: "A credencial atual não autoriza esta operação.",
  production_capabilities_unavailable: "A operação está indisponível neste ambiente.",
  dependency_unavailable: "Uma dependência não respondeu. Nenhuma alteração foi confirmada.",
  "invalid-response": "O portal recusou uma resposta inesperada. Nenhuma alteração foi confirmada.",
  "outcome-unknown": "O resultado do envio é desconhecido.",
};

const operationLabels: Readonly<Record<AssignmentOperation, string>> = {
  claim: "Assumir responsabilidade",
  release: "Liberar responsabilidade",
  reassign: "Reatribuir responsabilidade",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

export function TaskOwnershipControls({
  taskId,
  csrfToken,
  sessionBinding,
  onSessionUnavailable,
  onCommitted,
}: {
  taskId: string;
  csrfToken: string;
  sessionBinding: string;
  onSessionUnavailable: () => void;
  onCommitted: () => void;
}) {
  const [context, setContext] = useState<ContextState>({ kind: "idle" });
  const [candidates, setCandidates] = useState<AssignmentCandidates | null>(null);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [targetRef, setTargetRef] = useState("");
  const [command, setCommand] = useState<CommandState>({ kind: "none" });
  const [actionFailure, setActionFailure] = useState<AssignmentFailure | null>(null);
  const requestEpoch = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const reportedCommit = useRef<string | null>(null);
  const submissionIdentity = useRef<AssignmentSubmission | null>(null);
  const contextEpoch = useRef(0);
  const contextRequest = useRef<AbortController | null>(null);

  const replaceRequest = useCallback(() => {
    const epoch = ++requestEpoch.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    return { controller, epoch };
  }, []);

  const currentRequest = useCallback(
    (epoch: number, signal: AbortSignal) =>
      !signal.aborted && epoch === requestEpoch.current,
    [],
  );

  const invalidateSession = useCallback(() => {
    requestEpoch.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    contextEpoch.current += 1;
    contextRequest.current?.abort();
    contextRequest.current = null;
    // Hide unauthorized state immediately, but do not reinterpret a lost session as
    // no effect. Keep the exact private command until this context is replaced.
    setCandidates(null); setTargetRef(""); setLoadingCandidates(false);
    setContext({ kind: "idle" });
    setCommand({ kind: "none" });
    setActionFailure(null);
    onSessionUnavailable();
  }, [onSessionUnavailable]);

  const loadContext = useCallback(async () => {
    // Renew authority independently: this must not abort or replace a submitted command.
    const epoch = ++contextEpoch.current;
    contextRequest.current?.abort();
    const controller = new AbortController();
    contextRequest.current = controller;
    const current = () => !controller.signal.aborted && epoch === contextEpoch.current;
    setCandidates(null); setTargetRef(""); setLoadingCandidates(false);
    setContext({ kind: "loading" });
    if (command.kind === "receipt" && command.receipt.status === "conflict") {
      // Only an authenticated terminal receipt can retire a tracked command here.
      setCommand({ kind: "none" });
      submissionIdentity.current = null;
      reportedCommit.current = null;
    }
    setActionFailure(null);
    try {
      const result = await readAssignmentContext(taskId, controller.signal);
      if (!current()) return;
      contextRequest.current = null;
      if (result.kind === "success") {
        setContext({ kind: "ready", value: result.value });
        if (submissionIdentity.current !== null && command.kind === "none") {
          setCommand({ kind: "unknown", submission: submissionIdentity.current });
        }
      } else if (result.kind === "authentication_unavailable") {
        invalidateSession();
      } else {
        setContext({ kind: "error", failure: result.kind });
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError") && current()) {
        contextRequest.current = null;
        setContext({ kind: "error", failure: "dependency_unavailable" });
      }
    }
  }, [command, invalidateSession, taskId]);

  const loadCandidates = useCallback(async () => {
    if (context.kind !== "ready" || !isCurrent(context.value.valid_until) ||
        !context.value.allowed_operations.includes("reassign") || submissionIdentity.current !== null) return;
    const basis = context.value;
    const epoch = ++contextEpoch.current;
    contextRequest.current?.abort();
    const controller = new AbortController(); contextRequest.current = controller;
    const current = () => !controller.signal.aborted && epoch === contextEpoch.current;
    setCandidates(null); setTargetRef(""); setLoadingCandidates(true); setActionFailure(null);
    try {
      const result = await readAssignmentCandidates(basis, controller.signal);
      if (!current()) return;
      if (!isCurrent(basis.valid_until)) { setContext({ kind: "expired" }); return; }
      if (result.kind === "success") setCandidates(result.value);
      else if (result.kind === "authentication_unavailable") invalidateSession();
      else setActionFailure(result.kind);
    } catch (error) {
      if (current() && !(error instanceof DOMException && error.name === "AbortError")) setActionFailure("dependency_unavailable");
    } finally {
      if (current()) { contextRequest.current = null; setLoadingCandidates(false); }
    }
  }, [context, invalidateSession]);

  const send = useCallback(
    async (submission: AssignmentSubmission) => {
      const { controller, epoch } = replaceRequest();
      submissionIdentity.current = submission;
      setCommand({ kind: "sending", submission });
      setActionFailure(null);
      try {
        const result = await submitAssignment(submission, csrfToken, controller.signal);
        if (!currentRequest(epoch, controller.signal)) return;
        activeRequest.current = null;
        if (result.kind === "success") {
          setCommand({ kind: "pending", submission });
        } else if (result.kind === "authentication_unavailable") {
          invalidateSession();
        } else if (result.kind === "outcome-unknown") {
          setCommand({ kind: "unknown", submission });
        } else {
          // The response carries no pre-admission stage proof. Keep the first command
          // on every possible-effect refusal, as well as every refused retry.
          setCommand({ kind: "unknown", submission });
          setActionFailure(result.kind);
          if (["revision_conflict", "authority_unavailable", "operation_forbidden"].includes(result.kind)) {
            setContext({ kind: "expired" });
          }
        }
      } catch (error) {
        if (
          !(error instanceof DOMException && error.name === "AbortError") &&
          currentRequest(epoch, controller.signal)
        ) {
          activeRequest.current = null;
          setCommand({ kind: "unknown", submission });
        }
      }
    },
    [csrfToken, currentRequest, invalidateSession, replaceRequest],
  );

  const startOperation = useCallback(
    (operation: AssignmentOperation) => {
      if (submissionIdentity.current !== null) return;
      if (context.kind !== "ready" || !isCurrent(context.value.valid_until)) {
        setContext({ kind: "expired" });
        setActionFailure("revision_conflict");
        return;
      }
      const submission = makeAssignmentSubmission(
        context.value,
        operation,
        crypto.randomUUID(),
        operation === "reassign" ? candidates ?? undefined : undefined,
        operation === "reassign" ? targetRef : undefined,
      );
      if (submission === null) {
        setContext({ kind: "expired" });
        setActionFailure("invalid_request");
        return;
      }
      void send(submission);
    },
    [context, candidates, targetRef, send],
  );

  const consult = useCallback(
    async (receiptEndpoint: boolean) => {
      if (command.kind === "none") return;
      const submission = command.submission;
      const { command_id: commandId } = submission.command;
      const { controller, epoch } = replaceRequest();
      setActionFailure(null);
      try {
        const result = await readAssignmentReceipt(
          taskId,
          commandId,
          controller.signal,
          receiptEndpoint,
          submission,
        );
        if (!currentRequest(epoch, controller.signal)) return;
        activeRequest.current = null;
        if (result.kind === "success") {
          setCommand({ kind: "receipt", submission, receipt: result.value });
        } else if (result.kind === "authentication_unavailable") {
          invalidateSession();
        } else {
          setActionFailure(result.kind);
        }
      } catch (error) {
        if (
          !(error instanceof DOMException && error.name === "AbortError") &&
          currentRequest(epoch, controller.signal)
        ) {
          activeRequest.current = null;
          setActionFailure("dependency_unavailable");
        }
      }
    },
    [command, currentRequest, invalidateSession, onCommitted, replaceRequest, taskId],
  );

  useEffect(() => {
    requestEpoch.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    contextEpoch.current += 1;
    contextRequest.current?.abort();
    contextRequest.current = null;
    submissionIdentity.current = null;
    setCandidates(null); setTargetRef(""); setLoadingCandidates(false);
    setContext({ kind: "idle" });
    setCommand({ kind: "none" });
    setActionFailure(null);
    reportedCommit.current = null;
    return () => {
      requestEpoch.current += 1;
      activeRequest.current?.abort();
      activeRequest.current = null;
      contextEpoch.current += 1;
      contextRequest.current?.abort();
      contextRequest.current = null;
    };
  }, [sessionBinding, taskId]);

  useEffect(() => {
    if (context.kind !== "ready") return;
    const timer = window.setTimeout(
      () => setContext({ kind: "expired" }),
      expiryDelay(context.value.valid_until),
    );
    return () => window.clearTimeout(timer);
  }, [context]);

  useEffect(() => {
    if (candidates === null) return;
    const timer = window.setTimeout(() => { setCandidates(null); setTargetRef(""); }, expiryDelay(candidates.valid_until));
    return () => window.clearTimeout(timer);
  }, [candidates]);

  const busy = context.kind === "loading" || command.kind === "sending" || loadingCandidates;
  const receipt = command.kind === "receipt" ? command.receipt : null;

  return (
    <section className="ownership-controls" aria-labelledby="ownership-heading" aria-busy={busy}>
      <h3 id="ownership-heading">Responsabilidade pela tarefa</h3>
      {context.kind === "idle" && (
        <>
          <p>Consulte a autorização atual antes de alterar a responsabilidade desta tarefa.</p>
          <button className="secondary-action" type="button" onClick={() => void loadContext()}>
            Consultar responsabilidade
          </button>
        </>
      )}
      {context.kind === "loading" && <p role="status">Consultando autorização atual…</p>}
      {context.kind === "expired" && (
        <div className="ownership-message ownership-warning">
          <p role="status">A autorização precisa ser consultada novamente antes de outra operação.</p>
          <button className="secondary-action" type="button" onClick={() => void loadContext()}>
            Atualizar autorização
          </button>
        </div>
      )}
      {context.kind === "error" && (
        <div className="ownership-message ownership-error">
          <p role="alert">{failureMessages[context.failure]}</p>
          {context.failure !== "authentication_unavailable" && (
            <button className="secondary-action" type="button" onClick={() => void loadContext()}>
              Tentar novamente
            </button>
          )}
        </div>
      )}
      {context.kind === "ready" && (
        <>
          <p>
            Autorização válida até {formatTimestamp(context.value.valid_until)}. As operações abaixo
            foram determinadas pelo servidor para esta tarefa.
          </p>
          {context.value.allowed_operations.includes("reassign") && (
            <div>
              <button className="secondary-action" type="button" disabled={busy || command.kind !== "none"}
                onClick={() => void loadCandidates()}>Consultar destinatários autorizados</button>
              {loadingCandidates && <p role="status">Consultando destinatários autorizados…</p>}
              {candidates !== null && (candidates.candidates.length === 0
                ? <p role="status">Nenhum destinatário está autorizado nesta consulta.</p>
                : <label>Destinatário autorizado
                    <select value={targetRef} disabled={busy || command.kind !== "none"}
                      onChange={(event) => setTargetRef(event.target.value)}>
                      <option value="">Selecione um destinatário</option>
                      {candidates.candidates.map((item) => <option key={item.target_ref} value={item.target_ref}>{item.target_ref}</option>)}
                    </select>
                  </label>)}
            </div>
          )}
          {context.value.allowed_operations.length === 0 ? (
            <p role="status">Nenhuma mudança de responsabilidade está autorizada agora.</p>
          ) : (
            <div className="queue-actions">
              {context.value.allowed_operations.map((operation) => (
                <button
                  className={operation === "claim" ? "primary-action" : "secondary-action"}
                  disabled={busy || command.kind !== "none" || (operation === "reassign" && (candidates === null || !targetRef))}
                  key={operation}
                  type="button"
                  onClick={() => startOperation(operation)}
                >
                  {operationLabels[operation]}
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {actionFailure !== null && <p className="ownership-error" role="alert">{failureMessages[actionFailure]}</p>}

      {command.kind !== "none" && (
        <section className="ownership-command" aria-labelledby="ownership-command-heading">
          <h4 id="ownership-command-heading">Acompanhamento do comando</h4>
          <p className="exact-value">Protocolo: {command.submission.command.command_id}</p>
          <p role="status" aria-live="polite">
            {command.kind === "sending"
              ? "Enviando solicitação. O recebimento ainda não foi confirmado."
              : receipt?.status === "committed"
                ? receipt.assignment_disposition === "unchanged"
                  ? "Responsabilidade confirmada sem alteração pelo recibo do engine e pela auditoria."
                  : "Alteração executada e confirmada pelo recibo do engine e pela auditoria."
                : receipt?.status === "conflict"
                  ? "Comando encerrado em conflito. Nenhuma alteração foi confirmada por este recibo."
                  : command.kind === "unknown"
                    ? "Resultado desconhecido. O comando pode ter sido recebido; consulte o mesmo protocolo."
                    : "Solicitação recebida e pendente. A alteração ainda não foi confirmada."}
          </p>
          {receipt?.status === "committed" && (
            <dl className="ownership-proof">
              <div>
                <dt>Revisão consumida</dt>
                <dd className="exact-value">{receipt.consumed_task_revision}</dd>
              </div>
              <div>
                <dt>Revisão resultante</dt>
                <dd className="exact-value">{receipt.resulting_task_revision}</dd>
              </div>
            </dl>
          )}
          <div className="queue-actions">
            <button className="secondary-action" disabled={busy} type="button" onClick={() => void consult(false)}>
              Consultar comando
            </button>
            <button className="secondary-action" disabled={busy} type="button" onClick={() => void consult(true)}>
              Consultar recibo
            </button>
            {command.kind === "unknown" && (
              <button className="secondary-action" disabled={busy} type="button" onClick={() => void send(command.submission)}>
                Reenviar o mesmo comando
              </button>
            )}
            {receipt?.status === "conflict" && (
              <button className="secondary-action" disabled={busy} type="button" onClick={() => void loadContext()}>
                Atualizar autorização
              </button>
            )}
            {receipt?.status === "committed" && (
              <button
                className="secondary-action"
                disabled={reportedCommit.current === command.submission.command.command_id}
                type="button"
                onClick={() => {
                  reportedCommit.current = command.submission.command.command_id;
                  onCommitted();
                }}
              >
                Atualizar fila
              </button>
            )}
          </div>
          <p>Fechar a tela não cancela o comando.</p>
        </section>
      )}
    </section>
  );
}
