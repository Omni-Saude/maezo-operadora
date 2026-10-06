# Optional feedback and canonical financial independence — proposed contract v2 third repair

Status: **PROPOSED_NOT_ADOPTED**. Private documentary/conditional abstract proof only. Original v1/12-case/5-negative evidence is preserved. FIN-U4-F01/F02 repair is pending the original financial reviewer's exact-byte delta; this author does not approve its own work. Nothing here edits MAEZO source, delivered CA feedback, BPMN/DMN, a clinical policy, a native payment, timer, external wire, source authority or human ratification.

## Canonical boundary retained

CONTAS adjudicates from the payer perspective. Its decisions/glosa and mandatory provider notices stay canonical; the fraud communication exception/OQ-14 remains open. Terminal order remains decision/register → mandatory demonstrativo → applicable financial handoff → domain event → end. The positive/source-specific amount, source due date and provenance are preconditions of the existing handoff. Source/customer feedback cannot become one of its financial facts.

CONTAS and PAGTO contracts remain **DRAFT**. ADR-0040 remains **Proposed**; Accepted ADR-0018 and ADR-0028 remain mandatory. A source/contract fixture, care result, callback, constructor, local witness assertion or technical receipt does not ratify those authorities. Finance/source/human admission must be authentically qualified by their existing owners before any operational effect.

The private projection only preserves/forwards the ORIGINAL handoff packet conditional on an externally supplied canonical handoff admission. It does not release money or implement admissibility, threshold selection, a human approval or a source receipt. PAGTO independently executes native validation/admissibility → native DMN tier routing → required human approval and tier-match → protected release path → real financial source/outcome reconciliation. `UT_AnaliseAdmissibilidade` remains required when native admissibility routes there; it is not skipped by source-provided evidence. High-tier release remains guarded by `decisao_pagamento`, `aprovador_id`, justification and the existing tier match. No witness-generated object supplies these facts or proves a human completed a task.

Actual node anchors: `ST_ValidatePaymentData`, `ST_CalculateFacts`, `BRT_PagtoAdmissibility`, `GW_Admissibilidade`, `UT_AnaliseAdmissibilidade`, `BRT_AlcadaRouting`, `UT_AprovacaoAlcada`/`UT_CoordenacaoAlcada`, `ST_ReleaseLowValue`/`ST_ReleaseHighValue`. Their original code/model bytes and authority ordering are unchanged. Static order references in the samples are references to gates, not completed-gate evidence or a manufactured HumanProof.

## Exact CONTAS packet crosswalk

The fields/expressions below were extracted from the actual `handoff_pagamento` payload AST in `src/maezo/tools/workers/contas.py`, SHA256 `50143508740902700d495bc6cc0dec72559fabaa03494d3e094766ad09adf646`. `canonical-financial-crosswalk.json` retains the exact extraction. Sources include `SP-OP-CONTAS-001` variables/handoff and `SP-OP-PAGTO-001` inputs/business key; their hashes are in the input manifest.

| Existing field | Existing canonical producer expression | Preservation / authority limit |
|---|---|---|
| `tenant_id` | `tenant_id` | Original normalized tenant; no clinical/case/feedback tenant substitution. |
| `ordem_pagamento_id` | `_ordem_pagamento_id(tenant_id, numero_lote_tiss, prestador_id)` | Existing CONTAS deterministic order producer only; projection never mints another order. |
| `numero_lote_tiss` | `numero_lote_tiss` | Original normalized source lot identity. |
| `numero_guia_tiss` | `str(variables.get('numero_guia_tiss', ''))` | Original source traceability echo; does not authorize a payment. |
| `prestador_id` | `prestador_id` | Original normalized creditor/provider identity; no respondent or patient substitution. |
| `tipo_pagamento` | `TIPO_PAGAMENTO_PRESTADOR_REDE` | Existing fixed source value prestador_rede for this CONTAS slice. |
| `valor_pagamento_cents` | `_to_cents(valor)` | Source-selected presented or released amount through existing pure cent conversion; no survey-dependent amount. |
| `moeda` | `'BRL'` | Existing explicit BRL constant, no currency conversion. |
| `competencia` | `str(variables.get('competencia', ''))` | Source competence echoed; not changed by instrument/cohort. |
| `data_vencimento` | `data_vencimento` | Contractual source date stripped only; never defaulted from survey timeout. |
| `conta_origem_ref` | `str(variables.get('conta_origem_ref', ''))` | Source protected payer-account ref echoed, including source absence; not bank data or a guessed account. |
| `instrumento_pagamento` | `str(variables.get('instrumento_pagamento', ''))` | Source payment instrument echo; no witness selection. |
| `fonte_valor` | `fonte` | Calling task declares apresentado/liberado; never inferred from feedback. |
| `lastro_origem` | `lastro_origem` | Existing closed source provenance; evidence, never lastro_confirmado. |
| `lastro_decisor_id` | `lastro_decisor_id` | Source actor echo; EXACT empty string on automatic branch, never a fabricated human approval. |

