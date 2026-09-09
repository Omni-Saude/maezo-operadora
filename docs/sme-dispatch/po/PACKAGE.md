# SME package — PO (product owner)

**Task:** T0.6 dispatch packaging. **Status:** prepared — awaiting SME roster (`blocked(external)`,
see `../README.md` and `../tracker.md`). No reviewer has been named or contacted.

## How to use this package

1. Read the contract at the canonical path listed for each item (`docs/processes/contracts/` —
   single source of truth; BPMN/DMN under `spec/processes/` are the modeled artifacts).
2. Answer the review questions below in writing against the contract text.
3. Return redlines as a PR against the contract file (preferred) or an annotated copy — see
   `../README.md` §"The redline protocol".
4. Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
   `../README.md` §"Signoff artifact spec". No agent creates this file for you.
5. **Turnaround ask:** 10 business days per contract from receipt (non-binding, see
   `../README.md`).

## Why PO is on all 16 contracts

Every contract in this batch carries at least one organizational-taxonomy question (candidate
group names proposed by the modeling wave, marked PROPOSTO/DRAFT, needing confirmation against
your actual IdP/Tasklist console groups) or a process-design/priority open question (a
taxonomy, a business-rule ambiguity, a scope decision) that is a product call, not a
clinical/legal/privacy/financial one — the "process/priority → PO" rule from the task charter.
This package is intentionally broad but each entry below states the *specific* decision needed,
not a generic "please review."

## Assigned contracts

### SP-OP-ADEQUACAO-001 — Adequação Geográfica de Rede (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-ADEQUACAO-001.md`
- **Specific ask:** confirm candidate groups `gestao-rede` / `coordenacao-rede` against your org
  chart; confirm the taxonomy of `especialidade` (TUSS/CBO) and the geographic granularity
  (região de saúde vs município IBGE) used to key adequacy evaluation cells.

### SP-OP-ANS-CRON-001 — Agendador per-report_type (DRAFT, v0.2.0)

- **Contract:** `docs/processes/contracts/SP-OP-ANS-CRON-001.md`
- **Specific ask:** this is a pure scheduler with one process-definition per `report_type` — is
  the enumerated set of `report_type` values complete for your regulatory reporting obligations,
  or are there filing types not yet modeled?

### SP-OP-ANS-SUBMIT-001 — Envios Periódicos ANS (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md`
- **Specific ask:** confirm candidate groups `regulatorio-ans` / `juridico-regulatorio` /
  `coordenacao-regulatorio`; confirm the retry/backoff attempt count and windows
  (`PT5M`/`PT30M`/`PT2H`, then escalate) match your operational tolerance for a stuck filing.

### SP-OP-AUTH-001 — Autorização Prévia (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-AUTH-001.md`
- **Specific ask:** confirm candidate groups `medico-auditor` / `coordenacao-auditoria-medica` /
  `junta-medica`; confirm the `categoria_procedimento` enum
  (`consulta`/`exame_simples`/`exame_especial`/`terapia`/`internacao`/`opme`/
  `alta_complexidade`) is complete for your product catalog.

### SP-OP-CANCEL-001 — Cancelamento / Rescisão Contratual (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-CANCEL-001.md`
- **Specific ask:** confirm candidate groups `gestao-contratos` / `juridico-contratos` /
  `coordenacao-contratos`; decide the re-adesão/reactivation policy after a beneficiary-requested
  cancellation (explicitly unresolved in the contract's Pendências).

### SP-OP-CONTAS-001 — Processamento de Contas / Glosa (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-CONTAS-001.md`
- **Specific ask:** confirm candidate group naming — the contract notes your own plano's §3.1
  uses `analista-contas-medicas`/`coordenacao-faturamento` while this contract proposes
  `auditoria-contas`/`coordenacao-contas`; pick one taxonomy and apply it consistently.

### SP-OP-CRED-001 — (Des)credenciamento de Prestador (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-CRED-001.md`
- **Specific ask:** confirm candidate groups `gestao-rede` / `juridico-rede` /
  `coordenacao-rede`; decide whether "aprovação colegiada" style multi-reviewer gates are needed
  for high-impact decredentialing (e.g. hospital with many linked beneficiaries), or single
  reviewer is acceptable.

### SP-OP-ESCALATION-001 — Escalonamento Humano Universal (**FINAL, v1.0.0**)

- **Contract:** `docs/processes/contracts/SP-OP-ESCALATION-001.md`
- **Specific ask:** this contract is already FINAL and its candidate groups
  (`plantaoClinico`/`enfermagemTriagem`/`atendimentoHumano`/`supervisaoAtendimento`) are
  shipped — PO's role here is narrow: confirm these groups are still current (org changes since
  FINAL?) as part of the retro-verification pass (see `../tracker.md` signoff-absent flag and
  `../medico-auditor/PACKAGE.md` / `../regulatorio/PACKAGE.md` for the SLA-content half of the
  same retro-verification). **Turnaround ask: 3 business days** (narrow scope).

