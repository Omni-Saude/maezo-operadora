# Portal AUTH/ESC communications and history v1

Engineering contract extension for E04. Status: DRAFT; independent source-contract review required before user-text admission/disclosure activation. References: ADR0006, ADR0049 D3/D4; SP-OP-AUTH-001 Portal AUTH E01 approved ea4913d9 (durable intended-recipient inbox); SP-OP-ESCALATION-001 third-party text and PHI notes boundary. Does not modify BPMN, decisions, clinical policy, notification deadlines or AMH wire contracts.

## Product and privacy scope

A communication is a durable protected message with explicit intended recipients. `inbox_available` means the message and recipient inbox rows committed in the same transaction and are discoverable by an authorized recipient's inbox read. It does not mean the user read it, acknowledged legal notice, received email or WhatsApp, or that a workflow ran. No read receipt is invented. Posting text is never a task decision, clinical advice, process start, message correlation or agent instruction. If content later feeds an agent it must cross the existing untrusted-content boundary; this package does not feed agents.

The existing AUTH contract expressly supports protected requests and human-evidence-bound notices and current recipient disclosure. It does not grant free-text disclosure to every case participant. This extension permits free text only in a separately deployed PHI content service, authenticated with the same human session and separate exact `create_content`/`read_content` resource grant. User-authored text is stored encrypted without semantic alteration. No free text, subject, preview, personal name, attachment name, clinical notes, denial reasoning, or raw source ID crosses the General BFF, general history, audit event, URL or log. All errors are fixed safe classifications.

General API serves only separately granted structural fields. PHI content paths are routed to the PHI deployment, not mounted in General create_app. A protected same-origin route and opaque content ref are not bearer authorization. PHI service checks live resource/recipient/consent authority on every content read and verifies the body belongs to the exact message and case. Encryption/key/TLS/database role/network-zone/retention qualification remains deployment-owned; constructor arguments do not prove them.

## Closed browser/API contracts

- PHI `POST /api/v1/phi/cases/{case_ref}/communication-content`: `command_id`, `body` (untrusted text; transport budget only, no invented clinical max). Responds `body_ref`, `command_id`, `disposition=preserved`. Durable encrypted body is bound to tenant/environment/case/authenticated sender/current content policy receipt. Same command/body recovers the same reference; different body conflicts.
- General `POST /api/v1/portal/cases/{case_ref}/communications`: `command_id`, `body_ref`, `recipient_set_ref`. Browser never selects raw principal/issuer/tenant/audience or grants. Current authority resolves the exact intended recipient set from an independently governed source; no group-wide broadcast/wildcard/default. It binds the protected body/sender/case, immutable command digest and recipient-policy revision. Atomic publication verifies preserved body ownership, writes message + intended recipients + discoverable inbox rows + structural history event. Same command/payload/recipient authority digest recovers; differing payload conflicts. No network effect inside this transaction.
- General `GET /cases/{case_ref}/communications`: page of closed `{communication_ref,sender_kind,authored_at,inbox_available_at,delivery_state,body_ref}`. `sender_kind` is staff/beneficiary/provider/system, not a personal identity; `delivery_state` is only `inbox_available`. Each listed row requires current case+message+recipient+field disclosure authority. The page is a bounded first page with opaque cursor, ordered by `(sequence,communication_ref)`; zero items is not used to hide missing authority.
- PHI `GET /api/v1/phi/cases/{case_ref}/communications/{communication_ref}/content`: returns `{communication_ref,body}` only after current exact content read authorization, intended-recipient validation and ciphertext digest/AAD verification. No persistent cache/browser storage. Sender reads require an explicit recipient/disclosure grant too; creation alone grants no later read.
- General `GET /cases/{case_ref}/history`: page of closed structural `{event_ref,sequence,occurred_at,kind,communication_ref,command_ref,receipt_ref}`. Fixed semantic kind `communication_available` produces safe UI label `Comunicação disponível`; `command_receipt_indexed` means only an authenticated existing receipt was indexed. No arbitrary label/detail/subject field. All optional refs require separate explicit field/action authority. The stream is the portal's durable local history, not a claim of complete engine history; response `history_scope=portal_events` makes that limit explicit.

Page cursors are opaque server-side tokens bound to scope/principal/current membership/operation/case and last sequence, with authority-supplied expiry. No browser offset/sequence becomes an access grant; refreshing a revoked authority refuses the cursor. No messages from another tenant/environment/case/recipient are returned. A new recipient after sender publication does not retroactively get a row or access.

## Authority and persistence

All operations use server-authenticated HumanPrincipal and deployment tenant/environment. Provider must independently resolve explicit action, audience, case, resource, consent and permitted fields against current source/identity/policy revisions, with validity and revocation. W6 list/detail grants are only five case fields and confer no communications/history/content rights. General metadata service has no encryption key and cannot fetch body bytes; PHI service holds keys and a separately scoped DB role. Durable authorization statements/digests provide provenance, never a self-issued grant. Missing provider refuses; no inferred subject relation or fake consent.

