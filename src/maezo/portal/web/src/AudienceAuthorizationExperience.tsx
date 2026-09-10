import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  ChronologyView,
  DossierView,
  DocumentListView,
  DocumentRequestsView,
  ReceiptsView,
} from "./CaseWorkspace";
import { CaseCommunicationsPanel } from "./CaseCommunicationsPanel";
import { IntakeRecoveryPanel } from "./IntakeRecoveryPanel";
import { ExternalAreaPanel, ExternalNavigation, type ExternalArea } from "./PortalNavigation";
import type { CaseCommunicationsClient } from "./caseCommunicationsClient";
import type {
  AuthorizationIntakeDraft,
  AuthorizationIntakeFormView,
  AuthorizationIntakeProgressView,
  CaseExperienceFailure,
  CasePageView,
  CaseSummaryView,
  CaseWorkspaceView,
  ExperienceResult,
  PortalAudience,
  ProviderAuthorizationService,
} from "./caseExperienceModels";

type ExternalAudience = Exclude<PortalAudience, "staff">;
type PageState =
  | { kind: "loading" }
  | { kind: "ready"; page: CasePageView; loadingMore: boolean }
  | { kind: "failure"; failure: CaseExperienceFailure };
type DetailState =
  | { kind: "none" }
  | { kind: "loading"; summary: CaseSummaryView }
  | { kind: "ready"; workspace: CaseWorkspaceView }
  | { kind: "failure"; summary: CaseSummaryView; failure: CaseExperienceFailure };

const failureMessages: Record<CaseExperienceFailure, string> = {
  "session-unavailable": "Sua sessão não está mais disponível.",
  "access-revoked": "Seu vínculo atual não autoriza mais este conteúdo.",
  "resource-unavailable": "Este recurso não está mais disponível.",
  "refresh-required": "As informações mudaram. Atualize para continuar.",
  "dependency-unavailable": "Uma dependência não respondeu. Tente novamente.",
  "outcome-unknown": "O resultado do envio é desconhecido. Tente novamente sem alterar os dados para reutilizar o mesmo comando.",
  "invalid-response": "O portal recusou uma resposta inesperada.",
};

const caseKindLabels: Record<CaseSummaryView["kind"], string> = {
  authorization: "Autorização",
  reimbursement: "Reembolso",
  account: "Conta",
};

function isAccessFailure(failure: CaseExperienceFailure) {
  return failure === "session-unavailable" || failure === "access-revoked";
}

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function CasePicker({
  state,
  selectedRef,
  onSelect,
  onRetry,
  onLoadMore,
}: {
  state: PageState;
  selectedRef?: string;
  onSelect: (summary: CaseSummaryView) => void;
  onRetry: () => void;
  onLoadMore: () => void;
}) {
  if (state.kind === "loading") {
    return <p className="resource-message" role="status">Consultando suas solicitações autorizadas…</p>;
  }
  if (state.kind === "failure") {
    return (
      <div className="resource-message resource-error">
        <p role="alert">{failureMessages[state.failure]}</p>
        {state.failure !== "session-unavailable" && (
          <button className="secondary-action compact-action" type="button" onClick={onRetry}>
            Atualizar solicitações
          </button>
        )}
      </div>
    );
  }
  if (state.page.items.length === 0) {
    return <p className="resource-message">Nenhuma solicitação autorizada foi encontrada.</p>;
  }
  return (
    <div>
      <p className="freshness" role="status">
        Fonte consultada em {formatTimestamp(state.page.observedAt)}
      </p>
      <ul className="case-list">
        {state.page.items.map((item) => (
          <li key={item.caseRef} className={selectedRef === item.caseRef ? "selected-case" : ""}>
            <div>
              <p className="eyebrow">
                {caseKindLabels[item.kind]}
              </p>
              <h3>Referência {item.caseRef}</h3>
              <p className="event-meta">
                {item.state === "active" ? "Em andamento" : "Encerrada"} · atualização confirmada em {formatTimestamp(item.stateObservedAt)}
              </p>
            </div>
            <button
              className="secondary-action compact-action"
              type="button"
              aria-pressed={selectedRef === item.caseRef}
              onClick={() => onSelect(item)}
            >
              {selectedRef === item.caseRef ? "Solicitação aberta" : "Abrir solicitação"}
            </button>
          </li>
        ))}
      </ul>
      {state.page.nextCursor !== null && (
        <button
          className="secondary-action"
          type="button"
          disabled={state.loadingMore}
          onClick={onLoadMore}
        >
          {state.loadingMore ? "Carregando…" : "Carregar mais"}
        </button>
      )}
    </div>
  );
}

