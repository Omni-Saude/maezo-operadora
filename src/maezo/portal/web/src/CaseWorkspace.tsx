import { useId, useState } from "react";

import type {
  CaseWorkspaceView,
  ChronologyEventView,
  CommandProgressView,
  CommunicationView,
  DossierSectionView,
  DocumentRequestView,
  DocumentView,
  ResourceState,
} from "./caseExperienceModels";

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function SectionState({ state }: { state: ResourceState }) {
  if (state.kind === "ready") return null;
  if (state.kind === "loading") {
    return <p className="resource-message" role="status">Atualizando informações autorizadas…</p>;
  }
  return (
    <p className={`resource-message ${state.kind === "unavailable" ? "resource-error" : ""}`}>
      {state.message}
    </p>
  );
}

function DossierSection({ section }: { section: DossierSectionView }) {
  const headingId = useId();
  return (
    <section className="dossier-section" aria-labelledby={headingId}>
      <h3 id={headingId}>{section.heading}</h3>
      {section.description && <p>{section.description}</p>}
      <dl>
        {section.fields.map((field) => (
          <div key={field.fieldRef}>
            <dt>{field.label}</dt>
            <dd>{field.value}</dd>
            {field.provenanceLabel && (
              <dd className="provenance">Fonte: {field.provenanceLabel}</dd>
            )}
          </div>
        ))}
      </dl>
    </section>
  );
}

export function DossierView({
  state,
  sections,
}: {
  state: ResourceState;
  sections: readonly DossierSectionView[];
}) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <div className="section-heading compact-heading">
        <div>
          <p className="eyebrow">Informações do caso</p>
          <h2 id={headingId}>Dossiê autorizado</h2>
        </div>
        <span className="privacy-chip">Acesso por recurso</span>
      </div>
      <SectionState state={state} />
      {state.kind === "ready" && (
        <div className="dossier-grid">
          {sections.map((section) => <DossierSection section={section} key={section.sectionRef} />)}
        </div>
      )}
    </section>
  );
}