**Exactly 15 seeded fields.** The projection adds no wire fields. `lastro_confirmado`, `dados_pagamento_validos`, `duplicidade_suspeita`, `dentro_teto_l2` are NEVER seeded; PAGTO resolves those admissibility facts itself or via its native human task. No care/survey/response token is equivalent to lastro.

Caller provenance is explicit in unchanged CONTAS BPMN: `ST_HandoffPagamentoAuto` declares `fonte_valor=apresentado` / `lastro_origem=contas_adjudicacao_automatica`, with original `lastro_decisor_id=""`; `ST_HandoffPagamentoHumano` and `ST_HandoffPagamentoParcial` declare `liberado` / `contas_adjudicacao_humana`, preserving the authenticated source analyst identity and human decision boundary. Source code rejects unknown/contradictory provenance, invalid/blank source money, blank due date and missing identity anchors. The documentary examples are post-canonical-guard shape samples: illustrative actor strings are NOT authenticated decisions or receipts. They never run the worker/native effect.

Original identity producers remain `_ordem_pagamento_id(tenant_id, numero_lote_tiss, prestador_id)` and `_pagto_business_key(tenant_id, numero_lote_tiss, prestador_id)` through the existing shared `key_segment`. A CONTAS re-delivery preserves the same `ORDEM-CONTA-*` and `PAGTO-*` identity; feedback ID, instrument revision, respondent, timeout or changed source revision cannot create a new key. The original `SP-OP-PAGTO-001` permanent strict-start posture and emit-before-effect audit stay at `start_process_idempotent`; this proposal does not replace their durable storage or history checks.

The separate RECURSO origin stays out of this CONTAS sample implementation: its existing glosa-based key and `recurso_deferimento_humano` source authority require their own exact qualified mapping. A survey/care fact never chooses that origin or opens RECURSO. No new payment origin is introduced.

## Immutable packet/source/authority projection

The abstract `Packet` carries immutable canonical BYTES of every original field, plus the supplied original process/business identity, source authority/object/opaque revision and unchanged native gate-reference order. These last items are protected internal proof context, not extensions to the 15-field payment wire. The source/custody owner must publish/authenticate their real interpretation before an adapter binds them; illustrative strings are not authority. No field or identifier is minted by the projection witness: the separate samples use the source's unchanged payload expression/pure identity/cent helpers only on explicit illustrative inputs. No worker, engine, bank API, money release, audit signature or provider receipt is invoked.

Full input and output packet, tenant, order, creditor, amount, currency, competence, due date, account/instrument, amount source, lastro source/actor, source object, opaque source revision and original key remain exactly equal to the original pinned snapshot. A syntactically valid field substitution, changed native-order reference or revision does not renew this invocation. Legitimate corrections require the existing qualified native source/financial contract and original-key reconciliation; they are not guessed by the witness. Unknown financial result cannot reset a fence or resubmit an effect.

Financial native events are supplied by an external control parameter in the conditional model. `native_control_parameters` are explicitly illustrative scenario inputs, NOT authenticated approvals, source policies, human proof or production vocabulary. In real integration, only an authentic already-qualified original native financial controller can admit the dependent action; object construction, values such as “admitted”, or an optional-feedback outcome do not qualify that controller. Current real controller/publication/receipt evidence remains absent from this package.

An immutable product separates `Finance` from `Feedback`. `feedback_step` receives only the feedback partition; it cannot inspect/change a finance packet, key, amount, authority or queue. For every feedback-only step, finance state is exactly unchanged, which gives an inductive argument for arbitrarily long/forever pending suffixes. Native finance evolves only under original native inputs, using the same function as the finance-only oracle. The projection preserves the native trace and full packet; a feedback label cannot approve, cancel, reorder a human gate or mint a financial prerequisite.

