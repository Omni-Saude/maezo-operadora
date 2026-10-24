import { useCallback, useEffect, useId, useRef, useState } from "react";

import type {
  StaffDocumentPage,
  StaffDocumentRequestPage,
  StaffDocumentsClient,
  StaffDocumentsFailure,
} from "./staffDocumentsClient";

// W5 CLNT 5.5: the staff `documents` area, READ-ONLY. The panel consults the two GET
// routes the BFF already mounts (`documents.py`) and renders metadata only: uploads,
// request responses and document content stay out of this surface — the envio remains the
// stub's own promise for a later package, and bytes live on the protected PHI plane.

type ConsultState =
  | Readonly<{ kind: "idle" }>
  | Readonly<{ kind: "loading"; caseRef: string }>
  | Readonly<{ kind: "ready"; caseRef: string; documents: StaffDocumentPage; requests: StaffDocumentRequestPage }>
  | Readonly<{ kind: "error"; caseRef: string; failure: StaffDocumentsFailure }>;

const errorMessage: Readonly<Record<StaffDocumentsFailure, string>> = {
  invalid_request: "Confira a referência: ela tem de 16 a 128 letras, números, _ ou -.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Sua sessão não tem permissão para ver os documentos deste caso. Peça o vínculo ao administrador do portal.",
  resource_unavailable: "O caso não foi encontrado ou não está liberado para a sua sessão.",
  conflict: "Os documentos mudaram durante a consulta. Consulte de novo.",
  dependency_unavailable: "O serviço de documentos está indisponível: fora do ar ou não habilitado neste ambiente. Nada foi exibido.",
  "invalid-response": "O portal recusou uma resposta inesperada do serviço de documentos. Nada foi exibido.",
};

// Closed maps over the contract's own literals (`portal/contracts/documents.py`). An
// unknown disposition is shown raw, never hidden.
const requestDisposition: Readonly<Record<StaffDocumentRequestPage["requests"][number]["disposition"], string>> = {
  open: "Aberto",
  answered: "Respondido",
  expired: "Expirado",
  replaced: "Substituído",
};

function RequestDisposition({ disposition }: { disposition: StaffDocumentRequestPage["requests"][number]["disposition"] }) {
  const label = Object.hasOwn(requestDisposition, disposition) ? requestDisposition[disposition] : undefined;
  return label === undefined
    ? <span className="exact-value">{disposition}</span>
    : <>{label}</>;
}

