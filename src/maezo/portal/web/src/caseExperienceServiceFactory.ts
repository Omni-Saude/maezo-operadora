import type {
  AuthorizationIntakeDraft,
  AuthorizationIntakeFormView,
  AuthorizationIntakeProgressView,
  AuthorizationIntakeResult,
  CaseExperienceFailure,
  CasePageView,
  CaseSummaryView,
  CaseWorkspaceView,
  ExperienceResult,
  ProviderAuthorizationService,
} from "./caseExperienceModels";
import {
  createPortalProductClient,
  type AuthIntakeSubmission,
  type DocumentPage,
  type DocumentRequestPage,
  type IntakeReceipt,
  type PortalProductClient,
  type ProductApiFailure,
  type ProductApiFetch,
} from "./productCaseClient";

import { isCurrent } from "./taskReadTime";

type ExternalAudience = "beneficiary" | "provider";

export interface AuthorizationIntakeFormProvider {
  read(signal: AbortSignal): Promise<ExperienceResult<AuthorizationIntakeFormView>>;
}

export interface ProductionCaseExperienceService extends ProviderAuthorizationService {
  readonly product: PortalProductClient;
  readonly contentUpload: Readonly<{
    kind: "unavailable";
    message: string;
  }>;
  readAuthorizationProgress(
    intakeRef: string,
    signal: AbortSignal,
  ): Promise<AuthorizationIntakeResult>;
}

export type CaseExperienceServiceOptions = Readonly<{
  // The caller must derive this from the current server session. It is presentation
  // context only; every operation is authorized again by the server.
  audience: ExternalAudience;
  csrfToken: string;
  fetcher?: ProductApiFetch;
  intakeFormProvider?: AuthorizationIntakeFormProvider;
  commandId?: () => string;
}>;

const unavailable = (message: string) => ({
  state: { kind: "unavailable" as const, message },
  items: [],
});

const caseKindLabels: Readonly<Record<CaseSummaryView["kind"], string>> = {
  authorization: "Autorização",
  reimbursement: "Reembolso",
  account: "Conta assistencial",
};

const requestStatus: Readonly<Record<
  DocumentRequestPage["requests"][number]["disposition"],
  Readonly<{
    status: "open" | "answered" | "expired" | "replaced";
    label: string;
  }>
>> = {
  open: { status: "open", label: "Aguardando documentos" },
  answered: { status: "answered", label: "Resposta enviada" },
  expired: { status: "expired", label: "Pedido expirado" },
  replaced: { status: "replaced", label: "Pedido substituído" },
};

function mapFailure(failure: ProductApiFailure): CaseExperienceFailure {
  switch (failure) {
    case "authentication_unavailable": return "session-unavailable";
    case "operation_forbidden": return "access-revoked";
    case "resource_unavailable": return "resource-unavailable";
    case "conflict": return "refresh-required";
    case "dependency_unavailable": return "dependency-unavailable";
    case "outcome-unknown": return "outcome-unknown";
    case "invalid_request":
    case "invalid-response": return "invalid-response";
  }
}

function mapSummary(summary: Readonly<{
  case_ref: string;
  kind: "authorization" | "reimbursement" | "account";
  state: "active" | "ended";
  record_revision: string;
  state_observed_at: string;
}>): CaseSummaryView {
  return {
    caseRef: summary.case_ref,
    kind: summary.kind,
    state: summary.state,
    recordRevision: summary.record_revision,
    stateObservedAt: summary.state_observed_at,
  };
}

function mapDocuments(page: DocumentPage): CaseWorkspaceView["documents"] {
  if (page.documents.length === 0) {
    return { state: { kind: "empty", message: "Nenhum documento verificado está disponível." }, items: [] };
  }
  return {
    state: { kind: "ready" },
    items: page.documents.map((document) => ({
      documentRef: document.document_ref,
      name: "Documento verificado",
      kindLabel: `Tipo protegido: ${document.document_type_ref}`,
      statusLabel: "Custódia verificada",
      canDownload: true,
    })),
  };
}