export function ChronologyView({
  state,
  events,
}: {
  state: ResourceState;
  events: readonly ChronologyEventView[];
}) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Movimentações confirmadas</p>
      <h2 id={headingId}>Histórico cronológico</h2>
      <SectionState state={state} />
      {state.kind === "ready" && (
        <ol className="chronology">
          {events.map((event) => (
            <li key={event.eventRef}>
              <time dateTime={event.occurredAt}>{formatTimestamp(event.occurredAt)}</time>
              <div>
                <h3>{event.heading}</h3>
                <p>{event.description}</p>
                <p className="event-meta">
                  {event.sourceLabel}
                  {event.statusLabel ? ` · ${event.statusLabel}` : ""}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function DocumentListView({
  state,
  documents,
  downloadingRef,
  onDownload,
}: {
  state: ResourceState;
  documents: readonly DocumentView[];
  downloadingRef?: string;
  onDownload?: (documentRef: string) => void;
}) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Custódia e verificação</p>
      <h2 id={headingId}>Documentos</h2>
      <p>O acesso é conferido novamente a cada abertura.</p>
      <SectionState state={state} />
      {state.kind === "ready" && (
        <ul className="resource-list document-list">
          {documents.map((document) => (
            <li key={document.documentRef}>
              <div>
                <h3>{document.name}</h3>
                <p>{document.kindLabel}</p>
                <p className="event-meta">
                  {document.statusLabel} · registrado em {formatTimestamp(document.recordedAt)}
                </p>
              </div>
              {document.canDownload && onDownload && (
                <button
                  className="secondary-action compact-action"
                  type="button"
                  disabled={downloadingRef === document.documentRef}
                  onClick={() => onDownload(document.documentRef)}
                >
                  {downloadingRef === document.documentRef ? "Preparando acesso…" : "Abrir documento"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function DocumentRequestsView({
  state,
  requests,
  onRespond,
}: {
  state: ResourceState;
  requests: readonly DocumentRequestView[];
  onRespond?: (requestRef: string) => void;
}) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Ações pendentes</p>
      <h2 id={headingId}>Pedidos de documentos</h2>
      <SectionState state={state} />
      {state.kind === "ready" && (
        <ul className="resource-list request-list">
          {requests.map((request) => (
            <li key={request.requestRef}>
              <div>
                <span className={`status-chip status-${request.status}`}>{request.statusLabel}</span>
                <h3>{request.heading}</h3>
                <p>{request.instructions}</p>
                <p className="event-meta">
                  {request.dueAt === null
                    ? "Sem prazo exibido"
                    : `Prazo informado: ${formatTimestamp(request.dueAt)}`}
                </p>
              </div>
              {request.canRespond && onRespond && (
                <button
                  className="primary-action compact-action"
                  type="button"
                  onClick={() => onRespond(request.requestRef)}
                >
                  Enviar documentos para este pedido
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function CommunicationsView({
  state,
  communications,
}: {
  state: ResourceState;
  communications: readonly CommunicationView[];
}) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Caixa do portal</p>
      <h2 id={headingId}>Mensagens</h2>
      <p>O status abaixo representa a entrega nesta caixa.</p>
      <SectionState state={state} />
      {state.kind === "ready" && (
        <ol className="communication-list">
          {communications.map((communication) => (
            <li key={communication.communicationRef}>
              <header>
                <div>
                  <h3>{communication.subject}</h3>
                  <p className="event-meta">{communication.senderLabel}</p>
                </div>
                <time dateTime={communication.sentAt}>{formatTimestamp(communication.sentAt)}</time>
              </header>
              <p>{communication.body}</p>
              <p className="delivery-status">{communication.deliveryLabel}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function CommandProgress({ command }: { command: CommandProgressView }) {
  return (
    <li className={`command-card command-${command.state}`}>
      <div className="command-heading">
        <div>
          <h3>{command.actionLabel}</h3>
          <p className="event-meta">Atualizado em {formatTimestamp(command.updatedAt)}</p>
        </div>
        <span className="status-chip">{command.stateLabel}</span>
      </div>
      <p>{command.description}</p>
      {command.state === "accepted" && (
        <p className="truthful-state">A solicitação foi recebida. A execução ainda não foi confirmada.</p>
      )}
      {command.state === "reconciling" && (
        <p className="truthful-state">O portal está conferindo o resultado antes de apresentar uma conclusão.</p>
      )}
      {command.state === "executed" && (
        <dl className="receipt-facts">
          <div><dt>Resultado confirmado</dt><dd>{command.receipt.resultLabel}</dd></div>
          <div><dt>Recibo</dt><dd className="exact-value">{command.receipt.receiptRef}</dd></div>
          <div><dt>Registrado em</dt><dd>{formatTimestamp(command.receipt.recordedAt)}</dd></div>
        </dl>
      )}
    </li>
  );
}

export function ReceiptsView({
  state,
  commands,
}: {
  state: ResourceState;
  commands: readonly CommandProgressView[];
}) {
  const headingId = useId();
  return (
    <section className="workspace-section" aria-labelledby={headingId}>
      <p className="eyebrow">Acompanhamento técnico</p>
      <h2 id={headingId}>Solicitações e recibos</h2>
      <SectionState state={state} />
      {state.kind === "ready" && (
        <ul className="command-list">
          {commands.map((command) => <CommandProgress command={command} key={command.commandRef} />)}
        </ul>
      )}
    </section>
  );
}

export function CaseWorkspace({
  view,
  onClose,
  onDownloadDocument,
  onRespondDocumentRequest,
}: {
  view: CaseWorkspaceView;
  onClose?: () => void;
  onDownloadDocument?: (documentRef: string) => Promise<void> | void;
  onRespondDocumentRequest?: (requestRef: string) => void;
}) {
  const headingId = useId();
  const [downloadingRef, setDownloadingRef] = useState<string>();

  const download = onDownloadDocument
    ? async (documentRef: string) => {
        setDownloadingRef(documentRef);
        try {
          await onDownloadDocument(documentRef);
        } finally {
          setDownloadingRef(undefined);
        }
      }
    : undefined;

  return (
    <article className="case-workspace" aria-labelledby={headingId}>
      <header className="case-workspace-header">
        <div>
          <p className="eyebrow">{view.audienceLabel}</p>
          <h2 id={headingId}>{view.headline}</h2>
          <p>{view.guidance}</p>
        </div>
        <div className="case-state">
          <span className={`status-chip status-${view.summary.state}`}>{view.statusLabel}</span>
          <span>Atualizado em {formatTimestamp(view.summary.stateObservedAt)}</span>
        </div>
        {onClose && (
          <button className="secondary-action compact-action" type="button" onClick={onClose}>
            Voltar aos casos
          </button>
        )}
      </header>

      <div className="workspace-grid">
        <DossierView {...view.dossier} />
        <DocumentRequestsView
          state={view.documentRequests.state}
          requests={view.documentRequests.items}
          onRespond={onRespondDocumentRequest}
        />
        <ChronologyView {...view.chronology} />
        <DocumentListView
          state={view.documents.state}
          documents={view.documents.items}
          downloadingRef={downloadingRef}
          onDownload={download}
        />
        <CommunicationsView
          state={view.communications.state}
          communications={view.communications.items}
        />
        <ReceiptsView state={view.commands.state} commands={view.commands.items} />
      </div>
    </article>
  );
}
