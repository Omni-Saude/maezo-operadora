import { useCallback, useEffect, useId, useRef, useState } from "react";

import type {
  CaseCommunicationsClient,
  CommunicationPage,
  CommunicationsFailure,
  HistoryPage,
} from "./caseCommunicationsClient";
import { expiryDelay, isCurrent, retainedCeiling } from "./taskReadTime";

type PanelFailure = CommunicationsFailure | "expired";
type ResourcePage<T> =
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "failure"; failure: PanelFailure }>
  | Readonly<{ kind: "ready"; page: T; loadingMore: boolean }>;

type Props = Readonly<{
  caseRef: string;
  client: CaseCommunicationsClient;
  onSessionUnavailable: () => void;
}>;

const failureMessages: Readonly<Record<PanelFailure, string>> = {
  invalid_request: "A referência informada não pode ser consultada.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Seu vínculo atual não autoriza mais este conteúdo.",
  resource_unavailable: "Este caso não está mais disponível.",
  conflict: "A página mudou. Atualize para consultar a versão atual.",
  dependency_unavailable: "Uma dependência não respondeu. Tente novamente.",
  "invalid-response": "O portal recusou uma resposta inesperada.",
  "outcome-unknown": "O resultado do envio é desconhecido.",
  expired: "A validade destas informações terminou. Atualize para continuar.",
};

const senderLabels = {
  staff: "Colaborador",
  beneficiary: "Beneficiário",
  provider: "Prestador",
  system: "Sistema",
} as const;

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function invalidatesContext(failure: CommunicationsFailure) {
  return failure === "authentication_unavailable" || failure === "operation_forbidden" ||
    failure === "resource_unavailable" || failure === "conflict";
}

function ResourceState({
  state,
  loadingLabel,
  retry,
}: Readonly<{
  state: ResourcePage<unknown>;
  loadingLabel: string;
  retry: () => void;
}>) {
  if (state.kind === "ready") return null;
  if (state.kind === "loading") {
    return <p className="resource-message" role="status">{loadingLabel}</p>;
  }
  return (
    <div className="resource-message resource-error">
      <p role="alert">{failureMessages[state.failure]}</p>
      {state.failure !== "authentication_unavailable" && (
        <button className="secondary-action compact-action" type="button" onClick={retry}>
          Atualizar
        </button>
      )}
    </div>
  );
}

function CommunicationsSection({
  state,
  onRetry,
  onLoadMore,
}: Readonly<{
  state: ResourcePage<CommunicationPage>;
  onRetry: () => void;
  onLoadMore: () => void;
}>) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Caixa autorizada do portal</p>
      <h2 id={headingId}>Comunicações disponíveis</h2>
      <p>
        Esta lista confirma somente a disponibilidade nesta caixa. A origem registrada não indica
        se a mensagem foi enviada por você.
      </p>
      <p className="form-notice">
        A leitura do conteúdo e o envio de novas mensagens dependem do serviço PHI separado e não
        estão disponíveis neste painel.
      </p>
      <ResourceState state={state} loadingLabel="Consultando comunicações autorizadas…" retry={onRetry} />
      {state.kind === "ready" && state.page.items.length === 0 && (
        <p className="resource-message">Nenhuma comunicação está disponível nesta caixa.</p>
      )}
      {state.kind === "ready" && state.page.items.length > 0 && (
        <>
          <p className="freshness" role="status">
            Caixa consultada em {formatTimestamp(state.page.observed_at)}
          </p>
          <ol className="communication-list">
            {state.page.items.map((item) => (
              <li key={item.communication_ref}>
                <header>
                  <div>
                    <h3>Comunicação do portal</h3>
                    <p className="event-meta">Origem registrada: {senderLabels[item.sender_kind!]}</p>
                  </div>
                  <time dateTime={item.authored_at!}>{formatTimestamp(item.authored_at!)}</time>
                </header>
                <dl className="receipt-facts">
                  <div>
                    <dt>Referência</dt>
                    <dd className="exact-value">{item.communication_ref}</dd>
                  </div>
                </dl>
                <p className="delivery-status">
                  Disponível nesta caixa em {formatTimestamp(item.inbox_available_at!)}
                </p>
                <p>
                  {item.body_ref == null
                    ? "O conteúdo protegido não foi disponibilizado para este vínculo."
                    : "O conteúdo protegido possui referência autorizada no serviço PHI separado."}
                </p>
              </li>
            ))}
          </ol>
          {state.page.next_cursor !== null && (
            <button
              className="secondary-action"
              type="button"
              disabled={state.loadingMore}
              onClick={onLoadMore}
            >
              {state.loadingMore ? "Carregando comunicações…" : "Carregar mais comunicações"}
            </button>
          )}
        </>
      )}
    </section>
  );
}