function mapDocumentRequests(page: DocumentRequestPage): CaseWorkspaceView["documentRequests"] {
  if (page.requests.length === 0) {
    return { state: { kind: "empty", message: "Não há pedidos de documentos para este caso." }, items: [] };
  }
  return {
    state: { kind: "ready" },
    items: page.requests.map((request) => {
      const mapped = requestStatus[request.disposition];
      const types = request.requested_document_type_refs.length === 0
        ? "Nenhum tipo adicional foi informado."
        : `Tipos protegidos solicitados: ${request.requested_document_type_refs.join(", ")}.`;
      return {
        requestRef: request.request_ref,
        heading: "Pedido de documentos",
        instructions: `${types} Política aplicável: ${request.policy_ref}.`,
        status: mapped.status,
        statusLabel: mapped.label,
        dueAt: null,
        expectedRevision: request.revision,
        canRespond: request.disposition === "open",
      };
    }),
  };
}

function mapIntake(receipt: IntakeReceipt): AuthorizationIntakeProgressView {
  const common = {
    intakeRef: receipt.intake_ref,
    revision: receipt.revision,
  };
  switch (receipt.disposition) {
    case "admitted":
      return {
        ...common,
        state: "admitted",
        stateLabel: "Solicitação recebida",
        description: "O comando foi admitido e ainda não confirma o início do caso.",
      };
    case "dispatching":
      return {
        ...common,
        state: "dispatching",
        stateLabel: "Envio em andamento",
        description: "A solicitação está sendo encaminhada para o processo responsável.",
      };
    case "reconciling":
      return {
        ...common,
        state: "reconciling",
        stateLabel: "Conferindo resultado",
        description: "O portal está conciliando o processamento antes de confirmar o início do caso.",
      };
    case "rejected":
      return {
        ...common,
        state: "rejected",
        stateLabel: "Solicitação rejeitada",
        description: "O comando foi rejeitado. Nenhum início de caso foi confirmado.",
      };
    case "started":
      return {
        ...common,
        state: "started",
        stateLabel: "Caso iniciado",
        description: "O início do caso foi confirmado pelo recibo apresentado abaixo.",
        caseRef: receipt.case_ref!,
        startReceiptRef: receipt.start_receipt_ref!,
      };
  }
}

function workspaceFailure(...failures: ProductApiFailure[]) {
  return failures.find((item) =>
    item === "authentication_unavailable" || item === "operation_forbidden" || item === "conflict");
}

function draftKey(draft: AuthorizationIntakeDraft) {
  return JSON.stringify([
    draft.beneficiaryRef,
    draft.providerRef,
    draft.guideRef,
    draft.procedureCode,
    draft.procedureCategory,
    draft.careCharacter,
    draft.estimatedValueCents,
    ...draft.protectedDocumentRefs,
  ]);
}

