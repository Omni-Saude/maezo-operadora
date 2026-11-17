// GERADO; NÃO EDITAR. Regenere com:
//   python src/maezo/portal/web/codegen/c6-4/generate_c64_binding.py
// Contrato: tests/candidate_artifacts/commercial/contract-c6-4.json
// SHA-256 do contrato: d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea
// Pin conferido: config/integrations/amh/commercial-c6-4.source.lock.json (campo sha256)
// Somente forma (tipos de $defs, x-commands, x-status). Não é autoridade de publicação,
// admissão, identidade nem decisão; if/then/else, limites e padrões são do codec do servidor.

export const C64_CONTRACT_SHA256 = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea" as const;
export const C64_SCHEMA_ID = "urn:omni:commercial:proposed:v1" as const;

export const C64_STATUS = {
  "proposal": "PROPOSED",
  "software_implementation": "USER_AUTHORIZED",
  "source_publication": "NOT_PUBLISHED",
  "sales_admission": "NOT_ESTABLISHED",
  "tasy_write_homologation": "NOT_ESTABLISHED",
  "identity_lifecycle": "NOT_ESTABLISHED",
  "provider_binding": "NOT_ESTABLISHED",
  "human_acts": "NOT_ESTABLISHED",
  "real_integration": "NOT_RUN",
  "production": "NO_GO",
} as const;

export const C64_COMMANDS = {
  "operator.get": { payloadSchema: "OperatorGetPayload", resultKind: "operator", scope: "interop/sales.catalog.read", mode: "read" },
  "operator.register": { payloadSchema: "OperatorRegisterPayload", resultKind: "operator", scope: "interop/sales.catalog.manage", mode: "write" },
  "catalog.get": { payloadSchema: "CatalogGetPayload", resultKind: "catalog", scope: "interop/sales.catalog.read", mode: "read" },
  "catalog.draft": { payloadSchema: "CatalogDraftPayload", resultKind: "catalog", scope: "interop/sales.catalog.manage", mode: "write" },
  "catalog.import": { payloadSchema: "CatalogImportPayload", resultKind: "catalog", scope: "interop/sales.catalog.manage", mode: "write" },
  "catalog.review": { payloadSchema: "CatalogReviewPayload", resultKind: "catalog", scope: "interop/sales.catalog.manage", mode: "write" },
  "catalog.publish": { payloadSchema: "CatalogPublishPayload", resultKind: "catalog", scope: "interop/sales.catalog.manage", mode: "write" },
  "catalog.withdraw": { payloadSchema: "CatalogWithdrawPayload", resultKind: "catalog", scope: "interop/sales.catalog.manage", mode: "write" },
  "intake.describe": { payloadSchema: "IntakeDescribePayload", resultKind: "intake_descriptor", scope: "interop/sales.intake", mode: "read" },
  "prospect.save": { payloadSchema: "ProspectSavePayload", resultKind: "prospect", scope: "interop/sales.intake", mode: "write" },
  "contact.request": { payloadSchema: "ContactRequestPayload", resultKind: "contact_request", scope: "interop/sales.intake", mode: "write" },
  "party.prepare": { payloadSchema: "PartyPreparePayload", resultKind: "party_preparation", scope: "interop/sales.intake", mode: "write" },
  "party.submit": { payloadSchema: "PartySubmitPayload", resultKind: "party_linkage", scope: "interop/sales.intake", mode: "write" },
  "party.link": { payloadSchema: "PartyLinkPayload", resultKind: "party_linkage", scope: "interop/sales.intake", mode: "write" },
  "offer.quote": { payloadSchema: "OfferQuotePayload", resultKind: "quote", scope: "interop/sales.contract", mode: "write" },
  "contract.issue": { payloadSchema: "ContractIssuePayload", resultKind: "contract", scope: "interop/sales.contract", mode: "write" },
  "contract.get": { payloadSchema: "ContractGetPayload", resultKind: "contract", scope: "interop/sales.contract", mode: "read" },
  "receivable.ensure": { payloadSchema: "ReceivableEnsurePayload", resultKind: "receivable", scope: "interop/sales.billing.write", mode: "write" },
  "receivable.get": { payloadSchema: "ReceivableGetPayload", resultKind: "receivable", scope: "interop/sales.billing.read", mode: "read" },
  "payment.create": { payloadSchema: "PaymentCreatePayload", resultKind: "payment", scope: "interop/sales.billing.write", mode: "write" },
  "payment.reconcile": { payloadSchema: "PaymentReconcilePayload", resultKind: "payment", scope: "interop/sales.billing.write", mode: "write" },
  "mandate.create": { payloadSchema: "MandateCreatePayload", resultKind: "mandate", scope: "interop/sales.billing.write", mode: "write" },
  "mandate.get": { payloadSchema: "MandateGetPayload", resultKind: "mandate", scope: "interop/sales.billing.read", mode: "read" },
  "mandate.revoke": { payloadSchema: "MandateRevokePayload", resultKind: "mandate", scope: "interop/sales.billing.write", mode: "write" },
  "financial_action.request": { payloadSchema: "Financial_ActionRequestPayload", resultKind: "financial_action", scope: "interop/sales.billing.write", mode: "write" },
  "acceptance.record": { payloadSchema: "AcceptanceRecordPayload", resultKind: "acceptance", scope: "interop/sales.contract", mode: "write" },
  "operation.get": { payloadSchema: "OperationGetPayload", resultKind: "operation_status", scope: "interop/sales.recovery", mode: "read" },
  "operator.choices": { payloadSchema: "OperatorChoicesPayload", resultKind: "operator_choices", scope: "interop/sales.registry.read", mode: "read" },
  "operator.draft": { payloadSchema: "OperatorDraftPayload", resultKind: "operator_proposal", scope: "interop/sales.registry.manage", mode: "write" },
  "operator.review": { payloadSchema: "OperatorReviewPayload", resultKind: "operator_proposal", scope: "interop/sales.registry.manage", mode: "write" },
  "operator.proposal.get": { payloadSchema: "OperatorProposalGetPayload", resultKind: "operator_proposal", scope: "interop/sales.registry.read", mode: "read" },
  "financial_action.get": { payloadSchema: "FinancialActionGetPayload", resultKind: "financial_action", scope: "interop/sales.finance", mode: "read" },
  "operator.activate": { payloadSchema: "OperatorActivatePayload", resultKind: "operator", scope: "interop/sales.registry.manage", mode: "write" },
  "offer.terms": { payloadSchema: "OfferTermsPayload", resultKind: "terms_presentation", scope: "interop/sales.contract", mode: "read" },
  "work.list": { payloadSchema: "WorkListPayload", resultKind: "work_queue", scope: "interop/sales.work.read", mode: "read" },
  "work.claim": { payloadSchema: "WorkClaimPayload", resultKind: "work_item", scope: "interop/sales.work.manage", mode: "write" },
  "work.assign": { payloadSchema: "WorkAssignPayload", resultKind: "work_item", scope: "interop/sales.work.manage", mode: "write" },
  "work.handoff": { payloadSchema: "WorkHandoffPayload", resultKind: "work_item", scope: "interop/sales.work.manage", mode: "write" },
  "work.reconcile": { payloadSchema: "WorkReconcilePayload", resultKind: "work_item", scope: "interop/sales.work.manage", mode: "write" },
  "journey.save": { payloadSchema: "JourneySavePayload", resultKind: "journey_saved", scope: "interop/sales.journey.write", mode: "write" },
  "journey.get": { payloadSchema: "JourneyGetPayload", resultKind: "journey_snapshot", scope: "interop/sales.journey.read", mode: "read" },
  "support.handoff": { payloadSchema: "SupportHandoffPayload", resultKind: "support_handoff", scope: "interop/sales.support", mode: "write" },
} as const;

export type C64Operation = keyof typeof C64_COMMANDS;