Body, message, intended-recipient, inbox, command idempotency, structural event and cursor records are separate durable tables. Message publication inserts all delivery/history records transactionally. Every command conflict/unknown-commit retry uses the original immutable command/digest and never silently republishes. Body encryption AAD binds deployment scope, case, sender identity reference, command/body reference, plaintext digest and key ID; tampering or wrong-case reuse refuses. Actual PostgreSQL constraints, transaction/lease/expiry behavior and deployment require ROOT-scheduled live acceptance; offline tests are not that proof.

Receipt index is populated only by a server method that obtains an already authenticated current receipt from the existing HumanGateway/read_receipt or another qualified effect-specific receipt reader, then verifies exact tenant, case association, task/command and principal/resource authorization. An arbitrary receipt_ref in a browser payload or a stored receipt-shaped DTO is insufficient. Index retains only opaque command/receipt refs and digest/type, never fabricates receipt content or promotes pending admission into execution. Scope starts with existing staff task receipts; external receipt disclosure needs an explicit extra grant. If a qualified existing case/receipt association source is absent, index population remains unavailable while communication history still works.

## Worker and frontend composition

RequestDocumentsWorker and SendDenialNoticeWorker are future producers through a separately authenticated system-publication adapter, with exact occurrence identity and human evidence provenance; this package cannot label a notice delivered without durable inbox rows. Source events/Kafka are not inbox delivery. No worker or engine mutation is authorized by this contract alone. AUTH/ESC free-text outputs stay in PHI and do not appear automatically in this history.

E05 maps `communication_available` to a fixed safe label and preserves `inbox_available` as its own state. Subject/body preview are absent. `body_ref` may open only the independently authorized PHI content operation. Raw text is never accepted as instructions or read in General history. API OpenAPI is the only transport schema source; UI view models are not duplicate wire contracts.

## Commit admission construction (E04 C2, independently approved colocation amendment)

The construction mechanism follows the independently reviewed colocation successor
`e04-communications-mechanism-colocation-repair/PROPOSAL.md` (SHA-256
`d3a4dcf21c37b43ad8b8aa4a21cfb2a099477a4d54cf728d357b4df1e4c62384`), with
construction approval `native-review/e04-communications-colocation-delta/REVIEW.md`
(`69f95485ed8caa2c41fb79b8fedbe152c98f360dae3ec7359e659f872faf3790`). This is
mechanical construction authorization; source authority, database installation,
identity writer coverage and deployment qualification remain independent gates.

`communications/admission.py` composes the concrete `PostgresCommunicationAuthority`,
`PostgresCommunicationAdmission` and `PostgresCommunicationPublisher`. The admission
uses the actual `PostgresIdentityStore` engine. Each service binds its resolver to that
same store/engine; another identity or mutation engine is unavailable. A mutation
without admission is unavailable. The metadata and PHI services can use separate
qualified roles, but each role's identity and mutation operations must use its one
colocated database connection and transaction. General remains unable to read content
ciphertext or keys.

For `preserve`, `publish` and `index_receipt`, the storage adapter opens admission's
single existing bounded PostgreSQL transaction. It locks the exact authority head
`FOR SHARE`, then the original session and membership rows through
`portal_communication.lock_session`, then the existing command/case locks. It validates
the current closed publication, original access/principal, authority digest, recipient
revisions/policies, fields, session binding and all initial/current deadlines. Staff,
beneficiary and provider use the same identity invariants; identity rows convey no
communications permission. Inserts and commit occur in that same connection. A
separate-store outer lease is unsupported. Unknown commit remains uncertain; the
original command identity is retained for ordinary immutable recovery. Post-commit
response authorization still applies and is not treated as protection of earlier writes.

`authority_schema.sql` is an explicit DBA-installed addition, not runtime migration.
Its function locks the original `public.portal_sessions` and `public.portal_memberships`,
with an owner-installed login-to-tenant mapping; it never reads a copied identity view.
No function/table role, source grant or tenant mapping is enabled by default. Independent
installation must qualify exact original-table resolution, dedicated function ownership,
consumer/publisher privilege separation, all invalidation writers and the original PHI
role/key restrictions. A connection constructor does not establish that qualification.

Authority publication is per exact access, avoiding an invented universal grant census.
The source-owned freeze must cover every dependency/invalidation writer of that decision,
including relationship, consent, recipient membership, resource/field policy and key or
source-qualification revocations. `publish(access)` asks that mandatory source for a
frozen, verified decision; it accepts no arbitrary grant argument. The concrete publisher
locks the same authority head `FOR UPDATE`, compares its expected revision, persists the
canonical payload and an immutable publication receipt, and acknowledges the source only
after known commit. Revocation publishes a closed decision with no grant. Missing source
or absent/expired/revoked publication refuses access. After ambiguous commit, the publisher
retains the exact pending publication and source freeze; `reconcile()` reuses that identity
and existing receipt instead of creating another revision. Source freeze survival through
process loss and complete affected-access invalidation are mandatory upstream properties;
no implementation of that qualified provider is supplied by this package.

The offline controls execute the actual SQL adapters and existing transaction helper
against a synthetic row-lock/commit model. They are construction evidence, not a claim
of PostgreSQL concurrency, role, source coverage, PHI custody or runtime qualification.