function HistorySection({
  state,
  onRetry,
  onLoadMore,
}: Readonly<{
  state: ResourcePage<HistoryPage>;
  onRetry: () => void;
  onLoadMore: () => void;
}>) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Eventos estruturais autorizados</p>
      <h2 id={headingId}>Histórico do portal</h2>
      <p>
        Este histórico limitado reúne comunicações disponibilizadas e recibos indexados. Ele não
        representa o histórico completo do processo ou do prontuário.
      </p>
      <ResourceState state={state} loadingLabel="Consultando eventos autorizados…" retry={onRetry} />
      {state.kind === "ready" && state.page.items.length === 0 && (
        <p className="resource-message">Nenhum evento do portal está disponível para este caso.</p>
      )}
      {state.kind === "ready" && state.page.items.length > 0 && (
        <>
          <p className="freshness" role="status">
            Histórico consultado em {formatTimestamp(state.page.observed_at)}
          </p>
          <ol className="chronology">
            {state.page.items.map((item) => (
              <li key={item.event_ref}>
                <time dateTime={item.occurred_at!}>{formatTimestamp(item.occurred_at!)}</time>
                <div>
                  <h3>
                    {item.kind === "communication_available"
                      ? "Comunicação disponibilizada"
                      : "Recibo de comando indexado"}
                  </h3>
                  <p>Evento registrado no histórico limitado do portal.</p>
                  <dl className="receipt-facts">
                    <div><dt>Sequência</dt><dd className="exact-value">{item.sequence}</dd></div>
                    <div><dt>Evento</dt><dd className="exact-value">{item.event_ref}</dd></div>
                    {item.communication_ref && (
                      <div><dt>Comunicação</dt><dd className="exact-value">{item.communication_ref}</dd></div>
                    )}
                    {item.command_ref && (
                      <div><dt>Comando</dt><dd className="exact-value">{item.command_ref}</dd></div>
                    )}
                    {item.receipt_ref && (
                      <div><dt>Recibo</dt><dd className="exact-value">{item.receipt_ref}</dd></div>
                    )}
                  </dl>
                </div>
              </li>
            ))}
          </ol>
          {state.page.next_cursor !== null && (
            <button
              className="secondary-action"
              type="button"
              disabled={state.loadingMore}
              onClick={onLoadMore}
            >
              {state.loadingMore ? "Carregando eventos…" : "Carregar mais eventos"}
            </button>
          )}
        </>
      )}
    </section>
  );
}

export function CaseCommunicationsPanel(props: Props) {
  const [context, setContext] = useState({
    client: props.client,
    caseRef: props.caseRef,
    generation: 0,
  });
  if (context.client !== props.client || context.caseRef !== props.caseRef) {
    setContext({ client: props.client, caseRef: props.caseRef, generation: context.generation + 1 });
    return null;
  }
  return <CaseCommunicationsContext {...props} key={context.generation} />;
}