function SelectedCasePanel({
  state,
  area,
  onRetry,
  onDownload,
  communicationsClient,
  onSessionUnavailable,
}: {
  state: DetailState;
  area: ExternalArea;
  onRetry: () => void;
  onDownload: (documentRef: string) => Promise<void>;
  communicationsClient: CaseCommunicationsClient;
  onSessionUnavailable: () => void;
}) {
  if (state.kind === "none") {
    return <p className="resource-message">Abra uma solicitação para consultar esta área.</p>;
  }
  if (state.kind === "loading") {
    return <p className="resource-message" role="status">Consultando o caso selecionado…</p>;
  }
  if (state.kind === "failure") {
    return (
      <div className="resource-message resource-error">
        <p role="alert">{failureMessages[state.failure]}</p>
        {state.failure !== "session-unavailable" && (
          <button className="secondary-action compact-action" type="button" onClick={onRetry}>
            Consultar novamente
          </button>
        )}
      </div>
    );
  }
  const workspace = state.workspace;
  if (area === "requests") {
    return (
      <div className="stacked-sections">
        <DossierView state={workspace.dossier.state} sections={workspace.dossier.sections} />
        <ChronologyView state={workspace.chronology.state} events={workspace.chronology.events} />
      </div>
    );
  }
  if (area === "documents") {
    return (
      <div className="stacked-sections">
        <DocumentRequestsView
          state={workspace.documentRequests.state}
          requests={workspace.documentRequests.items}
        />
        <DocumentListView
          state={workspace.documents.state}
          documents={workspace.documents.items}
          onDownload={onDownload}
        />
      </div>
    );
  }
  if (area === "communications") {
    return (
      <CaseCommunicationsPanel
        caseRef={workspace.summary.caseRef}
        client={communicationsClient}
        onSessionUnavailable={onSessionUnavailable}
      />
    );
  }
  return <ReceiptsView state={workspace.commands.state} commands={workspace.commands.items} />;
}