The idempotency witness records the original existing source/process/business identity independently of source revision and optional-feedback identity. The first externally admitted original handoff creates at most one abstract forwarding output; exact replay preserves it. Changed body/source/revision/identity is refused for that original invocation. Unknown/cancelled/refused/pending native outcomes never receive a new dispatch by feedback or retry. Already forwarded historical data stays retained under native unknown/cancellation; only an external original receipt lookup may proceed. The abstract ledger proves only this projection's property, not the actual provider's exactly-once behavior or a new production idempotency implementation.

## Source receipt and delivery limits

Existing CONTAS return fields such as `handoff_executado`, `pagto_business_key`, `pagto_instance_id` and `pagto_already_existed` identify the attempted/idempotent process handoff. They are NOT a bank settlement receipt. Existing PAGTO worker return flags (`pagamento_executado`/`pagamento_liberado`) are not proof of provider settlement. A future source-qualified financial result must bind exact original tenant/order/creditor/amount/due/provenance/source revision/key and independently qualify native/human admission and actual financial receipt. This package publishes no receipt schema and manufactures none. Uncertain origin/engine/financial outcomes are receipt-first reconciled by existing qualified native interfaces, never hidden or re-enqueued by survey replay.

## Scope of optional feedback and CR-F6

OP11 instruments/voluntary response and CR-F5 case/requester/manifestation gates remain distinct from financial approval. CR-F6 qualified invitation/channel/current recipient/purpose/preferences/revocation and prepared/sent/delivered/unknown semantics apply to the OPTIONAL invitation only. Missing optional invitation, instrument, respondent or purpose authority disables that dependent read/send/feedback action; it cannot block an independently admitted canonical financial path. This does not waive the existing mandatory CONTAS/provider notice or native financial audit obligations.

Wrong tenant/case/respondent, stale/revoked feedback purpose, consumer disabled, failure, disagreement, replay and silence are feedback outcomes only. They cannot resolve a case, certify performance, change the financial source or select a payer/creditor. No clinical policy, cohort or business timer is adopted; an abstract timeout label has no duration and is not the delivered CA 72h rule.

## Bounded proof and remaining gates

Private `u4-packet-noninterference-proof.py` consumes the external illustrative packets/control parameters and checks all interleavings retaining each producer's native order: finance-first and feedback-first; forever absent, indefinite pending, response/disagreement, failed+duplicate, abstract timeout, disabled consumer, wrong tenant/case/respondent, stale/revoked purpose, source refusal/pending, cancellation before/after and uncertain finance outcome/replay. The report records 3 source-shape samples, 576 scenario families, 4,308 ordered traces and 9,930 feedback-only induction checks. It independently compares whole immutable packet/source/key/revision/native-order/queue to the finance-only oracle, not an enabledness boolean alias.

51 negative controls must fail: all 15 packet fields at input and output, original process/business/source/object/revision identity, canonical gate-reference order, duplicate enqueue, survey-gating, replay-renewed key, reordered native trace, unknown-result reset/resubmit, malformed/extra/forbidden packets. `financial-packet-proof-results.json` is deterministic authored evidence, not independent approval or token/engine acceptance.

The delivered CA U4 bytes remain unchanged: abstract liquidation follows a two-token AND join fed by clinical outcome and survey-or-timeout. This can have a bounded sound-token fragment while finance remains survey-dependent. This repair proposes the product projection; it does not install the adapted CA model as executable finance, generate BPMN, prove full engine token soundness or qualify payment effects. XSD/DI/Modeler/full engine/provider gates remain separate because no executable workflow is produced here.

Pending role acts (names/signatures absent): financial CONTAS/PAGTO owner verifies exact canonical origin/field/provenance/object/key/notice mapping, admissibility/DMN/tier and mandatory native human review; financial source owner publishes authenticated original receipts/revision/currentness and uncertainty reconciliation; CX/instrument owner publishes voluntary instrument/version/respondent and purpose; CR-F6 channel/recipient owner qualifies only discretionary invitation; DPO/security review minimization/custody/revocation/erasure; independent architecture/compliance and CIB/source verifier test unchanged native financial handoff/human-tier/receipt/replay under real interfaces after admission. These requirements are now aligned in the repaired W4-U4/U4 matrix entries. No absent optional authority creates a global kernel blockage.

Original financial source/human/runtime/production gates remain open. Contract/projection review and disabled source-independent mechanics remain executable; operational source/engine/financial acceptance is not inferred.