function DocumentList({ page }: { page: StaffDocumentPage }) {
  if (page.documents.length === 0) {
    return <p>Nenhum documento verificado neste caso.</p>;
  }
  return (
    <div className="table-scroll" tabIndex={0} aria-label="Tabela de documentos verificados do caso">
      <table>
        <caption className="visually-hidden">Documentos com custódia verificada neste caso</caption>
        <thead><tr><th scope="col">Documento</th><th scope="col">Tipo exigido</th><th scope="col">Situação</th></tr></thead>
        <tbody>
          {page.documents.map((document) => (
            <tr key={document.document_ref}>
              <th scope="row" className="exact-value">{document.document_ref}</th>
              <td className="exact-value">{document.document_type_ref}</td>
              <td>Verificado</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RequestList({ page }: { page: StaffDocumentRequestPage }) {
  if (page.requests.length === 0) {
    return <p>Nenhum pedido de documento para este caso.</p>;
  }
  return (
    <div className="table-scroll" tabIndex={0} aria-label="Tabela de pedidos de documento do caso">
      <table>
        <caption className="visually-hidden">Pedidos de documento do caso, com revisão e situação</caption>
        <thead><tr><th scope="col">Pedido</th><th scope="col">Revisão</th><th scope="col">Situação</th><th scope="col">Política</th><th scope="col">Tipos exigidos</th></tr></thead>
        <tbody>
          {page.requests.map((request) => (
            <tr key={request.request_ref}>
              <th scope="row" className="exact-value">{request.request_ref}</th>
              <td className="exact-value">{request.revision}</td>
              <td><RequestDisposition disposition={request.disposition} /></td>
              <td className="exact-value">{request.policy_ref}</td>
              <td>
                <ul className="document-type-list">
                  {request.requested_document_type_refs.map((typeRef) => (
                    <li key={typeRef} className="exact-value">{typeRef}</li>
                  ))}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StaffDocumentsPanel({
  client,
  onSessionUnavailable,
}: {
  client: StaffDocumentsClient;
  onSessionUnavailable: () => void;
}) {
  const inputId = useId();
  const [caseRef, setCaseRef] = useState("");
  const [consult, setConsult] = useState<ConsultState>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const resultHeading = useRef<HTMLHeadingElement | null>(null);

  const consultCase = useCallback(async (requestedCaseRef: string) => {
    const current = ++epoch.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setConsult({ kind: "loading", caseRef: requestedCaseRef });
    try {
      // Both reads ride the mounted GET routes; neither carries a body — the panel has no
      // way to mutate anything.
      const [documents, requests] = await Promise.all([
        client.listDocuments(requestedCaseRef, controller.signal),
        client.listDocumentRequests(requestedCaseRef, controller.signal),
      ]);
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      if (documents.kind === "failure") {
        setConsult({ kind: "error", caseRef: requestedCaseRef, failure: documents.failure });
        if (documents.failure === "authentication_unavailable") onSessionUnavailable();
        return;
      }
      if (requests.kind === "failure") {
        setConsult({ kind: "error", caseRef: requestedCaseRef, failure: requests.failure });
        if (requests.failure === "authentication_unavailable") onSessionUnavailable();
        return;
      }
      setConsult({
        kind: "ready",
        caseRef: requestedCaseRef,
        documents: documents.value,
        requests: requests.value,
      });
    } catch (error) {
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      setConsult({ kind: "error", caseRef: requestedCaseRef, failure: "dependency_unavailable" });
    }
  }, [client, onSessionUnavailable]);

  useEffect(() => () => {
    epoch.current += 1;
    request.current?.abort();
    request.current = null;
  }, []);

  useEffect(() => {
    if (consult.kind === "error") resultHeading.current?.focus();
  }, [consult]);

  return (
    <section className="staff-documents" aria-labelledby="staff-documents-heading">
      <div className="case-queue-heading">
        <div>
          <h2 id="staff-documents-heading">Documentos do caso</h2>
          <p>
            Consulta somente leitura dos documentos verificados e dos pedidos de documento de um
            caso liberado para a sua sessão.
          </p>
        </div>
      </div>
      <form className="case-lookup" onSubmit={(event) => { event.preventDefault(); void consultCase(caseRef.trim()); }}>
        <label htmlFor={inputId}>Consultar pela referência exata do caso</label>
        <div className="case-lookup-row">
          <input id={inputId} name="case-reference" value={caseRef} onChange={(event) => setCaseRef(event.target.value)} autoComplete="off" spellCheck={false} required minLength={16} maxLength={128} aria-describedby={`${inputId}-help`} />
          <button className="primary-action" type="submit" disabled={consult.kind === "loading"}>
            {consult.kind === "loading" ? "Consultando…" : "Consultar documentos"}
          </button>
        </div>
        <small id={`${inputId}-help`}>A referência completa tem de 16 a 128 caracteres.</small>
      </form>
      <div aria-live="polite" aria-atomic="false">
        {consult.kind === "loading" && <p className="queue-message">Consultando os documentos do caso.</p>}
        {consult.kind === "error" && (
          <section className="queue-message queue-error" aria-labelledby="staff-documents-error-heading">
            <h3 id="staff-documents-error-heading" ref={resultHeading} tabIndex={-1}>
              Não foi possível consultar os documentos
            </h3>
            <p role="alert">{errorMessage[consult.failure]}</p>
            {consult.failure !== "authentication_unavailable" && (
              <button className="secondary-action" type="button" onClick={() => void consultCase(consult.caseRef)}>
                Consultar novamente
              </button>
            )}
          </section>
        )}
      </div>
      {consult.kind === "ready" && (
        <>
          <p className="exact-value case-detail-ref">{consult.caseRef}</p>
          <section aria-labelledby="staff-documents-verified-heading">
            <h3 id="staff-documents-verified-heading">Documentos verificados</h3>
            <DocumentList page={consult.documents} />
          </section>
          <section aria-labelledby="staff-documents-requests-heading">
            <h3 id="staff-documents-requests-heading">Pedidos de documento</h3>
            <RequestList page={consult.requests} />
          </section>
          <p className="freshness-detail">
            Somente leitura. O envio de documentos ainda não está disponível nesta versão do portal.
          </p>
        </>
      )}
    </section>
  );
}
