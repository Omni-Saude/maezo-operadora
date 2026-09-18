import type {
  AuthorizationIntakeFormView,
  AuthorizationIntakeProgressView,
  CasePageView,
  CaseWorkspaceView,
  CommandProgressView,
} from "../caseExperienceModels";

// Synthetic UI fixtures only. This module is imported by tests and never by the application entrypoint.
export const acceptedCommand: CommandProgressView = {
  commandRef: "command-synthetic-1",
  actionLabel: "Envio da solicitação",
  state: "accepted",
  stateLabel: "Recebida para processamento",
  description: "A solicitação foi registrada e aguarda confirmação do processamento.",
  updatedAt: "2026-09-10T12:00:00Z",
};

export const admittedIntake: AuthorizationIntakeProgressView = {
  intakeRef: "intake_synthetic_1",
  state: "admitted",
  stateLabel: "Recebida para processamento",
  description: "A solicitação foi admitida e aguarda o início confirmado do caso.",
  updatedAt: "2026-09-10T12:00:00Z",
};

export const executedCommand: CommandProgressView = {
  commandRef: "command-synthetic-2",
  actionLabel: "Resposta ao pedido de documentos",
  state: "executed",
  stateLabel: "Execução confirmada",
  description: "A resposta foi correlacionada ao pedido aberto.",
  updatedAt: "2026-09-10T12:05:00Z",
  receipt: {
    receiptRef: "receipt-synthetic-2",
    recordedAt: "2026-09-10T12:05:00Z",
    resultLabel: "Resposta processada",
  },
};

export const casePageFixture: CasePageView = {
  items: [
    {
      caseRef: "case_synthetic_auth_1",
      kind: "authorization",
      state: "active",
      recordRevision: "90071992547409930001",
      stateObservedAt: "2026-09-10T11:58:00Z",
    },
  ],
  nextCursor: null,
  observedAt: "2026-09-10T12:00:00Z",
  validUntil: "2026-09-10T12:01:00Z",
};

export const workspaceFixture: CaseWorkspaceView = {
  summary: casePageFixture.items[0],
  audienceLabel: "Autorização prévia",
  statusLabel: "Documentos solicitados",
  headline: "Solicitação de autorização",
  guidance: "Há um pedido de documentos aberto para esta solicitação.",
  dossier: {
    state: { kind: "ready" },
    sections: [
      {
        sectionRef: "request",
        heading: "Solicitação",
        fields: [
          {
            fieldRef: "procedure",
            label: "Procedimento",
            value: "Procedimento sintético",
            provenanceLabel: "Guia informada pelo prestador",
          },
        ],
      },
    ],
  },
  chronology: {
    state: { kind: "ready" },
    events: [
      {
        eventRef: "event-synthetic-1",
        occurredAt: "2026-09-10T11:55:00Z",
        heading: "Documentos solicitados",
        description: "A equipe pediu informações adicionais para seguir com a análise.",
        sourceLabel: "Portal Maezo",
        statusLabel: "Entregue na caixa do portal",
      },
    ],
  },
  documents: {
    state: { kind: "ready" },
    items: [
      {
        documentRef: "document_synthetic_1",
        name: "Pedido médico",
        kindLabel: "Documento clínico protegido",
        statusLabel: "Verificado",
        recordedAt: "2026-09-10T11:30:00Z",
        canDownload: true,
      },
    ],
  },
  documentRequests: {
    state: { kind: "ready" },
    items: [
      {
        requestRef: "request_synthetic_1",
        heading: "Envie o laudo solicitado",
        instructions: "Responda a este pedido com o documento indicado.",
        status: "open",
        statusLabel: "Resposta pendente",
        dueAt: "2026-09-12T12:00:00Z",
        expectedRevision: "90071992547409930002",
        canRespond: true,
      },
    ],
  },
  communications: {
    state: { kind: "ready" },
    items: [
      {
        communicationRef: "communication_synthetic_1",
        sentAt: "2026-09-10T11:55:00Z",
        senderLabel: "Equipe de autorização",
        subject: "Documentos adicionais",
        body: "Consulte o pedido de documentos desta solicitação.",
        deliveryLabel: "Entregue nesta caixa do portal",
      },
    ],
  },
  commands: {
    state: { kind: "ready" },
    items: [acceptedCommand, executedCommand],
  },
};

export const intakeFormFixture: AuthorizationIntakeFormView = {
  beneficiaries: [{ ref: "beneficiary_synthetic_1", label: "Beneficiário autorizado" }],
  providers: [{ ref: "provider_synthetic_1", label: "Prestador autorizado" }],
  guides: [{ ref: "guide_ref_synthetic_1", label: "Guia autorizada 1" }],
  finalizedDocuments: [{ ref: "document_synthetic_finalized_1", label: "Pedido médico verificado" }],
  procedureCategories: [{ value: "consulta", label: "Consulta" }],
  careCharacters: [{ value: "eletivo", label: "Eletivo" }],
};
