// UI-only, audience-safe view models. Production adapters must map the reviewed generated
// OpenAPI client into these shapes; these types are not transport DTOs or authorization inputs.
export type PortalAudience = "staff" | "beneficiary" | "provider";

export type ResourceState =
  | { kind: "ready" }
  | { kind: "loading" }
  | { kind: "empty"; message: string }
  | { kind: "unavailable"; message: string };

export type CaseSummaryView = Readonly<{
  caseRef: string;
  kind: "authorization" | "reimbursement" | "account";
  state: "active" | "ended";
  recordRevision: string;
  stateObservedAt: string;
}>;

export type CasePageView = Readonly<{
  items: readonly CaseSummaryView[];
  nextCursor: string | null;
  observedAt: string;
  validUntil: string;
}>;

export type DossierFieldView = Readonly<{
  fieldRef: string;
  label: string;
  value: string;
  provenanceLabel?: string;
}>;

export type DossierSectionView = Readonly<{
  sectionRef: string;
  heading: string;
  description?: string;
  fields: readonly DossierFieldView[];
}>;

export type ChronologyEventView = Readonly<{
  eventRef: string;
  occurredAt: string;
  heading: string;
  description: string;
  sourceLabel: string;
  statusLabel?: string;
}>;

export type DocumentView = Readonly<{
  documentRef: string;
  name: string;
  kindLabel: string;
  statusLabel: string;
  recordedAt?: string;
  canDownload: boolean;
}>;

export type DocumentRequestView = Readonly<{
  requestRef: string;
  heading: string;
  instructions: string;
  status: "open" | "processing" | "answered" | "expired" | "replaced";
  statusLabel: string;
  dueAt: string | null;
  expectedRevision: string;
  canRespond: boolean;
}>;

export type CommunicationView = Readonly<{
  communicationRef: string;
  sentAt: string;
  senderLabel: string;
  subject: string;
  body: string;
  deliveryLabel: string;
}>;

type CommandProgressBase = Readonly<{
  commandRef: string;
  actionLabel: string;
  stateLabel: string;
  description: string;
  updatedAt: string;
}>;

export type CommandProgressView =
  | (CommandProgressBase &
      Readonly<{
        state: "accepted" | "reconciling" | "conflict" | "rejected";
      }>)
  | (CommandProgressBase &
      Readonly<{
        state: "executed";
        receipt: Readonly<{
    receiptRef: string;
    recordedAt: string;
    resultLabel: string;
        }>;
      }>);

export type CaseWorkspaceView = Readonly<{
  summary: CaseSummaryView;
  audienceLabel: string;
  statusLabel: string;
  headline: string;
  guidance: string;
  dossier: Readonly<{ state: ResourceState; sections: readonly DossierSectionView[] }>;
  chronology: Readonly<{ state: ResourceState; events: readonly ChronologyEventView[] }>;
  documents: Readonly<{ state: ResourceState; items: readonly DocumentView[] }>;
  documentRequests: Readonly<{ state: ResourceState; items: readonly DocumentRequestView[] }>;
  communications: Readonly<{ state: ResourceState; items: readonly CommunicationView[] }>;
  commands: Readonly<{ state: ResourceState; items: readonly CommandProgressView[] }>;
}>;

export type CaseExperienceFailure =
  | "session-unavailable"
  | "access-revoked"
  | "resource-unavailable"
  | "refresh-required"
  | "dependency-unavailable"
  | "outcome-unknown"
  | "invalid-response";

export type ExperienceResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: CaseExperienceFailure }>;

// One service object belongs to one authenticated context. Replace its identity when the
// session, tenant or represented relationship changes; never retarget an existing object.
export interface CaseExperienceService {
  listCases(signal: AbortSignal, cursor?: string): Promise<ExperienceResult<CasePageView>>;
  readCase(caseRef: string, signal: AbortSignal): Promise<ExperienceResult<CaseWorkspaceView>>;
  downloadDocument(documentRef: string, signal: AbortSignal): Promise<ExperienceResult<Blob>>;
}

export type AuthorizationIntakeDraft = Readonly<{
  beneficiaryRef: string;
  providerRef: string;
  guideRef: string;
  procedureCode: string;
  procedureCategory: string;
  careCharacter: string;
  estimatedValueCents: string;
  protectedDocumentRefs: readonly string[];
}>;

export type AuthorizationIntakeFormView = Readonly<{
  beneficiaries: readonly Readonly<{ ref: string; label: string }>[];
  providers: readonly Readonly<{ ref: string; label: string }>[];
  guides: readonly Readonly<{ ref: string; label: string }>[];
  finalizedDocuments: readonly Readonly<{ ref: string; label: string }>[];
  procedureCategories: readonly Readonly<{ value: string; label: string }>[];
  careCharacters: readonly Readonly<{ value: string; label: string }>[];
}>;

type AuthorizationIntakeProgressBase = Readonly<{
  intakeRef: string;
  stateLabel: string;
  description: string;
  revision?: string;
  updatedAt?: string;
}>;

export type AuthorizationIntakeProgressView =
  | (AuthorizationIntakeProgressBase &
      Readonly<{ state: "admitted" | "dispatching" | "reconciling" | "rejected" }>)
  | (AuthorizationIntakeProgressBase &
      Readonly<{
        state: "started";
        caseRef: string;
        startReceiptRef: string;
      }>);

export type AuthorizationIntakeResult =
  | Readonly<{ kind: "success"; progress: AuthorizationIntakeProgressView }>
  | Readonly<{ kind: "failure"; failure: CaseExperienceFailure }>;

export type AuthorizationRecoveryEntryView = Readonly<{
  commandId: string;
  progress: AuthorizationIntakeProgressView;
}>;

export type AuthorizationRecoveryPageView = Readonly<{
  items: readonly AuthorizationRecoveryEntryView[];
  nextCursor: string | null;
}>;

export type PendingAuthorizationObservationResult =
  | Readonly<{ kind: "success"; progress: AuthorizationIntakeProgressView }>
  | Readonly<{ kind: "not-observed"; commandId: string }>
  | Readonly<{ kind: "none" }>
  | Readonly<{ kind: "failure"; failure: CaseExperienceFailure }>;

export interface ProviderAuthorizationService extends CaseExperienceService {
  readAuthorizationIntakeForm(signal: AbortSignal): Promise<
    ExperienceResult<AuthorizationIntakeFormView>
  >;
  submitAuthorization(
    draft: AuthorizationIntakeDraft,
    signal: AbortSignal,
  ): Promise<AuthorizationIntakeResult>;
  discoverAuthorizationIntakes(
    signal: AbortSignal,
    cursor?: string,
  ): Promise<ExperienceResult<AuthorizationRecoveryPageView>>;
  observePendingAuthorization(
    signal: AbortSignal,
  ): Promise<PendingAuthorizationObservationResult>;
}
