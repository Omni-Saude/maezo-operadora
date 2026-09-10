import { useCallback, useEffect, useId, useRef, useState } from "react";

import type {
  AuthorizationRecoveryPageView,
  CaseExperienceFailure,
  ProviderAuthorizationService,
} from "./caseExperienceModels";

type PageState =
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "failure"; failure: CaseExperienceFailure }>
  | Readonly<{
      kind: "ready";
      page: AuthorizationRecoveryPageView;
      loadingMore: boolean;
    }>;

const failureMessages: Readonly<Record<CaseExperienceFailure, string>> = {
  "session-unavailable": "Sua sessão não está mais disponível.",
  "access-revoked": "Seu vínculo atual não autoriza esta consulta.",
  "resource-unavailable": "A recuperação de solicitações não está disponível agora.",
  "refresh-required": "As informações mudaram. Faça uma nova consulta.",
  "dependency-unavailable": "Uma dependência não respondeu. Tente novamente.",
  "outcome-unknown": "Não foi possível confirmar o resultado desta consulta.",
  "invalid-response": "O portal recusou uma resposta inesperada.",
};

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

type Props = Readonly<{
  service: ProviderAuthorizationService;
  onSessionUnavailable: () => void;
  onOpenCase?: (caseRef: string) => void;
}>;

export function IntakeRecoveryPanel(props: Props) {
  const [context, setContext] = useState({ service: props.service, generation: 0 });
  if (context.service !== props.service) {
    setContext({ service: props.service, generation: context.generation + 1 });
    return null;
  }
  return <IntakeRecoveryContext {...props} key={context.generation} />;
}

function IntakeRecoveryContext({
  service,
  onSessionUnavailable,
  onOpenCase,
}: Props) {
  const headingId = useId();
  const controller = useRef<AbortController | null>(null);
  const [state, setState] = useState<PageState>({ kind: "loading" });

  const handleFailure = useCallback((failure: CaseExperienceFailure) => {
    setState({ kind: "failure", failure });
    if (failure === "session-unavailable") onSessionUnavailable();
  }, [onSessionUnavailable]);

  const loadFirstPage = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setState({ kind: "loading" });
    try {
      const result = await service.discoverAuthorizationIntakes(request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        handleFailure(result.failure);
        return;
      }
      setState({ kind: "ready", page: result.value, loadingMore: false });
    } catch (error) {
      if (!request.signal.aborted && !isAbort(error)) {
        setState({ kind: "failure", failure: "dependency-unavailable" });
      }
    }
  }, [handleFailure, service]);

  useEffect(() => {
    void loadFirstPage();
    return () => controller.current?.abort();
  }, [loadFirstPage]);

  const loadMore = useCallback(async () => {
    if (state.kind !== "ready" || state.page.nextCursor === null) return;
    const current = state.page;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setState({ kind: "ready", page: current, loadingMore: true });
    try {
      const result = await service.discoverAuthorizationIntakes(
        request.signal,
        current.nextCursor ?? undefined,
      );
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        handleFailure(result.failure);
        return;
      }
      const items = [...current.items, ...result.value.items];
      const commands = items.map((item) => item.commandId);
      const intakes = items.map((item) => item.progress.intakeRef);
      if (commands.length !== new Set(commands).size || intakes.length !== new Set(intakes).size) {
        setState({ kind: "failure", failure: "invalid-response" });
        return;
      }
      setState({
        kind: "ready",
        loadingMore: false,
        page: { items, nextCursor: result.value.nextCursor },
      });
    } catch (error) {
      if (!request.signal.aborted && !isAbort(error)) {
        setState({ kind: "failure", failure: "dependency-unavailable" });
      }
    }
  }, [handleFailure, service, state]);

  return (
    <section className="workspace-section intake-recovery" aria-labelledby={headingId}>
      <p className="eyebrow">Recuperação da sessão atual</p>
      <h2 id={headingId}>Solicitações já recebidas</h2>
      <p>
        Consulte admissões que o servidor localizou para seu vínculo atual. Cada referência é
        revalidada antes de mostrar o acompanhamento.
      </p>
      <p className="form-notice">
        Esta consulta não reenvia solicitações e não comprova ausência de efeito para um envio incerto.
      </p>
      {state.kind === "loading" && (
        <p className="resource-message" role="status">Consultando admissões autorizadas…</p>
      )}
      {state.kind === "failure" && (
        <div className="resource-message resource-error">
          <p role="alert">{failureMessages[state.failure]}</p>
          {state.failure !== "session-unavailable" && (
            <button className="secondary-action compact-action" type="button" onClick={() => void loadFirstPage()}>
              Consultar novamente
            </button>
          )}
        </div>
      )}
      {state.kind === "ready" && (
        <>
          <p className="freshness" role="status">Consulta atual concluída.</p>
          {state.page.items.length === 0 ? (
            <p className="resource-message">
              Nenhuma admissão foi observada nesta consulta. Um envio incerto ainda pode ser
              confirmado em uma consulta futura.
            </p>
          ) : (
            <ol className="command-list">
              {state.page.items.map(({ commandId, progress }) => (
                <li className={`command-card intake-${progress.state}`} key={commandId}>
                  <div className="command-heading">
                    <div>
                      <h3>{progress.stateLabel}</h3>
                      <p>{progress.description}</p>
                    </div>
                    <span className="status-chip">{progress.stateLabel}</span>
                  </div>
                  <dl className="receipt-facts">
                    <div><dt>Comando original</dt><dd className="exact-value">{commandId}</dd></div>
                    <div><dt>Solicitação</dt><dd className="exact-value">{progress.intakeRef}</dd></div>
                    {progress.revision && (
                      <div><dt>Revisão</dt><dd className="exact-value">{progress.revision}</dd></div>
                    )}
                    {progress.state === "started" && (
                      <>
                        <div><dt>Caso iniciado</dt><dd className="exact-value">{progress.caseRef}</dd></div>
                        <div><dt>Recibo de início</dt><dd className="exact-value">{progress.startReceiptRef}</dd></div>
                      </>
                    )}
                  </dl>
                  {progress.state === "started" && onOpenCase && (
                    <button
                      className="secondary-action compact-action"
                      type="button"
                      onClick={() => onOpenCase(progress.caseRef)}
                    >
                      Abrir caso iniciado
                    </button>
                  )}
                </li>
              ))}
            </ol>
          )}
          <div className="action-row">
            <button className="secondary-action" type="button" onClick={() => void loadFirstPage()}>
              Fazer nova consulta
            </button>
            {state.page.nextCursor !== null && (
              <button
                className="secondary-action"
                type="button"
                disabled={state.loadingMore}
                onClick={() => void loadMore()}
              >
                {state.loadingMore ? "Consultando próxima página…" : "Consultar próxima página"}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