function ProviderAuthorizationIntake({
  service,
  onFailure,
}: {
  service: ProviderAuthorizationService;
  onFailure: (failure: CaseExperienceFailure) => void;
}) {
  const [form, setForm] = useState<ExperienceResult<AuthorizationIntakeFormView> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [observing, setObserving] = useState(false);
  const [progress, setProgress] = useState<AuthorizationIntakeProgressView>();
  const [error, setError] = useState<CaseExperienceFailure>();
  const [observationFailure, setObservationFailure] = useState<CaseExperienceFailure>();
  const [notObservedCommand, setNotObservedCommand] = useState<string>();
  const controller = useRef<AbortController | null>(null);
  const submitController = useRef<AbortController | null>(null);
  const observationController = useRef<AbortController | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const headingId = useId();
  const valueHelpId = `${headingId}-value-help`;
  const progressHeadingId = `${headingId}-progress`;

  useEffect(() => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setForm(null);
    setSubmitting(false);
    setObserving(false);
    setProgress(undefined);
    setError(undefined);
    setObservationFailure(undefined);
    setNotObservedCommand(undefined);
    void service
      .readAuthorizationIntakeForm(request.signal)
      .then((result) => {
        if (!request.signal.aborted) {
          setForm(result);
          if (result.kind === "failure") onFailure(result.failure);
        }
      })
      .catch((error: unknown) => {
        if (!request.signal.aborted && !isAbort(error)) {
          setForm({ kind: "failure", failure: "dependency-unavailable" });
        }
      });
    return () => {
      request.abort();
      controller.current?.abort();
      submitController.current?.abort();
      observationController.current?.abort();
      submitController.current = null;
      observationController.current = null;
    };
  }, [onFailure, service]);

  if (form === null) {
    return <p className="resource-message" role="status">Preparando a solicitação autorizada…</p>;
  }
  if (form.kind === "failure") {
    return <p className="resource-message resource-error" role="alert">{failureMessages[form.failure]}</p>;
  }

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // Ref ownership also fences keyboard/double events before React rerenders.
    if (submitController.current !== null || observationController.current !== null) return;
    const element = event.currentTarget;
    if (!element.reportValidity()) return;
    const values = new FormData(element);
    const estimatedValueCents = String(values.get("estimatedValueCents") ?? "");
    if (!/^(0|[1-9]\d*)$/.test(estimatedValueCents)) {
      const centsField = element.elements.namedItem("estimatedValueCents");
      if (centsField instanceof HTMLInputElement) {
        centsField.setCustomValidity("Use somente dígitos, sem ponto, vírgula ou sinal.");
        centsField.reportValidity();
        centsField.setCustomValidity("");
      }
      return;
    }
    const draft: AuthorizationIntakeDraft = {
      beneficiaryRef: String(values.get("beneficiaryRef") ?? ""),
      providerRef: String(values.get("providerRef") ?? ""),
      guideRef: String(values.get("guideRef") ?? ""),
      procedureCode: String(values.get("procedureCode") ?? ""),
      procedureCategory: String(values.get("procedureCategory") ?? ""),
      careCharacter: String(values.get("careCharacter") ?? ""),
      estimatedValueCents,
      protectedDocumentRefs: values.getAll("protectedDocumentRefs").map(String),
    };
    const request = new AbortController();
    submitController.current = request;
    setSubmitting(true);
    setError(undefined);
    setObservationFailure(undefined);
    setNotObservedCommand(undefined);
    setProgress(undefined);
    try {
      const result = await service.submitAuthorization(draft, request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        setError(result.failure);
        onFailure(result.failure);
        return;
      }
      setProgress(result.progress);
      formRef.current?.reset();
    } catch (caught) {
      if (!request.signal.aborted && !isAbort(caught)) setError("dependency-unavailable");
    } finally {
      if (submitController.current === request) {
        submitController.current = null;
        if (!request.signal.aborted) setSubmitting(false);
      }
    }
  };

  const observePending = async () => {
    if (submitController.current !== null || observationController.current !== null) return;
    const request = new AbortController();
    observationController.current = request;
    setObserving(true);
    setObservationFailure(undefined);
    setNotObservedCommand(undefined);
    try {
      const result = await service.observePendingAuthorization(request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        setObservationFailure(result.failure);
        onFailure(result.failure);
        return;
      }
      if (result.kind === "none") {
        setError("outcome-unknown");
        return;
      }
      if (result.kind === "not-observed") {
        setNotObservedCommand(result.commandId);
        setError(undefined);
        setObservationFailure(undefined);
        return;
      }
      setError(undefined);
      setObservationFailure(undefined);
      setProgress(result.progress);
      formRef.current?.reset();
    } catch (caught) {
      if (!request.signal.aborted && !isAbort(caught)) {
        setObservationFailure("dependency-unavailable");
      }
    } finally {
      if (observationController.current === request) {
        observationController.current = null;
        if (!request.signal.aborted) setObserving(false);
      }
    }
  };

  return (
    <section className="workspace-section intake-section" aria-labelledby={headingId}>
      <p className="eyebrow">Autorização prévia</p>
      <h2 id={headingId}>Enviar nova solicitação</h2>
      <p>
        Informe os dados da guia. O servidor confirma seus vínculos e avalia os fatos necessários antes de iniciar o caso.
      </p>
      <form ref={formRef} className="intake-form" onSubmit={submit}>
        <div className="form-grid">
          <label>
            Beneficiário
            <select name="beneficiaryRef" required defaultValue="">
              <option value="" disabled>Selecione um vínculo autorizado</option>
              {form.value.beneficiaries.map((option) => <option value={option.ref} key={option.ref}>{option.label}</option>)}
            </select>
          </label>
          <label>
            Prestador solicitante
            <select name="providerRef" required defaultValue="">
              <option value="" disabled>Selecione um vínculo autorizado</option>
              {form.value.providers.map((option) => <option value={option.ref} key={option.ref}>{option.label}</option>)}
            </select>
          </label>
          <label>
            Guia TISS protegida
            <select name="guideRef" required defaultValue="">
              <option value="" disabled>Selecione uma guia autorizada</option>
              {form.value.guides.map((option) => <option value={option.ref} key={option.ref}>{option.label}</option>)}
            </select>
          </label>
          <label>
            Código do procedimento TUSS
            <input name="procedureCode" required inputMode="numeric" autoComplete="off" />
          </label>
          <label>
            Categoria do procedimento
            <select name="procedureCategory" required defaultValue="">
              <option value="" disabled>Selecione</option>
              {form.value.procedureCategories.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label>
            Caráter do atendimento
            <select name="careCharacter" required defaultValue="">
              <option value="" disabled>Selecione</option>
              {form.value.careCharacters.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label>
            Valor estimado em centavos
            <input
              name="estimatedValueCents"
              required
              inputMode="numeric"
              pattern="0|[1-9][0-9]*"
              aria-describedby={valueHelpId}
              autoComplete="off"
            />
            <small id={valueHelpId}>Use somente dígitos, sem ponto, vírgula ou sinal.</small>
          </label>
        </div>
        {form.value.finalizedDocuments.length > 0 && (
          <fieldset className="document-options">
            <legend>Documentos finalizados</legend>
            <p>Selecione somente os documentos que pertencem a esta solicitação.</p>
            {form.value.finalizedDocuments.map((document) => (
              <label key={document.ref}>
                <input type="checkbox" name="protectedDocumentRefs" value={document.ref} />
                <span>{document.label}</span>
              </label>
            ))}
          </fieldset>
        )}
        <p className="form-notice">
          O envio registra a solicitação para processamento. Ele não representa autorização concedida.
        </p>
        <button className="primary-action" type="submit" disabled={submitting || observing}>
          {submitting ? "Enviando com segurança…" : "Enviar solicitação"}
        </button>
      </form>
      {error && <p className="resource-message resource-error" role="alert">{failureMessages[error]}</p>}
      {observationFailure && (
        <p className="resource-message resource-error" role="alert">
          {failureMessages[observationFailure]} O resultado do envio continua desconhecido.
        </p>
      )}
      {error === "outcome-unknown" && (
        <button
          className="secondary-action"
          type="button"
          disabled={observing || submitting}
          onClick={() => void observePending()}
        >
          {observing ? "Consultando comando original…" : "Verificar comando original"}
        </button>
      )}
      {notObservedCommand && (
        <section className="resource-message" aria-label="Comando ainda não observado">
          <p role="status">
            Nenhuma admissão foi observada para este comando nesta consulta. O resultado continua incerto.
          </p>
          <dl className="receipt-facts">
            <div><dt>Comando original</dt><dd className="exact-value">{notObservedCommand}</dd></div>
          </dl>
          <p>
            Consulte novamente. Uma repetição do envio deve manter os mesmos dados para reutilizar
            este comando; esta observação não autoriza uma nova solicitação.
          </p>
          <button
            className="secondary-action compact-action"
            type="button"
            disabled={observing || submitting}
            onClick={() => void observePending()}
          >
            {observing ? "Consultando comando original…" : "Consultar comando novamente"}
          </button>
        </section>
      )}
      {progress && (
        <section className={`intake-progress intake-${progress.state}`} aria-labelledby={progressHeadingId}>
          <div className="command-heading">
            <div>
              <p className="eyebrow">Acompanhamento do envio</p>
              <h3 id={progressHeadingId}>{progress.stateLabel}</h3>
            </div>
            <span className="status-chip">{progress.stateLabel}</span>
          </div>
          <p>{progress.description}</p>
          {progress.state !== "started" && progress.state !== "rejected" && (
            <p className="truthful-state">O caso ainda não foi confirmado como iniciado.</p>
          )}
          {progress.state === "started" && (
            <dl className="receipt-facts">
              <div><dt>Caso iniciado</dt><dd className="exact-value">{progress.caseRef}</dd></div>
              <div><dt>Recibo de início</dt><dd className="exact-value">{progress.startReceiptRef}</dd></div>
              {progress.revision && (
                <div><dt>Revisão do acompanhamento</dt><dd className="exact-value">{progress.revision}</dd></div>
              )}
              {progress.updatedAt && (
                <div><dt>Confirmado em</dt><dd>{formatTimestamp(progress.updatedAt)}</dd></div>
              )}
            </dl>
          )}
        </section>
      )}
    </section>
  );
}

type AudienceExperienceProps = {
  audience: ExternalAudience;
  service: ProviderAuthorizationService;
  communicationsClient: CaseCommunicationsClient;
  onSessionUnavailable: () => void;
};

export function AudienceAuthorizationExperience(props: AudienceExperienceProps) {
  const [context, setContext] = useState({
    service: props.service, audience: props.audience, generation: 0,
  });
  if (context.service !== props.service || context.audience !== props.audience) {
    // Reset before rendering children: no old protected tree is committed under
    // a new service/audience. Unmount cleanup aborts all work in the old scope.
    setContext({ service: props.service, audience: props.audience, generation: context.generation + 1 });
    return null;
  }
  return <AudienceAuthorizationContext key={context.generation} {...props} />;
}

function AudienceAuthorizationContext({
  audience, service, communicationsClient, onSessionUnavailable,
}: AudienceExperienceProps) {
  const [activeArea, setActiveArea] = useState<ExternalArea>("requests");
  const [pageState, setPageState] = useState<PageState>({ kind: "loading" });
  const [detailState, setDetailState] = useState<DetailState>({ kind: "none" });
  const pageController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const downloadController = useRef<AbortController | null>(null);
  const downloadUrl = useRef<string | null>(null);
  const [preparedDownload, setPreparedDownload] = useState<string>();
  const [accessFailure, setAccessFailure] = useState<CaseExperienceFailure>();

  const releaseDownload = useCallback(() => {
    downloadController.current?.abort();
    if (downloadUrl.current !== null) URL.revokeObjectURL(downloadUrl.current);
    downloadUrl.current = null;
    setPreparedDownload(undefined);
  }, []);
  const selectedSummary =
    detailState.kind === "none"
      ? undefined
      : detailState.kind === "ready"
        ? detailState.workspace.summary
        : detailState.summary;

  const handleFailure = useCallback((failure: CaseExperienceFailure) => {
    if (isAccessFailure(failure)) {
      pageController.current?.abort();
      detailController.current?.abort();
      releaseDownload();
      setDetailState({ kind: "none" });
      setPageState({ kind: "failure", failure });
      setAccessFailure(failure);
    }
    if (failure === "session-unavailable") onSessionUnavailable();
  }, [onSessionUnavailable, releaseDownload]);

  const loadFirstPage = useCallback(async () => {
    detailController.current?.abort();
    releaseDownload();
    setDetailState({ kind: "none" });
    pageController.current?.abort();
    const request = new AbortController();
    pageController.current = request;
    setPageState({ kind: "loading" });
    try {
      const result = await service.listCases(request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        setPageState({ kind: "failure", failure: result.failure });
        handleFailure(result.failure);
        return;
      }
      setAccessFailure(undefined);
      setPageState({ kind: "ready", page: result.value, loadingMore: false });
    } catch (caught) {
      if (!request.signal.aborted && !isAbort(caught)) {
        setPageState({ kind: "failure", failure: "dependency-unavailable" });
      }
    }
  }, [handleFailure, releaseDownload, service]);

  useEffect(() => {
    void loadFirstPage();
    return () => {
      pageController.current?.abort();
      detailController.current?.abort();
      releaseDownload();
    };
  }, [loadFirstPage, releaseDownload]);

  const selectCase = useCallback(async (summary: CaseSummaryView) => {
    releaseDownload();
    detailController.current?.abort();
    const request = new AbortController();
    detailController.current = request;
    setDetailState({ kind: "loading", summary });
    try {
      const result = await service.readCase(summary.caseRef, request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        setDetailState({ kind: "failure", summary, failure: result.failure });
        handleFailure(result.failure);
        return;
      }
      setDetailState({ kind: "ready", workspace: result.value });
    } catch (caught) {
      if (!request.signal.aborted && !isAbort(caught)) {
        setDetailState({ kind: "failure", summary, failure: "dependency-unavailable" });
      }
    }
  }, [handleFailure, releaseDownload, service]);

  const loadMore = useCallback(async () => {
    if (pageState.kind !== "ready" || pageState.page.nextCursor === null) return;
    const currentPage = pageState.page;
    const cursor = pageState.page.nextCursor;
    pageController.current?.abort();
    const request = new AbortController();
    pageController.current = request;
    setPageState({ kind: "ready", page: currentPage, loadingMore: true });
    try {
      const result = await service.listCases(request.signal, cursor);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        setPageState({ kind: "failure", failure: result.failure });
        handleFailure(result.failure);
        return;
      }
      setPageState({
        kind: "ready",
        loadingMore: false,
        page: {
          ...result.value,
          items: [...currentPage.items, ...result.value.items],
        },
      });
    } catch (caught) {
      if (!request.signal.aborted && !isAbort(caught)) {
        setPageState({ kind: "failure", failure: "dependency-unavailable" });
      }
    }
  }, [handleFailure, pageState, service]);

  const download = useCallback(async (documentRef: string) => {
    if (detailState.kind !== "ready" || accessFailure) return;
    releaseDownload();
    const request = new AbortController();
    downloadController.current = request;
    try {
      const result = await service.downloadDocument(documentRef, request.signal);
      if (request.signal.aborted) return;
      if (result.kind === "failure") {
        releaseDownload();
        if (!isAccessFailure(result.failure) && selectedSummary) {
          setDetailState({ kind: "failure", summary: selectedSummary, failure: result.failure });
        }
        handleFailure(result.failure);
        return;
      }
      // Render a download-only link, never interpret a protected Blob as HTML
      // or a remote destination. This component owns its temporary URL.
      const url = URL.createObjectURL(result.value);
      downloadUrl.current = url;
      setPreparedDownload(url);
    } catch (caught) {
      if (!request.signal.aborted && !isAbort(caught) && selectedSummary) {
        releaseDownload();
        setDetailState({ kind: "failure", summary: selectedSummary, failure: "dependency-unavailable" });
      }
    }
  }, [accessFailure, detailState.kind, handleFailure, releaseDownload, selectedSummary, service]);

  const audienceLabel = audience === "beneficiary" ? "beneficiário" : "prestador";
  return (
    <section className="audience-experience" aria-labelledby="authorization-heading">
      <header className="experience-heading">
        <div>
          <p className="eyebrow">Área do {audienceLabel}</p>
          <h2 id="authorization-heading">Autorizações</h2>
          <p>Acompanhe cada solicitação, pendência, mensagem e resultado autorizado.</p>
        </div>
        <div className="trust-note">
          <strong>Acesso atual</strong>
          <span>Cada caso é verificado pelo servidor.</span>
        </div>
      </header>

      <ExternalNavigation audience={audience} active={activeArea} onChange={setActiveArea} />
      {preparedDownload && (
        <section className="resource-message" aria-label="Documento preparado">
          <p role="status">O documento autorizado está pronto para baixar.</p>
          <a href={preparedDownload} download="documento">Baixar documento preparado</a>
          <button type="button" onClick={releaseDownload}>Fechar acesso ao documento</button>
        </section>
      )}

      <ExternalAreaPanel area="requests" active={activeArea === "requests"}>
        <div className="two-column-experience">
          <section className="workspace-section" aria-labelledby="case-list-heading">
            <p className="eyebrow">Acompanhamento</p>
            <h2 id="case-list-heading">Suas solicitações</h2>
            <CasePicker
              state={pageState}
              selectedRef={selectedSummary?.caseRef}
              onSelect={selectCase}
              onRetry={() => void loadFirstPage()}
              onLoadMore={() => void loadMore()}
            />
            <SelectedCasePanel
              state={detailState}
              area="requests"
              onRetry={() => selectedSummary && void selectCase(selectedSummary)}
              onDownload={download}
              communicationsClient={communicationsClient}
              onSessionUnavailable={onSessionUnavailable}
            />
          </section>
          {audience === "provider" && !accessFailure && (
            <div className="stacked-sections">
              <ProviderAuthorizationIntake service={service} onFailure={handleFailure} />
              <IntakeRecoveryPanel
                service={service}
                onSessionUnavailable={onSessionUnavailable}
              />
            </div>
          )}
        </div>
      </ExternalAreaPanel>

      {(["documents", "communications", "receipts"] as const).map((area) => (
        <ExternalAreaPanel area={area} active={activeArea === area} key={area}>
          <section className="selected-case-context" aria-label="Solicitação selecionada">
            <CasePicker
              state={pageState}
              selectedRef={selectedSummary?.caseRef}
              onSelect={selectCase}
              onRetry={() => void loadFirstPage()}
              onLoadMore={() => void loadMore()}
            />
          </section>
          <SelectedCasePanel
            state={detailState}
            area={area}
            onRetry={() => selectedSummary && void selectCase(selectedSummary)}
            onDownload={download}
            communicationsClient={communicationsClient}
            onSessionUnavailable={onSessionUnavailable}
          />
        </ExternalAreaPanel>
      ))}
    </section>
  );
}