### SP-OP-FRAUDE-001 — Investigação de Fraude (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-FRAUDE-001.md`
- **Specific ask:** confirm candidate groups `investigacao-fraude` / `coordenacao-investigacao` /
  `juridico-fraude`; decide `numero_caso` correlation granularity (one case per
  provider-competência? per event? per open entity?) — flagged as needing produto/jurídico
  input; confirm the Beatriz↔fraud-domain persona mapping (flagged in the contract as
  "R-PERSONA-MAP DRAFT — a chamada mais frágil") before that agent is authored.

### SP-OP-INADIMPLENCIA-001 — Suspensão / Rescisão por Inadimplência (DRAFT, v0.2.0)

- **Contract:** `docs/processes/contracts/SP-OP-INADIMPLENCIA-001.md`
- **Specific ask:** confirm candidate groups `juridico-contratos` / `gestao-cobranca` /
  `coordenacao-cobranca` (coordinate with CANCEL-001's groups to avoid divergent taxonomy for the
  two processes that both touch `contract_termination`); decide whether Fernando (the
  inadimplência agent) needs a dedicated process-aware role or should operate purely as a
  navigator that escalates to CANCEL-001 (explicitly framed as a product decision in the
  contract, separate from the already-resolved operational question).

### SP-OP-LGPD-DSR-001 — Direitos do Titular (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-LGPD-DSR-001.md`
- **Specific ask:** decide whether consent revocation with immediate effect (LGPD art. 18 §2)
  warrants a dedicated fast-path UX/process distinct from the general DSR flow — the contract
  flags this as an open evaluation, not yet decided.

### SP-OP-NIP-001 — Resposta a NIP (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-NIP-001.md`
- **Specific ask:** confirm candidate groups `nucleo-ans` / `regulatorio-ans` /
  `juridico-regulatorio` / `medico-auditor` / `coordenacao-regulatorio`; confirm the stable
  business-key identifier choice (`numero_nip_ans` vs `protocolo_ans`) from an operational
  (not just regulatory) standpoint — which one is actually available reliably at intake?

### SP-OP-PAGTO-001 — Pagamentos de Alçada (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-PAGTO-001.md`
- **Specific ask:** confirm candidate groups `aprovacao-financeira-l1/l2/l3` /
  `comite-financeiro` / `coordenacao-financeira` against your IdP; jointly with finanças, decide
  if the comitê tier needs a quorum/multi-instance User Task model instead of a single approver.

### SP-OP-PROGRAMA-001 — Programas de Cuidado (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-PROGRAMA-001.md`
- **Specific ask:** define the taxonomy of `programa_id` (crônicos / pré-natal / oncologia / APS —
  explicitly an open question in the contract) and the eligibility criteria per program; confirm
  candidate groups `coordenacao-clinica` / `equipe-cuidado`; decide whether enrollment requires a
  dedicated consent-capture User Task **before** the coordinator's decision (explicit open
  question in the contract).

### SP-OP-RECURSO-001 — Recurso de Glosa (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-RECURSO-001.md`
- **Specific ask:** confirm candidate groups `analista-recurso-glosa` / `coordenacao-recurso`
  (both marked PROPOSTO); confirm the appeal-letter/petition text generation workflow (Marina
  drafts, human authors/approves) matches your actual legal-drafting process.

### SP-OP-REEMBOLSO-001 — Reembolso ao Beneficiário (DRAFT, v0.1.0)

- **Contract:** `docs/processes/contracts/SP-OP-REEMBOLSO-001.md`
- **Specific ask:** confirm candidate groups `analise-reembolso` / `medico-auditor` /
  `coordenacao-reembolso`; confirm required attachment/proof types per `tipo_reembolso`
  (`livre_escolha`/`fora_rede`/`urgencia_emergencia`/`indisponibilidade_rede`) match your actual
  claims-intake requirements.

## Turnaround

10 business days per contract (standard, proposed, non-binding), except SP-OP-ESCALATION-001
(3 business days, narrow retro-verification scope) — see `../README.md` §"Expected turnaround".

## Signoff

Record your verdict in `docs/processes/contracts/signoffs/<CONTRACT-ID>.signoff.yaml` per
`../README.md` §"Signoff artifact spec". This package does not create, pre-fill, or infer any
signoff file.

## Taxonomia organizacional — sessão única (ROW R-034)

O `candidateGroups` "confirm" ask repetido em quase todo item acima (`gestao-rede`,
`auditoria-contas`, `medico-auditor`, `plantaoClinico`/`enfermagemTriagem` de
SP-OP-ESCALATION-001 etc.) é a mesma pergunta de organograma feita processo a processo. A resposta
aprovada de R-034 (`OWNER-DECISIONS-REGISTER`, APROVADO-APOS-REVISAO-HUMANA) consolida essa
pergunta numa única sessão de taxonomia cobrindo os 8 processos do escopo do dono (ESCALATION,
CANCEL, ADEQUACAO, CRED, NIP, PROGRAMA, AUTH, ANS-SUBMIT): a tabela preparatória
`ORG-TAXONOMY-TABLE.md` (e sua contraparte `org-taxonomy-table.yaml`) neste mesmo diretório lista
todo `grupo declarado → arquivo:linha → processo → User Task/regra DMN → SLA/ato → PROPOSTO?`,
com o campo `nome_real` vazio para o dono preencher numa sentada. Isto não substitui os asks
individuais acima — é o insumo consolidado para respondê-los todos de uma vez.