export function createCaseExperienceService(
  options: CaseExperienceServiceOptions,
): ProductionCaseExperienceService {
  const product = createPortalProductClient(options);
  // Current authenticated service lifetime only; never persisted across sessions.
  let pending: {
    key: string;
    submission: AuthIntakeSubmission;
    uncertain: boolean;
    inFlight: boolean;
  } | undefined;
  const nextCommandId = options.commandId ?? (() => crypto.randomUUID());

  return {
    product,
    contentUpload: {
      kind: "unavailable",
      message: "A transferência do conteúdo do arquivo ainda não está disponível neste serviço.",
    },
    async listCases(signal, cursor) {
      const result = await product.listCases(signal, cursor);
      if (result.kind === "failure") return { kind: "failure", failure: mapFailure(result.failure) };
      const value: CasePageView = {
        items: result.value.items.map(mapSummary),
        nextCursor: result.value.next_cursor,
        observedAt: result.value.freshness.observed_at,
        validUntil: result.value.freshness.valid_until,
      };
      signal.throwIfAborted();
      if (!isCurrent(value.validUntil)) return { kind: "failure", failure: "refresh-required" };
      return { kind: "success", value };
    },
    async readCase(caseRef, signal) {
      const [detail, documents, requests] = await Promise.all([
        product.readCase(caseRef, signal),
        product.listDocuments(caseRef, signal),
        product.listDocumentRequests(caseRef, signal),
      ]);
      if (detail.kind === "failure") return { kind: "failure", failure: mapFailure(detail.failure) };
      const subordinateFailures = [documents, requests]
        .filter((result): result is Extract<typeof result, { kind: "failure" }> => result.kind === "failure")
        .map((result) => result.failure);
      const blocked = workspaceFailure(...subordinateFailures);
      if (blocked !== undefined) return { kind: "failure", failure: mapFailure(blocked) };

      const summary = mapSummary(detail.value.case);
      const view: CaseWorkspaceView = {
        summary,
        audienceLabel: options.audience === "beneficiary" ? "Área do beneficiário" : "Área do prestador",
        statusLabel: summary.state === "active" ? "Em andamento" : "Encerrada",
        headline: caseKindLabels[summary.kind],
        guidance: "Consulte abaixo somente as informações autorizadas para o vínculo atual.",
        dossier: { state: { kind: "unavailable", message: "O dossiê detalhado não está disponível neste serviço." }, sections: [] },
        chronology: { state: { kind: "unavailable", message: "O histórico cronológico não está disponível neste serviço." }, events: [] },
        documents: documents.kind === "success"
          ? mapDocuments(documents.value)
          : unavailable("Os documentos não puderam ser consultados agora."),
        documentRequests: requests.kind === "success"
          ? mapDocumentRequests(requests.value)
          : unavailable("Os pedidos de documentos não puderam ser consultados agora."),
        communications: unavailable("As mensagens não estão disponíveis neste serviço."),
        commands: unavailable("O índice de recibos não está disponível neste serviço."),
      };
      // Subordinate reads may outlive the original detail grant. No await or
      // cleanup may follow this final retained-deadline check before disclosure.
      signal.throwIfAborted();
      if (!isCurrent(detail.value.freshness.valid_until)) {
        return { kind: "failure", failure: "refresh-required" };
      }
      return { kind: "success", value: view };
    },
    async downloadDocument(documentRef, signal) {
      const result = await product.downloadDocument(documentRef, signal);
      return result.kind === "success"
        ? result
        : { kind: "failure", failure: mapFailure(result.failure) };
    },
    async readAuthorizationIntakeForm(signal) {
      if (options.audience !== "provider") {
        return { kind: "failure", failure: "access-revoked" };
      }
      if (options.intakeFormProvider === undefined) {
        return { kind: "failure", failure: "resource-unavailable" };
      }
      return options.intakeFormProvider.read(signal);
    },
    async submitAuthorization(draft, signal) {
      if (options.audience !== "provider") {
        return { kind: "failure", failure: "access-revoked" };
      }
      const key = draftKey(draft);
      if (pending !== undefined && (pending.key !== key || pending.inFlight)) {
        return { kind: "failure", failure: "outcome-unknown" };
      }
      const submission: AuthIntakeSubmission = pending?.submission ?? {
        schema_version: 1,
        command_id: nextCommandId(),
        beneficiary_ref: draft.beneficiaryRef,
        provider_ref: draft.providerRef,
        guide_ref: draft.guideRef,
        codigo_procedimento_tuss: draft.procedureCode,
        categoria_procedimento: draft.procedureCategory as AuthIntakeSubmission["categoria_procedimento"],
        carater_atendimento: draft.careCharacter as AuthIntakeSubmission["carater_atendimento"],
        valor_estimado_centavos: draft.estimatedValueCents,
        document_refs: [...draft.protectedDocumentRefs],
      };
      const attempt = pending ?? { key, submission, uncertain: false, inFlight: false };
      pending = attempt;
      attempt.inFlight = true;
      let result;
      try {
        result = await product.submitAuthorization(attempt.submission, signal);
        signal.throwIfAborted();
      } catch (error) {
        // Abort may race admission; later refusals cannot resolve that old outcome.
        attempt.uncertain = true;
        throw error;
      } finally {
        attempt.inFlight = false;
      }
      if (result.kind === "success") {
        pending = undefined;
        return { kind: "success", progress: mapIntake(result.value) };
      }
      if (result.failure === "outcome-unknown") attempt.uncertain = true;
      if (!attempt.uncertain) pending = undefined;
      return { kind: "failure", failure: mapFailure(result.failure) };
    },
    async readAuthorizationProgress(intakeRef, signal) {
      if (options.audience !== "provider") {
        return { kind: "failure", failure: "access-revoked" };
      }
      const result = await product.readAuthorizationIntake(intakeRef, signal);
      return result.kind === "success"
        ? { kind: "success", progress: mapIntake(result.value) }
        : { kind: "failure", failure: mapFailure(result.failure) };
    },
  };
}