export type AcceptanceRecordPayload = {
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "party_linkage_ref": Ref;
  readonly "terms_digest": Digest;
  readonly "decision": "accept" | "decline";
  readonly "terms_review_ref": Ref;
};
export type BillingObligation = {
  readonly "billing_obligation_ref": Ref;
  readonly "billing_period_label": string;
  readonly "due_date": string;
  readonly "money": Money;
};
export type CatalogDraftPayload = {
  readonly "catalog_ref": Ref;
  readonly "rows": readonly CatalogRow[];
};
export type CatalogGetPayload = {
  readonly "catalog_ref": Ref;
  readonly "version": (Revision | null);
};
export type CatalogImportPayload = {
  readonly "catalog_ref": Ref;
  readonly "import_format": "csv_rows_v2";
  readonly "rows": readonly CatalogRow[];
};
export type CatalogNetworkAssociation = {
  readonly "establishment_ref": Ref;
  readonly "unit_ref": (Ref | null);
  readonly "service_ref": Ref;
  readonly "association_source_ref": Ref;
  readonly "source_revision": Revision;
};
export type CatalogPublishPayload = {
  readonly "catalog_ref": Ref;
  readonly "version": Revision;
  readonly "digest": Digest;
};
export type CatalogReviewPayload = {
  readonly "catalog_ref": Ref;
  readonly "version": Revision;
  readonly "digest": Digest;
  readonly "decision": "approved" | "rejected";
};
export type CatalogRow = {
  readonly "product_ref": Ref;
  readonly "product_label": string;
  readonly "product_source_ref": Ref;
  readonly "network_ref": Ref;
  readonly "network_version": Revision;
  readonly "network_label": string;
  readonly "terms_ref": Ref;
  readonly "terms_digest": Digest;
  readonly "price_source_ref": Ref;
  readonly "attributes": readonly {
    readonly "attribute_ref": Ref;
    readonly "label": string;
    readonly "value": string;
  }[];
  readonly "document_refs": readonly Ref[];
  readonly "network_associations": readonly CatalogNetworkAssociation[];
};
export type CatalogWithdrawPayload = {
  readonly "catalog_ref": Ref;
  readonly "version": Revision;
  readonly "digest": Digest;
  readonly "reason_ref": Ref;
};
export type Command = ({
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.register";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorRegisterPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "catalog.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": CatalogGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "catalog.draft";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": CatalogDraftPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "catalog.import";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": CatalogImportPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "catalog.review";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": CatalogReviewPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "catalog.publish";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": CatalogPublishPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "catalog.withdraw";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": CatalogWithdrawPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "intake.describe";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": IntakeDescribePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "prospect.save";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": ProspectSavePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "contact.request";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": ContactRequestPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "party.prepare";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": PartyPreparePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "party.submit";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": PartySubmitPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "party.link";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": PartyLinkPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "offer.quote";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OfferQuotePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "contract.issue";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": ContractIssuePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "contract.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": ContractGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "receivable.ensure";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": ReceivableEnsurePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "receivable.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": ReceivableGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "payment.create";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": PaymentCreatePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "payment.reconcile";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": PaymentReconcilePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "mandate.create";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": MandateCreatePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "mandate.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": MandateGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "mandate.revoke";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": MandateRevokePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "financial_action.request";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": Financial_ActionRequestPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "acceptance.record";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": AcceptanceRecordPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operation.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperationGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.choices";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorChoicesPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.draft";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorDraftPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.review";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorReviewPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.proposal.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorProposalGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "financial_action.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": FinancialActionGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "operator.activate";
  readonly "command_ref": Ref;
  readonly "expected_revision": (Revision | null);
  readonly "payload": OperatorActivatePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "offer.terms";
  readonly "command_ref": Ref;
  readonly "expected_revision": null;
  readonly "payload": OfferTermsPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "work.list";
  readonly "command_ref": Ref;
  readonly "expected_revision": null;
  readonly "payload": WorkListPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "work.claim";
  readonly "command_ref": Ref;
  readonly "expected_revision": Revision;
  readonly "payload": WorkClaimPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "work.assign";
  readonly "command_ref": Ref;
  readonly "expected_revision": Revision;
  readonly "payload": WorkAssignPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "work.handoff";
  readonly "command_ref": Ref;
  readonly "expected_revision": Revision;
  readonly "payload": WorkHandoffPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "work.reconcile";
  readonly "command_ref": Ref;
  readonly "expected_revision": Revision;
  readonly "payload": WorkReconcilePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "journey.save";
  readonly "command_ref": Ref;
  readonly "expected_revision": null;
  readonly "payload": JourneySavePayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "journey.get";
  readonly "command_ref": Ref;
  readonly "expected_revision": null;
  readonly "payload": JourneyGetPayload;
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "command": "support.handoff";
  readonly "command_ref": Ref;
  readonly "expected_revision": Revision;
  readonly "payload": SupportHandoffPayload;
});
export type ContactRequestPayload = {
  readonly "prospect_ref": Ref;
  readonly "contact_ref": Ref;
  readonly "contact_permission_ref": Ref;
  readonly "topic": "product_information" | "contracting_help" | "billing_help";
};
export type ContractGetPayload = {
  readonly "contract_ref": Ref;
};
export type ContractIssuePayload = {
  readonly "quote_ref": Ref;
  readonly "acceptance_receipt_ref": Ref;
  readonly "party_linkage_ref": Ref;
  readonly "terms_digest": Digest;
};
export type ContractIssuedReceipt = {
  readonly "contract_ref": Ref;
  readonly "issuer_receipt_ref": Ref;
  readonly "accepted_receipt_ref": Ref;
  readonly "quote_ref": Ref;
  readonly "terms_digest": Digest;
  readonly "payer_ref": Ref;
  readonly "party_linkage_ref": Ref;
  readonly "issued_at": Timestamp;
  readonly "source_revision": Revision;
};
export type CustomerAcceptanceRead = ({
  readonly "schemaVersion": "customer-acceptance.proposed.v1";
  readonly "status": "recorded";
  readonly "decision": "accepted" | "declined";
  readonly "acceptanceRef": Ref;
  readonly "recordedAt": Timestamp;
} | {
  readonly "schemaVersion": "customer-acceptance.proposed.v1";
  readonly "status": "refused";
  readonly "outcome": "session_absent" | "session_expired" | "session_revoked" | "session_altered" | "wrong_tenant" | "wrong_principal" | "wrong_purpose" | "stale_revision" | "offer_version_mismatch" | "terms_hash_mismatch" | "vendor_seat_refused" | "duplicate_submission" | "authority_unproven" | "source_unavailable";
} | {
  readonly "schemaVersion": "customer-acceptance.proposed.v1";
  readonly "status": "uncertain";
  readonly "correlationRef": Ref;
});
export type CustomerCoverageRead = ({
  readonly "schemaVersion": "customer-coverage.proposed.v1";
  readonly "coverageState": "active" | "revoked";
  readonly "observedAt": Timestamp;
} | {
  readonly "schemaVersion": "customer-coverage.proposed.v1";
  readonly "coverageState": "unknown";
  readonly "unknownReason"?: "source_unavailable" | "identity_unproven" | "purpose_denied" | "stale_generation" | "absent_generation";
  readonly "observedAt": Timestamp;
});
export type CustomerEnrollmentRead = ({
  readonly "schemaVersion": "customer-enrollment.proposed.v1";
  readonly "status": "timeline";
  readonly "enrollmentStatus": "pending" | "issued" | "refused" | "review_required";
  readonly "timeline": readonly {
    readonly "observedAt": Timestamp;
    readonly "status": "pending" | "issued" | "refused" | "review_required";
  }[];
} | {
  readonly "schemaVersion": "customer-enrollment.proposed.v1";
  readonly "status": "refused";
  readonly "outcome": "session_absent" | "session_expired" | "session_revoked" | "session_altered" | "wrong_tenant" | "wrong_principal" | "wrong_purpose" | "stale_revision" | "accepted_commitment_missing" | "accepted_commitment_mismatch" | "duplicate_submission" | "authority_unproven" | "source_unavailable";
} | {
  readonly "schemaVersion": "customer-enrollment.proposed.v1";
  readonly "status": "uncertain";
  readonly "correlationRef": Ref;
});
export type CustomerJourneySnapshot = {
  readonly "journey_ref": Ref;
  readonly "revision": Revision;
  readonly "prospect_ref": (Ref | null);
  readonly "account_ref": (Ref | null);
  readonly "party_ref": (Ref | null);
  readonly "payer_ref": (Ref | null);
  readonly "quote_contexts": readonly JourneyQuoteContext[];
  readonly "acceptance": CustomerAcceptanceRead;
  readonly "contract": (contractResultData | null);
  readonly "enrollment": CustomerEnrollmentRead;
  readonly "coverage": CustomerCoverageRead;
  readonly "documents": readonly JourneyDocument[];
  readonly "pending_items": readonly JourneyPendingItem[];
  readonly "pending_operations": readonly JourneyPendingOperation[];
  readonly "next_actions": readonly JourneyNextAction[];
  readonly "support": CustomerSupportRead;
  readonly "support_topics": readonly JourneySupportTopic[];
  readonly "observed_at": Timestamp;
  readonly "profile_ref": (Ref | null);
  readonly "party_linkage": (PartyLinkage | null);
  readonly "commercial_acceptance": (acceptanceResultData | null);
  readonly "receivable_refs": readonly Ref[];
  readonly "payment_attempt_refs": readonly Ref[];
  readonly "mandate_refs": readonly Ref[];
  readonly "financial_action_refs": readonly Ref[];
};
export type CustomerSupportRead = ({
  readonly "schemaVersion": "sales-support-case.proposed.v2";
  readonly "status": "projection";
  readonly "vocabularyRef": Ref;
  readonly "vocabularyRevision": Revision;
  readonly "caseKind": string;
  readonly "caseKindLabel": string;
  readonly "caseStatus": string;
  readonly "caseStatusLabel": string;
  readonly "caseRef": Ref;
  readonly "protocolRef": (Ref | null);
} | {
  readonly "schemaVersion": "sales-support-case.proposed.v2";
  readonly "status": "refused";
  readonly "outcome": "UNKNOWN_OPERATION" | "CONTRACT_MISMATCH" | "SOURCE_UNAVAILABLE" | "AUTHORITY_UNPROVEN" | "PURPOSE_DENIED" | "STALE_REVISION" | "AUDIT_UNAVAILABLE";
});
export type Digest = string;
export type FinancialActionGetPayload = {
  readonly "action_ref": Ref;
};
export type Financial_ActionRequestPayload = {
  readonly "action": "refund" | "dispute" | "cancel_payment" | "cancel_contract";
  readonly "contract_ref": Ref;
  readonly "attempt_ref": (Ref | null);
  readonly "source_authorization_ref": Ref;
};
export type HostedAction = {
  readonly "action_ref": Ref;
  readonly "url": string;
  readonly "expires_at": (Timestamp | null);
};
export type HostedTermsDocument = {
  readonly "url": string;
  readonly "expires_at": (Timestamp | null);
};
export type IntakeDescribePayload = {
  readonly "stage": "prospect" | "contact" | "party";
};
export type IntakeDescriptor = {
  readonly "descriptor_ref": Ref;
  readonly "revision": Revision;
  readonly "stage": "prospect" | "contact" | "party";
  readonly "valid_until": Timestamp;
  readonly "fields": readonly IntakeField[];
};
export type IntakeField = {
  readonly "field_ref": Ref;
  readonly "label": string;
  readonly "purpose_ref": Ref;
  readonly "value_type": "text" | "email" | "phone" | "document" | "date" | "choice";
  readonly "required": boolean;
  readonly "max_length": number;
  readonly "choices": readonly string[];
};
export type IntakeValue = {
  readonly "field_ref": Ref;
  readonly "value": string;
};
export type JourneyDocument = {
  readonly "document_ref": Ref;
  readonly "source_revision": Revision;
  readonly "document_kind_ref": Ref;
  readonly "label": string;
  readonly "state": "available" | "pending" | "unavailable" | "withdrawn" | "unknown";
  readonly "required": (boolean | null);
  readonly "hosted_document": (HostedTermsDocument | null);
};
export type JourneyGetPayload = {
  readonly "journey_ref": (Ref | null);
  readonly "cursor": (Ref | null);
  readonly "limit": number;
};
export type JourneyNextAction = {
  readonly "action_ref": Ref;
  readonly "label": string;
  readonly "action_kind": "administrative" | "support" | "external_clinical_channel";
  readonly "responsible_party_ref": (Ref | null);
  readonly "topic_ref": (Ref | null);
};
export type JourneyPendingItem = {
  readonly "pending_ref": Ref;
  readonly "source_revision": Revision;
  readonly "requirement_ref": Ref;
  readonly "label": string;
  readonly "responsible_party_ref": (Ref | null);
};
export type JourneyPendingOperation = {
  readonly "command_ref": Ref;
  readonly "operation_ref": Ref;
  readonly "command": "prospect.save" | "contact.request" | "party.prepare" | "party.submit" | "party.link" | "offer.quote" | "offer.terms" | "acceptance.record" | "contract.issue" | "contract.get" | "receivable.ensure" | "receivable.get" | "payment.create" | "payment.reconcile" | "mandate.create" | "mandate.get" | "mandate.revoke" | "financial_action.request" | "financial_action.get" | "journey.save" | "journey.get" | "support.handoff";
  readonly "state": "pending" | "uncertain";
};
export type JourneyPlanChoice = {
  readonly "option_ref": Ref;
  readonly "catalog_ref": Ref;
  readonly "catalog_version": Revision;
  readonly "product_ref": Ref;
  readonly "label": string;
};
export type JourneyQuoteContext = {
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "terms_digest": Digest;
  readonly "valid_until": Timestamp;
  readonly "quote": (quoteResultData | null);
};
export type JourneySavePayload = {
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "terms_digest": Digest;
};
export type JourneySupportTopic = {
  readonly "topic_ref": Ref;
  readonly "label": string;
  readonly "channel_kind": "administrative" | "external_clinical";
};
export type MandateCreatePayload = {
  readonly "contract_ref": Ref;
  readonly "provider": "stripe";
  readonly "choice_ref": Ref;
};
export type MandateGetPayload = {
  readonly "mandate_ref": Ref;
};
export type MandateRevokePayload = {
  readonly "mandate_ref": Ref;
};
export type MandateView = {
  readonly "mandate_ref": Ref;
  readonly "revision": Revision;
  readonly "provider": "stripe" | "infinitepay";
  readonly "state": "pending" | "active" | "revocation_pending" | "revoked" | "failed" | "unknown";
  readonly "provider_receipt_ref": (Ref | null);
  readonly "hosted_action": (HostedAction | null);
  readonly "payment_capabilities": PaymentCapabilitySet;
};
export type Method = "pix" | "card" | "boleto";
export type Money = {
  readonly "amount_minor": number;
  readonly "currency": "BRL";
};
export type OfferQuotePayload = {
  readonly "catalog_ref": Ref;
  readonly "catalog_version": Revision;
  readonly "product_ref": Ref;
  readonly "profile_ref": Ref;
};
export type OfferTermsPayload = {
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "terms_digest": Digest;
};
export type OperationGetPayload = {
  readonly "target_command_ref": Ref;
};
export type OperationalResult = ({
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "status": "succeeded";
  readonly "operation_ref": Ref;
  readonly "reason_code": null;
  readonly "data": (operatorResultData | catalogResultData | intake_descriptorResultData | prospectResultData | contact_requestResultData | party_preparationResultData | party_linkageResultData | quoteResultData | contractResultData | receivableResultData | paymentResultData | mandateResultData | financial_actionResultData | acceptanceResultData | operation_statusResultData | operator_choicesResultData | operator_proposalResultData | terms_presentationResultData | work_queueResultData | work_itemResultData | journey_savedResultData | journey_snapshotResultData | support_handoffResultData);
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "status": "denied" | "conflict" | "uncertain" | "unavailable";
  readonly "operation_ref": (Ref | null);
  readonly "reason_code": "binding_unqualified" | "admission_required" | "identity_unqualified" | "lifecycle_unqualified" | "purpose_denied" | "representation_denied" | "tenant_mismatch" | "actor_denied" | "source_unavailable" | "source_stale" | "authority_revoked" | "invalid_request" | "unsupported_command" | "stale_revision" | "idempotency_conflict" | "reviewer_must_differ" | "catalog_not_published" | "catalog_withdrawn" | "quote_expired" | "terms_mismatch" | "acceptance_missing" | "contract_not_issued" | "proceed_authority_missing" | "payer_unqualified" | "payment_methods_unavailable" | "receivable_mismatch" | "attempt_in_progress" | "provider_mismatch" | "account_mismatch" | "amount_mismatch" | "mandate_not_active" | "provider_authentication_failed" | "reconciliation_required" | "journal_unavailable" | "source_action_required" | "rate_limited" | "terms_review_required" | "terms_presentation_unavailable" | "work_owner_unqualified" | "work_not_claimed" | "work_action_unavailable" | "work_assignment_denied" | "observed_method_unqualified" | "journey_unavailable" | "journey_mismatch" | "support_topic_unavailable" | "support_publication_unqualified" | "association_unqualified" | "payment_capability_unavailable" | "payment_choice_mismatch";
  readonly "data": null;
});
export type OperatorActivatePayload = {
  readonly "proposal_ref": Ref;
};
export type OperatorChoicesPayload = {
  readonly "operator_ref": Ref;
};
export type OperatorDraftPayload = {
  readonly "operator_ref": Ref;
  readonly "display_name": string;
  readonly "legal_entity_ref": Ref;
  readonly "tasy_connection_ref": Ref;
  readonly "payment_connections": OperatorPaymentConnectionChoices;
};
export type OperatorGetPayload = {
  readonly "operator_ref": Ref;
};
export type OperatorPaymentConnectionChoices = {
  readonly "stripe_card": (Ref | null);
  readonly "infinitepay_pix_card": (Ref | null);
  readonly "asaas_boleto": (Ref | null);
};
export type OperatorProposalGetPayload = {
  readonly "proposal_ref": Ref;
};
export type OperatorRegisterPayload = {
  readonly "operator_ref": Ref;
  readonly "display_name": string;
  readonly "legal_entity_ref": Ref;
  readonly "tasy_binding_ref": Ref;
  readonly "psp_binding_refs": {
    readonly "stripe_card": Ref;
    readonly "infinitepay_pix_card": Ref;
    readonly "asaas_boleto": Ref;
  };
};
export type OperatorReviewPayload = {
  readonly "proposal_ref": Ref;
  readonly "decision": "approve" | "reject";
};
export type OriginalOperationalResult = ({
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "status": "succeeded";
  readonly "operation_ref": Ref;
  readonly "reason_code": null;
  readonly "data": (operatorResultData | catalogResultData | intake_descriptorResultData | prospectResultData | contact_requestResultData | party_preparationResultData | party_linkageResultData | quoteResultData | contractResultData | receivableResultData | paymentResultData | mandateResultData | financial_actionResultData | acceptanceResultData | operator_choicesResultData | operator_proposalResultData | RecoveredTermsPresentationResultData | work_queueResultData | work_itemResultData | journey_savedResultData | RecoveredJourneySnapshotResultData | RecoveredSupportHandoffResultData);
} | {
  readonly "schema_version": "omni-commercial.proposed.v1";
  readonly "status": "denied" | "conflict" | "uncertain" | "unavailable";
  readonly "operation_ref": (Ref | null);
  readonly "reason_code": "binding_unqualified" | "admission_required" | "identity_unqualified" | "lifecycle_unqualified" | "purpose_denied" | "representation_denied" | "tenant_mismatch" | "actor_denied" | "source_unavailable" | "source_stale" | "authority_revoked" | "invalid_request" | "unsupported_command" | "stale_revision" | "idempotency_conflict" | "reviewer_must_differ" | "catalog_not_published" | "catalog_withdrawn" | "quote_expired" | "terms_mismatch" | "acceptance_missing" | "contract_not_issued" | "proceed_authority_missing" | "payer_unqualified" | "payment_methods_unavailable" | "receivable_mismatch" | "attempt_in_progress" | "provider_mismatch" | "account_mismatch" | "amount_mismatch" | "mandate_not_active" | "provider_authentication_failed" | "reconciliation_required" | "journal_unavailable" | "source_action_required" | "rate_limited" | "terms_review_required" | "terms_presentation_unavailable" | "work_owner_unqualified" | "work_not_claimed" | "work_action_unavailable" | "work_assignment_denied" | "observed_method_unqualified" | "journey_unavailable" | "journey_mismatch" | "support_topic_unavailable" | "support_publication_unqualified" | "association_unqualified" | "payment_capability_unavailable" | "payment_choice_mismatch";
  readonly "data": null;
});
export type PartyLinkPayload = {
  readonly "prospect_ref": Ref;
  readonly "account_ref": (Ref | null);
  readonly "party_ref": Ref;
  readonly "linkage_receipt_ref": Ref;
};
export type PartyLinkage = {
  readonly "linkage_ref": Ref;
  readonly "revision": Revision;
  readonly "prospect_ref": Ref;
  readonly "account_ref": (Ref | null);
  readonly "party_ref": (Ref | null);
  readonly "payer_ref": (Ref | null);
  readonly "policyholder_ref": (Ref | null);
  readonly "beneficiary_refs": readonly Ref[];
};
export type PartyPreparePayload = {
  readonly "prospect_ref": Ref;
  readonly "account_ref": (Ref | null);
  readonly "roles": readonly ("payer" | "policyholder" | "beneficiary")[];
};
export type PartySubmitPayload = {
  readonly "preparation_ref": Ref;
  readonly "descriptor_ref": Ref;
  readonly "descriptor_revision": Revision;
  readonly "values": readonly IntakeValue[];
};
export type PaymentCapabilitiesQualified = {
  readonly "status": "qualified";
  readonly "scope": PaymentCapabilityScope;
  readonly "capability_ref": Ref;
  readonly "source_revision": Revision;
  readonly "verified_at": Timestamp;
  readonly "valid_until": (Timestamp | null);
  readonly "choices": readonly PaymentCapabilityChoice[];
  readonly "default_card_choice_ref": (Ref | null);
};
export type PaymentCapabilitiesUnavailable = {
  readonly "status": "unavailable";
  readonly "scope": PaymentCapabilityScope;
  readonly "reason": "source_unavailable" | "unqualified" | "revoked" | "unsupported" | "not_applicable" | "refresh_required";
  readonly "choices": readonly never[];
  readonly "default_card_choice_ref": null;
};
export type PaymentCapabilityChoice = {
  readonly "choice_ref": Ref;
  readonly "provider": Provider;
  readonly "method": Method;
  readonly "operation": "payment.create" | "payment.reconcile" | "mandate.create" | "mandate.get" | "mandate.revoke";
  readonly "use": "one_time" | "mandate_authorization" | "invoice_with_mandate";
};
export type PaymentCapabilityScope = {
  readonly "kind": "connection" | "operator" | "contract" | "receivable" | "payment" | "mandate";
  readonly "ref": Ref;
};
export type PaymentCapabilitySet = (PaymentCapabilitiesUnavailable | PaymentCapabilitiesQualified);
export type PaymentCreatePayload = {
  readonly "receivable_ref": Ref;
  readonly "method": Method;
  readonly "provider": Provider;
  readonly "mandate_ref": (Ref | null);
  readonly "choice_ref": Ref;
};
export type PaymentReconcilePayload = {
  readonly "attempt_ref": Ref;
};
export type PaymentView = {
  readonly "attempt_ref": Ref;
  readonly "receivable_ref": Ref;
  readonly "revision": Revision;
  readonly "method": Method;
  readonly "provider": Provider;
  readonly "state": "created" | "pending" | "authorized" | "paid" | "settled" | "failed" | "cancelled" | "uncertain";
  readonly "money": Money;
  readonly "paid_receipt_ref": (Ref | null);
  readonly "settled_receipt_ref": (Ref | null);
  readonly "tasy_ack_state": "pending" | "acknowledged" | "rejected" | "unknown";
  readonly "tasy_ack_receipt_ref": (Ref | null);
  readonly "hosted_action": (HostedAction | null);
  readonly "observed_method": (Method | null);
  readonly "observed_method_receipt_ref": (Ref | null);
  readonly "payment_capabilities": PaymentCapabilitySet;
};
export type ProspectSavePayload = {
  readonly "prospect_ref": (Ref | null);
  readonly "descriptor_ref": Ref;
  readonly "descriptor_revision": Revision;
  readonly "values": readonly IntakeValue[];
};
export type Provider = "stripe" | "infinitepay" | "asaas";
export type PublicCatalogAssociationPresentation = {
  readonly "schemaVersion": "sales-catalog-presentation.proposed.v3";
  readonly "status": "usable";
  readonly "publishedCriteria": readonly ("price_and_conditions" | "modality_and_eligibility" | "network_and_geography" | "accommodation_and_coparticipation" | "waiting_periods" | "source_and_validity")[];
  readonly "products": readonly PublicCatalogAssociationProduct[];
  readonly "networkChannelClassification": "portal_corporativo" | "vendor_channel" | "unclassified";
  readonly "currentnessVerifiedAt": Timestamp;
};
export type PublicCatalogAssociationProduct = {
  readonly "optionRef": string;
  readonly "productName": string;
  readonly "operatorName": string;
  readonly "ansRegistry": PublicQualifiedValue;
  readonly "modality": "individual_familiar" | "empresarial" | "adesao";
  readonly "offerState": "usable" | "unavailable" | "review_required";
  readonly "segmentation": PublicQualifiedValue;
  readonly "geographicScope": PublicQualifiedValue;
  readonly "accommodation": PublicQualifiedValue;
  readonly "coparticipation": PublicQualifiedValue;
  readonly "waitingPeriod": PublicQualifiedValue;
  readonly "cpt": PublicQualifiedValue;
  readonly "price": PublicReferenceState;
  readonly "conditions": PublicReferenceState;
  readonly "documents": PublicReferenceState;
  readonly "network": PublicReferenceState;
  readonly "productVersion": string;
  readonly "operatorAnsRegistry": PublicQualifiedValue;
  readonly "networkVerifiedAt": PublicQualifiedValue;
  readonly "documentAvailability": {
    readonly "mps": PublicReferenceState;
    readonly "glc": PublicReferenceState;
  };
  readonly "networkAssociations": readonly {
    readonly "associationRef": string;
    readonly "establishmentName": PublicQualifiedValue;
    readonly "unitName": PublicQualifiedValue;
    readonly "serviceName": PublicQualifiedValue;
    readonly "verifiedAt": Timestamp;
  }[];
};
export type PublicCatalogAssociationSource = {
  readonly "publicAssociationRef": string;
  readonly "establishmentRef": Ref;
  readonly "unitRef": (Ref | null);
  readonly "serviceRef": Ref;
  readonly "associationSourceRef": Ref;
  readonly "sourceRevision": Revision;
  readonly "establishmentName": PublicCatalogQualifiedValue;
  readonly "unitName": PublicCatalogQualifiedValue;
  readonly "serviceName": PublicCatalogQualifiedValue;
  readonly "verifiedAt": Timestamp;
};
export type PublicCatalogAssociationSourceWire = {
  readonly "schemaVersion": "sales-catalog-read.proposed.v2";
  readonly "status": "usable" | "unavailable" | "review_required";
  readonly "refusalReason"?: "UNKNOWN_OPERATION" | "CONTRACT_MISMATCH" | "SOURCE_UNAVAILABLE" | "AUTHORITY_UNPROVEN" | "PURPOSE_DENIED" | "STALE_REVISION" | "AUDIT_UNAVAILABLE";
  readonly "source": {
    readonly "authorityRef": string;
    readonly "catalogueRef": string;
    readonly "catalogueVersion": string;
    readonly "publicationState": "published" | "withdrawn" | "not_published";
  };
  readonly "currentness": {
    readonly "businessRevision": string;
    readonly "verifiedAt": string;
  };
  readonly "expiry": {
    readonly "dataState": "known" | "unknown";
    readonly "validUntil"?: string;
  };
  readonly "withdrawal": {
    readonly "state": "none_published" | "withdrawn";
    readonly "withdrawalRef"?: string;
  };
  readonly "networkChannelClassification": "portal_corporativo" | "vendor_channel" | "unclassified";
  readonly "publishedCriteria": {
    readonly "comparableCriteria": readonly ("price_and_conditions" | "modality_and_eligibility" | "network_and_geography" | "accommodation_and_coparticipation" | "waiting_periods" | "source_and_validity")[];
    readonly "filterableFields": readonly ("modality" | "segmentation" | "geographic_scope" | "accommodation" | "coparticipation")[];
    readonly "sortableFields": readonly "price_monthly_per_life"[];
  };
  readonly "products": readonly {
    readonly "optionRef": string;
    readonly "productRef": string;
    readonly "productVersion": string;
    readonly "productName": string;
    readonly "operatorName": string;
    readonly "ansRegistry": {
      readonly "dataState": "known" | "unknown";
      readonly "productRegistryCode"?: string;
      readonly "operatorRegistryCode"?: string;
    };
    readonly "modality": "individual_familiar" | "empresarial" | "adesao";
    readonly "offerState": "usable" | "unavailable" | "review_required";
    readonly "segmentation": PublicCatalogQualifiedValue;
    readonly "geographicScope": PublicCatalogQualifiedValue;
    readonly "accommodation": PublicCatalogQualifiedValue;
    readonly "coparticipation": PublicCatalogQualifiedValue;
    readonly "waitingPeriod": PublicCatalogQualifiedValue;
    readonly "cpt": PublicCatalogQualifiedValue;
    readonly "price": {
      readonly "dataState": "known" | "unknown";
      readonly "currency": "BRL";
      readonly "amounts"?: readonly {
        readonly "kind": "monthly_per_life" | "monthly_total_composition";
        readonly "amount": string;
      }[];
    };
    readonly "conditions": {
      readonly "dataState": "known" | "unknown";
      readonly "conditionsRef"?: string;
    };
    readonly "documents": readonly {
      readonly "documentKind": "mps" | "glc";
      readonly "dataState": "known" | "unknown";
      readonly "documentRef"?: string;
    }[];
    readonly "network": {
      readonly "dataState": "known" | "unknown";
      readonly "networkRef"?: string;
      readonly "verifiedAt"?: string;
    };
    readonly "networkAssociations": readonly PublicCatalogAssociationSource[];
  }[];
};
export type PublicCatalogNumericPresentation = {
  readonly "schemaVersion": "sales-catalog-presentation.proposed.v4";
  readonly "status": "usable";
  readonly "publishedCriteria": readonly ("price_and_conditions" | "modality_and_eligibility" | "network_and_geography" | "accommodation_and_coparticipation" | "waiting_periods" | "source_and_validity")[];
  readonly "products": readonly PublicCatalogNumericProduct[];
  readonly "networkChannelClassification": "portal_corporativo" | "vendor_channel" | "unclassified";
  readonly "currentnessVerifiedAt": Timestamp;
};
export type PublicCatalogNumericProduct = {
  readonly "optionRef": string;
  readonly "productName": string;
  readonly "operatorName": string;
  readonly "ansRegistry": PublicQualifiedValue;
  readonly "modality": "individual_familiar" | "empresarial" | "adesao";
  readonly "offerState": "usable" | "unavailable" | "review_required";
  readonly "segmentation": PublicQualifiedValue;
  readonly "geographicScope": PublicQualifiedValue;
  readonly "accommodation": PublicQualifiedValue;
  readonly "coparticipation": PublicQualifiedValue;
  readonly "waitingPeriod": PublicQualifiedValue;
  readonly "cpt": PublicQualifiedValue;
  readonly "price": QualifiedPublicPrice;
  readonly "conditions": PublicReferenceState;
  readonly "documents": PublicReferenceState;
  readonly "network": PublicReferenceState;
  readonly "productVersion": string;
  readonly "operatorAnsRegistry": PublicQualifiedValue;
  readonly "networkVerifiedAt": PublicQualifiedValue;
  readonly "documentAvailability": {
    readonly "mps": PublicReferenceState;
    readonly "glc": PublicReferenceState;
  };
  readonly "networkAssociations": readonly {
    readonly "associationRef": string;
    readonly "establishmentName": PublicQualifiedValue;
    readonly "unitName": PublicQualifiedValue;
    readonly "serviceName": PublicQualifiedValue;
    readonly "verifiedAt": Timestamp;
  }[];
};
export type PublicCatalogNumericSourceWire = {
  readonly "schemaVersion": "sales-catalog-read.proposed.v3";
  readonly "status": "usable" | "unavailable" | "review_required";
  readonly "refusalReason"?: "UNKNOWN_OPERATION" | "CONTRACT_MISMATCH" | "SOURCE_UNAVAILABLE" | "AUTHORITY_UNPROVEN" | "PURPOSE_DENIED" | "STALE_REVISION" | "AUDIT_UNAVAILABLE";
  readonly "source": {
    readonly "authorityRef": string;
    readonly "catalogueRef": string;
    readonly "catalogueVersion": string;
    readonly "publicationState": "published" | "withdrawn" | "not_published";
  };
  readonly "currentness": {
    readonly "businessRevision": string;
    readonly "verifiedAt": string;
  };
  readonly "expiry": {
    readonly "dataState": "known" | "unknown";
    readonly "validUntil"?: string;
  };
  readonly "withdrawal": {
    readonly "state": "none_published" | "withdrawn";
    readonly "withdrawalRef"?: string;
  };
  readonly "networkChannelClassification": "portal_corporativo" | "vendor_channel" | "unclassified";
  readonly "publishedCriteria": {
    readonly "comparableCriteria": readonly ("price_and_conditions" | "modality_and_eligibility" | "network_and_geography" | "accommodation_and_coparticipation" | "waiting_periods" | "source_and_validity")[];
    readonly "filterableFields": readonly ("modality" | "segmentation" | "geographic_scope" | "accommodation" | "coparticipation")[];
    readonly "sortableFields": readonly "price_monthly_per_life"[];
  };
  readonly "products": readonly {
    readonly "optionRef": string;
    readonly "productRef": string;
    readonly "productVersion": string;
    readonly "productName": string;
    readonly "operatorName": string;
    readonly "ansRegistry": {
      readonly "dataState": "known" | "unknown";
      readonly "productRegistryCode"?: string;
      readonly "operatorRegistryCode"?: string;
    };
    readonly "modality": "individual_familiar" | "empresarial" | "adesao";
    readonly "offerState": "usable" | "unavailable" | "review_required";
    readonly "segmentation": PublicCatalogQualifiedValue;
    readonly "geographicScope": PublicCatalogQualifiedValue;
    readonly "accommodation": PublicCatalogQualifiedValue;
    readonly "coparticipation": PublicCatalogQualifiedValue;
    readonly "waitingPeriod": PublicCatalogQualifiedValue;
    readonly "cpt": PublicCatalogQualifiedValue;
    readonly "price": QualifiedPublicPrice;
    readonly "conditions": {
      readonly "dataState": "known" | "unknown";
      readonly "conditionsRef"?: string;
    };
    readonly "documents": readonly {
      readonly "documentKind": "mps" | "glc";
      readonly "dataState": "known" | "unknown";
      readonly "documentRef"?: string;
    }[];
    readonly "network": {
      readonly "dataState": "known" | "unknown";
      readonly "networkRef"?: string;
      readonly "verifiedAt"?: string;
    };
    readonly "networkAssociations": readonly PublicCatalogAssociationSource[];
  }[];
};
export type PublicCatalogPresentation = {
  readonly "schemaVersion": "sales-catalog-presentation.proposed.v2";
  readonly "status": "usable";
  readonly "publishedCriteria": readonly ("price_and_conditions" | "modality_and_eligibility" | "network_and_geography" | "accommodation_and_coparticipation" | "waiting_periods" | "source_and_validity")[];
  readonly "products": readonly PublicCatalogProduct[];
  readonly "networkChannelClassification": "portal_corporativo" | "vendor_channel" | "unclassified";
  readonly "currentnessVerifiedAt": Timestamp;
};
export type PublicCatalogProduct = {
  readonly "optionRef": string;
  readonly "productName": string;
  readonly "operatorName": string;
  readonly "ansRegistry": PublicQualifiedValue;
  readonly "modality": "individual_familiar" | "empresarial" | "adesao";
  readonly "offerState": "usable" | "unavailable" | "review_required";
  readonly "segmentation": PublicQualifiedValue;
  readonly "geographicScope": PublicQualifiedValue;
  readonly "accommodation": PublicQualifiedValue;
  readonly "coparticipation": PublicQualifiedValue;
  readonly "waitingPeriod": PublicQualifiedValue;
  readonly "cpt": PublicQualifiedValue;
  readonly "price": PublicReferenceState;
  readonly "conditions": PublicReferenceState;
  readonly "documents": PublicReferenceState;
  readonly "network": PublicReferenceState;
  readonly "productVersion": string;
  readonly "operatorAnsRegistry": PublicQualifiedValue;
  readonly "networkVerifiedAt": PublicQualifiedValue;
  readonly "documentAvailability": {
    readonly "mps": PublicReferenceState;
    readonly "glc": PublicReferenceState;
  };
};
export type PublicCatalogQualifiedValue = {
  readonly "dataState": "known" | "unknown";
  readonly "value"?: string;
};
export type PublicCatalogResponse = (PublicCatalogNumericPresentation | PublicCatalogUnavailable);
export type PublicCatalogSourceResponse = (PublicCatalogNumericSourceWire | PublicCatalogUnavailable);
export type PublicCatalogSourceWire = {
  readonly "schemaVersion": "sales-catalog-read.proposed.v1";
  readonly "status": "usable" | "unavailable" | "review_required";
  readonly "refusalReason"?: "UNKNOWN_OPERATION" | "CONTRACT_MISMATCH" | "SOURCE_UNAVAILABLE" | "AUTHORITY_UNPROVEN" | "PURPOSE_DENIED" | "STALE_REVISION" | "AUDIT_UNAVAILABLE";
  readonly "source": {
    readonly "authorityRef": string;
    readonly "catalogueRef": string;
    readonly "catalogueVersion": string;
    readonly "publicationState": "published" | "withdrawn" | "not_published";
  };
  readonly "currentness": {
    readonly "businessRevision": string;
    readonly "verifiedAt": string;
  };
  readonly "expiry": {
    readonly "dataState": "known" | "unknown";
    readonly "validUntil"?: string;
  };
  readonly "withdrawal": {
    readonly "state": "none_published" | "withdrawn";
    readonly "withdrawalRef"?: string;
  };
  readonly "networkChannelClassification": "portal_corporativo" | "vendor_channel" | "unclassified";
  readonly "publishedCriteria": {
    readonly "comparableCriteria": readonly ("price_and_conditions" | "modality_and_eligibility" | "network_and_geography" | "accommodation_and_coparticipation" | "waiting_periods" | "source_and_validity")[];
    readonly "filterableFields": readonly ("modality" | "segmentation" | "geographic_scope" | "accommodation" | "coparticipation")[];
    readonly "sortableFields": readonly "price_monthly_per_life"[];
  };
  readonly "products": readonly {
    readonly "optionRef": string;
    readonly "productRef": string;
    readonly "productVersion": string;
    readonly "productName": string;
    readonly "operatorName": string;
    readonly "ansRegistry": {
      readonly "dataState": "known" | "unknown";
      readonly "productRegistryCode"?: string;
      readonly "operatorRegistryCode"?: string;
    };
    readonly "modality": "individual_familiar" | "empresarial" | "adesao";
    readonly "offerState": "usable" | "unavailable" | "review_required";
    readonly "segmentation": PublicCatalogQualifiedValue;
    readonly "geographicScope": PublicCatalogQualifiedValue;
    readonly "accommodation": PublicCatalogQualifiedValue;
    readonly "coparticipation": PublicCatalogQualifiedValue;
    readonly "waitingPeriod": PublicCatalogQualifiedValue;
    readonly "cpt": PublicCatalogQualifiedValue;
    readonly "price": {
      readonly "dataState": "known" | "unknown";
      readonly "currency": "BRL";
      readonly "amounts"?: readonly {
        readonly "kind": "monthly_per_life" | "monthly_total_composition";
        readonly "amount": string;
      }[];
    };
    readonly "conditions": {
      readonly "dataState": "known" | "unknown";
      readonly "conditionsRef"?: string;
    };
    readonly "documents": readonly {
      readonly "documentKind": "mps" | "glc";
      readonly "dataState": "known" | "unknown";
      readonly "documentRef"?: string;
    }[];
    readonly "network": {
      readonly "dataState": "known" | "unknown";
      readonly "networkRef"?: string;
      readonly "verifiedAt"?: string;
    };
  }[];
};
export type PublicCatalogUnavailable = {
  readonly "schemaVersion": "sales-catalog.proposed.v1";
  readonly "status": "unavailable";
  readonly "reason": "source_unavailable";
};
export type PublicPriceFact = {
  readonly "priceRef": string;
  readonly "money": PublicPriceMoney;
  readonly "amountKind": "reference" | "estimate" | "exact_unit" | "exact_total";
  readonly "unit": "per_life" | "per_contract" | "published_composition";
  readonly "billingPeriod": {
    readonly "periodRef": string;
    readonly "kind": "monthly" | "one_time" | "source_defined";
    readonly "label": string;
  };
  readonly "applicability": {
    readonly "contextRef": string;
    readonly "version": string;
    readonly "label": string;
    readonly "comparisonBasisRef": (string | null);
  };
  readonly "currentness": {
    readonly "sourceRevision": string;
    readonly "verifiedAt": Timestamp;
    readonly "validUntil": (Timestamp | null);
  };
};
export type PublicPriceMoney = {
  readonly "amountMinor": number;
  readonly "currency": "BRL";
};
export type PublicQualifiedValue = ({
  readonly "dataState": "unknown";
} | {
  readonly "dataState": "known";
  readonly "value": string;
});
export type PublicReferenceState = {
  readonly "dataState": "known" | "unknown";
};
export type QualifiedPublicPrice = ({
  readonly "dataState": "unknown";
} | {
  readonly "dataState": "known";
  readonly "amounts": readonly PublicPriceFact[];
});
export type ReceivableEnsurePayload = {
  readonly "contract_ref": Ref;
  readonly "billing_obligation_ref": Ref;
};
export type ReceivableGetPayload = {
  readonly "receivable_ref": Ref;
};
export type RecoveredCustomerJourneySnapshot = {
  readonly "journey_ref": Ref;
  readonly "revision": Revision;
  readonly "prospect_ref": (Ref | null);
  readonly "account_ref": (Ref | null);
  readonly "party_ref": (Ref | null);
  readonly "payer_ref": (Ref | null);
  readonly "quote_contexts": readonly JourneyQuoteContext[];
  readonly "acceptance": CustomerAcceptanceRead;
  readonly "contract": (contractResultData | null);
  readonly "enrollment": CustomerEnrollmentRead;
  readonly "coverage": CustomerCoverageRead;
  readonly "documents": readonly RecoveredJourneyDocument[];
  readonly "pending_items": readonly JourneyPendingItem[];
  readonly "pending_operations": readonly JourneyPendingOperation[];
  readonly "next_actions": readonly JourneyNextAction[];
  readonly "support": CustomerSupportRead;
  readonly "support_topics": readonly JourneySupportTopic[];
  readonly "observed_at": Timestamp;
  readonly "profile_ref": (Ref | null);
  readonly "party_linkage": (PartyLinkage | null);
  readonly "commercial_acceptance": (acceptanceResultData | null);
  readonly "receivable_refs": readonly Ref[];
  readonly "payment_attempt_refs": readonly Ref[];
  readonly "mandate_refs": readonly Ref[];
  readonly "financial_action_refs": readonly Ref[];
};
export type RecoveredJourneyDocument = {
  readonly "document_ref": Ref;
  readonly "source_revision": Revision;
  readonly "document_kind_ref": Ref;
  readonly "label": string;
  readonly "state": "available" | "pending" | "unavailable" | "withdrawn" | "unknown";
  readonly "required": (boolean | null);
  readonly "hosted_document": null;
};
export type RecoveredJourneySnapshotResultData = {
  readonly "kind": "journey_snapshot";
  readonly "journeys": readonly RecoveredCustomerJourneySnapshot[];
  readonly "next_cursor": (Ref | null);
  readonly "plan_choices": readonly never[];
};
export type RecoveredSupportHandoffResultData = {
  readonly "kind": "support_handoff";
  readonly "journey_ref": Ref;
  readonly "handoff_ref": Ref;
  readonly "source_revision": Revision;
  readonly "state": "pending" | "received" | "refused";
  readonly "source_receipt_ref": (Ref | null);
  readonly "case_read": CustomerSupportRead;
  readonly "external_handoff": null;
};
export type RecoveredTermsPresentationResultData = {
  readonly "kind": "terms_presentation";
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "terms_digest": Digest;
  readonly "terms_review_ref": Ref;
  readonly "review_state": "pending" | "presented";
  readonly "presentation_receipt_ref": (Ref | null);
  readonly "hosted_document": null;
};
export type Ref = string;
export type Revision = number;
export type SupportHandoffPayload = {
  readonly "journey_ref": Ref;
  readonly "topic_ref": Ref;
};
export type Timestamp = string;
export type WorkAssignPayload = {
  readonly "work_ref": Ref;
  readonly "assignee_ref": Ref;
};
export type WorkAssignee = {
  readonly "assignee_ref": Ref;
  readonly "display_label": string;
  readonly "responsible_queue_refs": readonly Ref[];
};
export type WorkClaimPayload = {
  readonly "work_ref": Ref;
};
export type WorkHandoffPayload = {
  readonly "work_ref": Ref;
  readonly "action_ref": Ref;
};
export type WorkItem = {
  readonly "work_ref": Ref;
  readonly "revision": Revision;
  readonly "responsible_queue_ref": Ref;
  readonly "original_operation_ref": Ref;
  readonly "original_command": "operator.get" | "operator.register" | "catalog.get" | "catalog.draft" | "catalog.import" | "catalog.review" | "catalog.publish" | "catalog.withdraw" | "intake.describe" | "prospect.save" | "contact.request" | "party.prepare" | "party.submit" | "party.link" | "offer.quote" | "contract.issue" | "contract.get" | "receivable.ensure" | "receivable.get" | "payment.create" | "payment.reconcile" | "mandate.create" | "mandate.get" | "mandate.revoke" | "financial_action.request" | "acceptance.record" | "operator.choices" | "operator.draft" | "operator.review" | "operator.proposal.get" | "financial_action.get" | "operator.activate" | "offer.terms" | "journey.save" | "journey.get" | "support.handoff";
  readonly "target_kind": "operation" | "contract" | "receivable" | "payment" | "financial_action";
  readonly "target_ref": Ref;
  readonly "state": WorkState;
  readonly "reason_code": "uncertain_effect" | "orphaned_effect" | "source_action_required" | "accounting_pending" | "source_conflict";
  readonly "owner_ref": (Ref | null);
  readonly "handoff_ref": (Ref | null);
  readonly "handoff_receipt_ref": (Ref | null);
  readonly "resolution_receipt_ref": (Ref | null);
  readonly "next_actions": readonly WorkNextAction[];
};
export type WorkListPayload = {
  readonly "states": readonly WorkState[];
  readonly "cursor": (Ref | null);
  readonly "limit": number;
};
export type WorkNextAction = {
  readonly "action_ref": Ref;
  readonly "action_kind": "query_source" | "request_source_execution" | "request_human_review";
};
export type WorkReconcilePayload = {
  readonly "work_ref": Ref;
};
export type WorkState = "open" | "claimed" | "handoff_pending" | "awaiting_source" | "resolved";
export type acceptanceResultData = {
  readonly "kind": "acceptance";
  readonly "acceptance_receipt_ref": Ref;
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "party_linkage_ref": Ref;
  readonly "terms_digest": Digest;
  readonly "decision": "accepted" | "declined";
  readonly "recorded_at": Timestamp;
  readonly "terms_review_ref": Ref;
};
export type catalogResultData = {
  readonly "kind": "catalog";
  readonly "catalog_ref": Ref;
  readonly "version": Revision;
  readonly "revision": Revision;
  readonly "digest": Digest;
  readonly "state": "draft" | "approved" | "rejected" | "published" | "withdrawn";
  readonly "rows": readonly CatalogRow[];
};
export type contact_requestResultData = {
  readonly "kind": "contact_request";
  readonly "contact_request_ref": Ref;
  readonly "revision": Revision;
  readonly "state": "requested" | "suppressed" | "delivered" | "failed";
};
export type contractResultData = {
  readonly "kind": "contract";
  readonly "contract_ref": Ref;
  readonly "revision": Revision;
  readonly "state": "pending" | "issued" | "refused" | "cancelled";
  readonly "issued_receipt": (ContractIssuedReceipt | null);
  readonly "billing_obligations": readonly BillingObligation[];
  readonly "payment_capabilities": PaymentCapabilitySet;
};
export type financial_actionResultData = {
  readonly "kind": "financial_action";
  readonly "action_ref": Ref;
  readonly "revision": Revision;
  readonly "action": "refund" | "dispute" | "cancel_payment" | "cancel_contract";
  readonly "state": "requested" | "pending" | "completed" | "refused" | "uncertain";
  readonly "source_receipt_ref": (Ref | null);
};
export type intake_descriptorResultData = {
  readonly "kind": "intake_descriptor";
  readonly "descriptor": IntakeDescriptor;
};
export type journey_savedResultData = {
  readonly "kind": "journey_saved";
  readonly "journey_ref": Ref;
  readonly "revision": Revision;
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "terms_digest": Digest;
};
export type journey_snapshotResultData = {
  readonly "kind": "journey_snapshot";
  readonly "journeys": readonly CustomerJourneySnapshot[];
  readonly "next_cursor": (Ref | null);
  readonly "plan_choices": readonly JourneyPlanChoice[];
};
export type mandateResultData = {
  readonly "kind": "mandate";
  readonly "mandate": MandateView;
};
export type operation_statusResultData = {
  readonly "kind": "operation_status";
  readonly "target_command_ref": Ref;
  readonly "original_command": "operator.get" | "operator.register" | "catalog.get" | "catalog.draft" | "catalog.import" | "catalog.review" | "catalog.publish" | "catalog.withdraw" | "intake.describe" | "prospect.save" | "contact.request" | "party.prepare" | "party.submit" | "party.link" | "offer.quote" | "contract.issue" | "contract.get" | "receivable.ensure" | "receivable.get" | "payment.create" | "payment.reconcile" | "mandate.create" | "mandate.get" | "mandate.revoke" | "financial_action.request" | "acceptance.record" | "operator.choices" | "operator.draft" | "operator.review" | "operator.proposal.get" | "financial_action.get" | "operator.activate" | "offer.terms" | "work.list" | "work.claim" | "work.assign" | "work.handoff" | "work.reconcile" | "journey.save" | "journey.get" | "support.handoff";
  readonly "state": "pending" | "uncertain" | "completed";
  readonly "original_result": (OriginalOperationalResult | null);
};
export type operatorResultData = {
  readonly "kind": "operator";
  readonly "operator_ref": Ref;
  readonly "display_name": string;
  readonly "revision": Revision;
  readonly "configuration_state": "proposed" | "qualified" | "revoked";
  readonly "available_methods": readonly Method[];
  readonly "contracting_ready": boolean;
  readonly "payment_capabilities": PaymentCapabilitySet;
};
export type operator_choicesResultData = {
  readonly "kind": "operator_choices";
  readonly "operator_ref": Ref;
  readonly "legal_entities": readonly {
    readonly "legal_entity_ref": Ref;
    readonly "label": string;
  }[];
  readonly "tasy_connections": readonly {
    readonly "connection_ref": Ref;
    readonly "label": string;
    readonly "legal_entity_ref": Ref;
  }[];
  readonly "payment_connections": readonly {
    readonly "connection_ref": Ref;
    readonly "label": string;
    readonly "legal_entity_ref": Ref;
    readonly "provider": Provider;
    readonly "methods": readonly Method[];
    readonly "payment_capabilities": PaymentCapabilitySet;
  }[];
};
export type operator_proposalResultData = {
  readonly "kind": "operator_proposal";
  readonly "proposal_ref": Ref;
  readonly "operator_ref": Ref;
  readonly "revision": Revision;
  readonly "state": "draft" | "reviewed" | "rejected" | "active" | "superseded";
  readonly "display_name": string;
  readonly "legal_entity_ref": Ref;
  readonly "tasy_connection_ref": Ref;
  readonly "payment_connections": OperatorPaymentConnectionChoices;
  readonly "proposal_digest": Digest;
  readonly "author_ref": Ref;
  readonly "reviewer_ref": (Ref | null);
  readonly "reviewed_revision": (Revision | null);
  readonly "active_binding_revision": (Revision | null);
};
export type party_linkageResultData = {
  readonly "kind": "party_linkage";
  readonly "linkage": PartyLinkage;
};
export type party_preparationResultData = {
  readonly "kind": "party_preparation";
  readonly "preparation_ref": Ref;
  readonly "descriptor": IntakeDescriptor;
};
export type paymentResultData = {
  readonly "kind": "payment";
  readonly "payment": PaymentView;
};
export type prospectResultData = {
  readonly "kind": "prospect";
  readonly "prospect_ref": Ref;
  readonly "profile_ref": Ref;
  readonly "revision": Revision;
};
export type quoteResultData = {
  readonly "kind": "quote";
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "catalog_ref": Ref;
  readonly "catalog_version": Revision;
  readonly "product_ref": Ref;
  readonly "network_version": Revision;
  readonly "terms_digest": Digest;
  readonly "money": Money;
  readonly "valid_until": Timestamp;
  readonly "contractable": boolean;
};
export type receivableResultData = {
  readonly "kind": "receivable";
  readonly "receivable_ref": Ref;
  readonly "revision": Revision;
  readonly "contract_ref": Ref;
  readonly "payer_ref": Ref;
  readonly "billing_obligation_ref": Ref;
  readonly "due_date": string;
  readonly "money": Money;
  readonly "state": "open" | "paid" | "cancelled" | "unknown";
  readonly "source_receipt_ref": Ref;
  readonly "payment_capabilities": PaymentCapabilitySet;
};
export type support_handoffResultData = {
  readonly "kind": "support_handoff";
  readonly "journey_ref": Ref;
  readonly "handoff_ref": Ref;
  readonly "source_revision": Revision;
  readonly "state": "pending" | "received" | "refused";
  readonly "source_receipt_ref": (Ref | null);
  readonly "case_read": CustomerSupportRead;
  readonly "external_handoff": (HostedTermsDocument | null);
};
export type terms_presentationResultData = {
  readonly "kind": "terms_presentation";
  readonly "quote_ref": Ref;
  readonly "source_revision": Revision;
  readonly "terms_digest": Digest;
  readonly "terms_review_ref": Ref;
  readonly "review_state": "pending" | "presented";
  readonly "presentation_receipt_ref": (Ref | null);
  readonly "hosted_document": HostedTermsDocument;
};
export type work_itemResultData = {
  readonly "kind": "work_item";
  readonly "item": WorkItem;
};
export type work_queueResultData = {
  readonly "kind": "work_queue";
  readonly "items": readonly WorkItem[];
  readonly "assignees": readonly WorkAssignee[];
  readonly "next_cursor": (Ref | null);
};

export type C64Document = Command | OperationalResult;