function CaseCommunicationsContext({ caseRef, client, onSessionUnavailable }: Props) {
  const [communications, setCommunications] = useState<ResourcePage<CommunicationPage>>({ kind: "loading" });
  const [history, setHistory] = useState<ResourcePage<HistoryPage>>({ kind: "loading" });
  const communicationsController = useRef<AbortController | null>(null);
  const historyController = useRef<AbortController | null>(null);

  const clearContext = useCallback((failure: CommunicationsFailure) => {
    communicationsController.current?.abort();
    historyController.current?.abort();
    setCommunications({ kind: "failure", failure });
    setHistory({ kind: "failure", failure });
    if (failure === "authentication_unavailable") onSessionUnavailable();
  }, [onSessionUnavailable]);

  const loadCommunications = useCallback(async () => {
    communicationsController.current?.abort();
    const request = new AbortController();
    communicationsController.current = request;
    setCommunications({ kind: "loading" });
    try {
      const result = await client.listCommunications(caseRef, request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        if (invalidatesContext(result.failure)) clearContext(result.failure);
        else setCommunications({ kind: "failure", failure: result.failure });
        return;
      }
      if (!isCurrent(result.value.valid_until)) {
        setCommunications({ kind: "failure", failure: "expired" });
        return;
      }
      setCommunications({ kind: "ready", page: result.value, loadingMore: false });
    } catch (error) {
      if (!request.signal.aborted && !(error instanceof DOMException && error.name === "AbortError")) {
        setCommunications({ kind: "failure", failure: "dependency_unavailable" });
      }
    }
  }, [caseRef, clearContext, client]);

  const loadHistory = useCallback(async () => {
    historyController.current?.abort();
    const request = new AbortController();
    historyController.current = request;
    setHistory({ kind: "loading" });
    try {
      const result = await client.listHistory(caseRef, request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        if (invalidatesContext(result.failure)) clearContext(result.failure);
        else setHistory({ kind: "failure", failure: result.failure });
        return;
      }
      if (!isCurrent(result.value.valid_until)) {
        setHistory({ kind: "failure", failure: "expired" });
        return;
      }
      setHistory({ kind: "ready", page: result.value, loadingMore: false });
    } catch (error) {
      if (!request.signal.aborted && !(error instanceof DOMException && error.name === "AbortError")) {
        setHistory({ kind: "failure", failure: "dependency_unavailable" });
      }
    }
  }, [caseRef, clearContext, client]);

  useEffect(() => {
    void loadCommunications();
    void loadHistory();
    return () => {
      communicationsController.current?.abort();
      historyController.current?.abort();
    };
  }, [loadCommunications, loadHistory]);

  const earliest = [
    communications.kind === "ready" ? communications.page.valid_until : null,
    history.kind === "ready" ? history.page.valid_until : null,
  ].filter((value): value is string => value !== null)
    .reduce<string | null>((left, right) => left === null ? right : retainedCeiling(left, right), null);

  useEffect(() => {
    if (earliest === null) return;
    const timer = window.setTimeout(() => {
      communicationsController.current?.abort();
      historyController.current?.abort();
      setCommunications({ kind: "failure", failure: "expired" });
      setHistory({ kind: "failure", failure: "expired" });
    }, expiryDelay(earliest));
    return () => window.clearTimeout(timer);
  }, [earliest]);

  const loadMoreCommunications = useCallback(async () => {
    if (communications.kind !== "ready" || communications.page.next_cursor === null) return;
    const current = communications.page;
    communicationsController.current?.abort();
    const request = new AbortController();
    communicationsController.current = request;
    setCommunications({ kind: "ready", page: current, loadingMore: true });
    try {
      const result = await client.listCommunications(
        caseRef,
        request.signal,
        current.next_cursor ?? undefined,
      );
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        if (invalidatesContext(result.failure)) clearContext(result.failure);
        else setCommunications({ kind: "failure", failure: result.failure });
        return;
      }
      const validUntil = retainedCeiling(current.valid_until, result.value.valid_until);
      const items = [...current.items, ...result.value.items];
      const refs = items.map((item) => item.communication_ref);
      if (!isCurrent(validUntil) || refs.length !== new Set(refs).size) {
        setCommunications({ kind: "failure", failure: isCurrent(validUntil) ? "invalid-response" : "expired" });
        return;
      }
      setCommunications({
        kind: "ready",
        loadingMore: false,
        page: { ...result.value, items, valid_until: validUntil },
      });
    } catch (error) {
      if (!request.signal.aborted && !(error instanceof DOMException && error.name === "AbortError")) {
        setCommunications({ kind: "failure", failure: "dependency_unavailable" });
      }
    }
  }, [caseRef, clearContext, client, communications]);

  const loadMoreHistory = useCallback(async () => {
    if (history.kind !== "ready" || history.page.next_cursor === null) return;
    const current = history.page;
    historyController.current?.abort();
    const request = new AbortController();
    historyController.current = request;
    setHistory({ kind: "ready", page: current, loadingMore: true });
    try {
      const result = await client.listHistory(caseRef, request.signal, current.next_cursor ?? undefined);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        if (invalidatesContext(result.failure)) clearContext(result.failure);
        else setHistory({ kind: "failure", failure: result.failure });
        return;
      }
      const validUntil = retainedCeiling(current.valid_until, result.value.valid_until);
      const items = [...current.items, ...result.value.items];
      const refs = items.map((item) => item.event_ref);
      const sequences = items.map((item) => BigInt(item.sequence!));
      const ordered = sequences.every((value, index) => index === 0 || sequences[index - 1] < value);
      if (!isCurrent(validUntil) || refs.length !== new Set(refs).size || !ordered) {
        setHistory({ kind: "failure", failure: isCurrent(validUntil) ? "invalid-response" : "expired" });
        return;
      }
      setHistory({
        kind: "ready",
        loadingMore: false,
        page: { ...result.value, items, valid_until: validUntil },
      });
    } catch (error) {
      if (!request.signal.aborted && !(error instanceof DOMException && error.name === "AbortError")) {
        setHistory({ kind: "failure", failure: "dependency_unavailable" });
      }
    }
  }, [caseRef, clearContext, client, history]);

  return (
    <section className="stacked-sections" aria-label="Comunicações e eventos do portal">
      <CommunicationsSection
        state={communications}
        onRetry={() => void loadCommunications()}
        onLoadMore={() => void loadMoreCommunications()}
      />
      <HistorySection
        state={history}
        onRetry={() => void loadHistory()}
        onLoadMore={() => void loadMoreHistory()}
      />
    </section>
  );
}
