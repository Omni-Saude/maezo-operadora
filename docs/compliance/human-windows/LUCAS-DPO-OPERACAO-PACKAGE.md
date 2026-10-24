# PACOTE DPO / OPERAÇÃO-FINANCEIRO (LUCAS) — D-25 contras-assinaturas do erasure, D-26 pontes, habilitação do Lucas e R1–R5

**Missão:** W5 CLNT 5.6 — pacote de evidência para as janelas humanas do DPO e da operação/financeiro que habilitam o Lucas (`docs/plans/maezo-completion-program.md` §W5 5.6 e §HDR).
**Autor:** engenheiro de pacotes (agente). **Data:** 2026-10-24. **Base:** `main` = `dc544a0e` (#965), checkout limpo de `origin/main`.
**Natureza:** INFORMATIVO. Este pacote **não decide, não ratifica e não contras-assina nada** (ADR-0007; cerca §0(4) do plano). Nenhum campo de decisão/revisor/data está preenchido aqui.
**Consumidor:** DPO (+ jurídico-privacidade) para D-25/D-26; financeiro/PO e operação para as habilitações do Lucas e R1–R5 (`docs/plans/maezo-completion-program.md:139`: "DPO: D-25 16 contras-assinaturas do erasure + insumos GP11/M2 + VW4 · D-26 pontes de identidade. … Jurídico/operação/financeiro: R1–R5 (bloqueiam OP17); revisões de habilitação do lucas").
**Regra de leitura:** toda afirmação de fato traz `arquivo:linha`; contagens somadas no fim (§8). Pacote irmão (jurídico D-03/D-04/REG-1.13) já existe: `docs/compliance/legal/` (PR #935).

---

## 0. Resumo executivo

| # | Pendência | Estado hoje | Prazo |
|---|---|---|---|
| 1 | **D-25a** — contras-assinaturas do plano de eliminação por camada (`spec/policies/retention/erasure-plan.template.yaml`) | `status: DRAFT`, `ratificado: false`, `dpo_review: PENDENTE`; **41 relações**, cada uma com `decisao_dpo`/`base_legal`/`retencao` = `PENDENTE` (`erasure-plan.template.yaml:96-104`; contagem §8) | sem data própria; pré-condição da Chave A do erasure (§1.2) |
| 2 | **D-25b** — insumos GP11/M2 + VW4 (contras-assinatura DPO) | insumos GP11 aceitos pelo dono; promoção do SP-OP-SUPP-001 a FINAL exige revisão DPO; 7 blocos §7 do VW4-ANSWERS aguardam assinatura (GP11 = DPO) | sem data própria |
| 3 | **D-26** — pontes de identidade do erasure | substrato construído DARK pelo #965 (`src/maezo/platform/identity_bridges.py`; 2 flags OFF por padrão; zero chamador); a decisão que habilita a travessia é o plano D-25a ratificado | sem data própria; flip = onda ROLL |
| 4 | **Habilitação do Lucas** | roteador `MAEZO_ROTEADOR_LUCAS` default OFF; 2 DMNs do Lucas DRAFT; fonte de cobrança sem validação de banco; DPA do provedor pendente (`scripts/ci/check_roteador_lucas.py`) | pré-condições da cerca de CI (§4) |
| 5 | **R1–R5** — cadeia de ratificação que bloqueia OP17 (`commission.settle`) | todos `RATIFY-LATER`; OP17 `candidate_not_live` — nada implementado | sem data própria |

Divergência de contagem registrada (plano ↔ código): o §HDR diz "16 contras-assinaturas"; a enumeração viva hoje tem **41 relações**. O próprio registro da fila já havia flagrado a deriva 15→16 em 2026-09-04 (`docs/review-queue.md:27`, nota DU-01-b), e o docstring do módulo diz "the original 16 plus nine ADR-0049 relations" (`src/maezo/platform/lifecycle/erasure_plan.py:9-10`) e "16+ decisões por relação" (`src/maezo/platform/identity_bridges.py:38`). As seções abaixo trabalham com o número real de hoje (41); a correção do rótulo no plano é emenda do dono, não deste pacote.

---

## 1. D-25a — as contras-assinaturas do plano de eliminação por camada

### 1.1 O artefato e o portão

- **Artefato:** `spec/policies/retention/erasure-plan.template.yaml` — enumera, camada por camada, ONDE dados de um titular podem residir na persistência, e reserva um espaço VAZIO para a decisão do DPO sobre cada uma. "A ESTRUTURA é um FATO derivado do schema e está preenchida. A DECISão (`decisao_dpo`), a BASE LEGAL (`base_legal`) e o PRAZO (`retencao`) são do DPO + jurídico e estão TODOS como placeholders explícitos" (`erasure-plan.template.yaml:5-10`).
- "NENHUM AGENTE, ORQUESTRADOR OU GATEKEEPER AUTOMATIZADO PODE PREENCHER ESSES CAMPOS. Fabricar uma decisão de DPO neste repositório é um evento de compliance, não um conflito de merge" (`:12-14`).
- **Pacote de revisão (o que o DPO lê):** `docs/reviews/adr-0029-erasure-packet.md` — mecânica por relação, o SQL documentado, e a tensão de `audit_chain` (anonimizar quebra a cadeia exatamente como eliminar, porque `decision_basis` está no preimage de `record_hash` — decisão D-1 do pacote; §5 do packet). Nota: o packet §3 tabela diz "15 relações" — número da época; a enumeração viva é a do carregador (§1.3).
- **Estado:** `status: DRAFT`, `ratificado: false`, `dpo_review: PENDENTE`, campos humanos placeholder explícitos e detectáveis (`dpo_reviewer: PENDENTE-IDENTIDADE-DO-DPO`, `dpo_review_date: PENDENTE-AAAA-MM-DD`, `evidence_ref`, `notes` — `:96-104`); `review_packet` aponta para o pacote acima (`:105`).
- **Revisor na fila:** "DPO + juridico-privacidade" (`docs/review-queue.md:27`).
- **CODEOWNERS:** `spec/policies/retention/` e o template têm linha própria com `@rodaquino-OMNI @Omni-Saude/security-team @lucasreisEvah` (`.github/CODEOWNERS:138`, `:142`).

### 1.2 As duas chaves (o que a ratificação liga e o que ela NÃO liga)

`erasure-plan.template.yaml:38-52`:

- **Chave A — este arquivo ratificado** (`ratificado: true` + `dpo_review: APPROVED` + toda `decisao_dpo`/`base_legal`/`retencao` preenchida). Vira `load_erasure_plan()` de recusa para sucesso, SEM mudança de código e sem redeploy.
- **Chave B — o mecanismo de execução existir.** Não existe: `ErasureManager.erase()`/`verify()` levantam `ErasureNotImplementedError` SEMPRE (`src/maezo/platform/erasure.py:152`/`:187`); `assert_dry_run_only()` recusa EXECUTE em ambos os estados — antes da ratificação com razão `plan_unratified`, depois com `execution_mechanism_absent` (`erasure_plan.py:22-29`). "Ratificar este arquivo NÃO liga nada" (`:48-52`) — ratificação é ato de governança e nunca pode ser, sozinha, gatilho de efeito destrutivo. Pinado por `tests/unit/platform/test_erasure_plan.py::test_execute_mode_is_still_refused_once_the_plan_is_ratified`.

### 1.3 As 41 relações e o que cada decisão exige (contagem: 41 = 41)

A enumeração canônica vive no carregador (`PERSISTENCE_LAYERS`, `src/maezo/platform/lifecycle/erasure_plan.py`) e o template precisa cobrir EXATAMENTE o mesmo conjunto — deriva falha o teste nos dois sentidos (`erasure_plan.py:13-15`; anti-drift em `tests/unit/platform/test_erasure_plan.py`). O carregador exige as três chaves de decisão em TODA entrada e recusa qualquer valor que ainda pareça placeholder (`PENDENTE/PENDING/PLACEHOLDER/TODO/TBD/XXX`) ou que o conjunto de `tabela` divirja do que o código enumera (`erasure-plan.template.yaml:70-78`; recusa de cobertura em `erasure_plan.py:1102`).

Para CADA uma das 41 relações o DPO + jurídico decide: `decisao_dpo` ∈ {ELIMINAR, ANONIMIZAR, RETER_COM_BASE_LEGAL} + `base_legal` + `retencao` (vocabulário em `erasure-plan.template.yaml:63-68`).

| # | Camada | Relação | Criada em (citação da enumeração) |
|---|---|---|---|
| 1 | trabalho | `checkpoints` | nenhuma migração — `PostgresSaver.setup()/.asetup()` (0006:14-23,25-35) |
| 2 | trabalho | `checkpoint_blobs` | idem (0006:19-21) — BYTEA portador de PHI |
| 3 | trabalho | `checkpoint_writes` | idem (0006:22-23) |
| 4 | trabalho | `checkpoint_migrations` | idem (0006:17) |
| 5 | trabalho | `agent_checkpoints` | 0001:33-43 criada; 0006:69 removida (RETIRADA) |
| 6 | trabalho | `agent_checkpoint_writes` | 0001:48-58 criada; 0006:68 removida (RETIRADA) |
| 7 | episodica | `agent_memory` | 0001:63-75; índice parcial por titular 0001:82-86 |
| 8 | semantica | `agent_memory.embedding` | 0001:72 (`vector(1536)`); REMOVIDA por `0009_drop_pgvector` (linha permanece enumerada, sonda desligada — DU-01-b, `docs/review-queue.md:27`) |
| 9 | auditoria | `audit_chain` | 0002:27-51; UNIQUE(prev_record_hash) 0002:44-50 — SEM coluna de titular; tensão da decisão D-1 do packet |
| 10 | auditoria | `audit_emit_dedup` | 0005:59-67 |
| 11 | custodia | `custody_bundles` | 0004:31-45; índices 0004:47-58 |
| 12 | registro_de_eliminacao | `erasure_log` | 0004:64-79; índice (tenant, fhir_patient_id) 0004:82-84 |
| 13 | idempotencia | `a2a_idempotency` | 0003:32-45 |
| 14 | idempotencia | `driver_idempotency` | 0003:61-67; repurpose + coluna status 0010 |
| 15 | inbox_amh | `amh_inbox` | 0007:127-229 |
| 16 | outbox_a2a | `a2a_fact_outbox` | 0008:134-183; índices 0008:191-201 |
| 17 | identidade_portal | `portal_login_transactions` | 0012_portal_identity_session |
| 18 | identidade_portal | `portal_code_claims` | 0012_portal_identity_session |
| 19 | identidade_portal | `portal_sessions` | 0012_portal_identity_session |
| 20 | identidade_portal | `portal_memberships` | 0012_portal_identity_session |
| 21 | comando_humano | `human_command_delivery` | 0013_human_command_outbox (FK; guards) |
| 22 | comando_humano | `human_command_outbox` | 0013_human_command_outbox (trigger imutável) |
| 23 | autoridade_atribuicao | `portal_assignment_source` | 0014_staff_assignment_authority |
| 24 | autoridade_atribuicao | `portal_assignment_publications` | 0014_staff_assignment_authority (FK; trigger imutável) |
| 25 | autoridade_atribuicao | `portal_assignment_receipt_source` | 0014_staff_assignment_authority (FK) |
| 26 | inbox_escalonamento | `escalation_team_notice` | 0015_escalation_team_notice (triggers imutáveis) |
| 27 | custodia_contato_retomada | `beneficiario_contato_retomada` | 0016_recipient_custody (índice expires_at) |
| 28 | roteamento_conversa | `conversa_agente_ativo` | 0017_conversa_agente_ativo |
| 29 | durabilidade_capacidade | `v21_journey_journal` | 0018_capability_durability_journal |
| 30 | durabilidade_capacidade | `v21_capability_command` | 0018 (journal FK) |
| 31 | durabilidade_capacidade | `v21_journal_observation` | 0018 (command FK) |
| 32 | durabilidade_capacidade | `v21_journal_inbox` | 0018 (command FK) |
| 33 | durabilidade_capacidade | `v21_external_wait` | 0018 (journal/command FKs) |
| 34 | durabilidade_capacidade | `v21_journal_outbox` | 0018 (journal/command FKs) |
| 35 | identidade_portal | `portal_vendor_channels` | 0019_vendor_channel_authority |
| 36 | identidade_portal | `portal_vendor_submissions` | 0020_vendor_channel_submissions |
| 37 | identidade_portal | `portal_vendor_suppressions` | 0021_vendor_suppression_records |
| 38 | acesso_beneficiario | `conversa_acesso_beneficiario` | 0022_conversa_acesso_beneficiario |
| 39 | lembrete_cobranca | `lembrete_cobranca_enviado` | 0027_lembrete_cobranca |
| 40 | lembrete_cobranca | `lembrete_cobranca_optout` | 0027_lembrete_cobranca |
| 41 | acesso_beneficiario | `acesso_falhas_por_cpf` | 0028_acesso_falhas_por_cpf |

Tensões já documentadas que o DPO precisa pesar (do review-queue, linha 27 — resumo, não decisão): (1) só DUAS colunas de todo o encadeamento identificam titular por `fhir_patient_id` (`agent_memory` 0001:69 NULLABLE; `erasure_log` 0004:67) e as pontes eram lacuna de engenharia — **agora substrato #965** (§3); (2) `audit_chain`: ELIMINAR é hoje inexequível pelo mecanismo ratificado (decisão D-1 do packet); (3) reter linhas de idempotência/inbox PROTEGE a eliminação (re-entrega re-materializaria o dado apagado).

- **Impacto do silêncio:** o dry-run segue honesto e inerte (recusa `not_ratified`/`placeholder_value`), a Chave A não vira, e qualquer ponte/composição futura esbarra na falta de decisão — o custo é a fila de privacidade permanecer 100% pendente num programa cujo muro é 2027-02-09.

---

## 2. D-25b — insumos GP11/M2 + VW4 (contras-assinatura DPO)

1. **GP11 (não-contato/supressão) — wiring feito; revisão DPO pendente.** Os insumos DPO foram ACEITOS PELO DONO (k=100 sobre tupla C2/C6, taxonomia C1–C6, SLA 15 dias úteis com relógio por registro, KPI `phi_egress_violations`) e o wiring materializou store 0021, DMN `suppression_routing` com partições da taxonomia, boundaries de escalonamento 50/80/100 e honra na construção de lista (`docs/review-queue.md:2443-2464`). **O que falta do DPO:** "A promocao do SP-OP-SUPP-001 a FINAL segue exigindo REVISAO DPO (formalizacao administrativa do encarregado art. 41 §4d e o conteudo desta DMN seguem sob revisao; §4o do art. 41 e VETADO e nao integra)" (`docs/review-queue.md:2460-2461`). Enquanto não promovido: seam ausente = UNKNOWN (nunca zero) e rota fora do vocabulário fechado = incidente CONTRACT_MISMATCH (nunca honra por omissão) (`docs/review-queue.md:2463-2464`). A titularidade do grupo `dpo` = encarregado nomeado (LGPD art. 41, papel estatutário) e a ratificação de quem ocupa é VW0-D20 (`docs/review-queue.md:2448`).
2. **VW4 (GP5–GP11) — 7 blocos de assinatura humana.** Os rascunhos estão prontos e auditados (`docs/audits/VENDOR-XP-2026-10/VW4-RATIFICATION-ANSWERS-V1.md` v1.1, RE-GATE PASS 11/11; linhas `RATIFY-LATER-DRAFT-READY` no VW0-DECISION-REGISTER). "Resta humano: cada ratificador nomeado assina seu bloco §7 (GP5 Jurídico-Produtos+canal · GP6 Comercial+compliance · GP7 PRODUTO+jurídico · GP8 SME+jurídico · GP9 Conformidade+jurídico · GP10 Compliance · **GP11 DPO**)" — cada assinatura destrava seu pacote de engenharia (`docs/Tarefas_Pendentes.md:1160-1172`). O bloco do **GP11 é a contras-assinatura do DPO** citada pelo finding F-P02 do programa ("DPO counter-signature of GP11 inputs + M2 note pending" — anexo do plano, `docs/audits/JOURNEYS-COMPLETENESS-2026-10/MASTER-PLAN-ANNEX.md:38`, `FINDINGS.md:54`).

---

## 3. D-26 — as pontes de identidade do erasure (o que o #965 construiu dark e qual decisão habilita)

### 3.1 O que foi construído (dark; PR #965 = `dc544a0e`)

`src/maezo/platform/identity_bridges.py` — o substrato das DUAS travessias nomeadas pela Chave B do plano e pelo carregador ("MISSING IDENTITY BRIDGES", `erasure_plan.py:37-43`; a seção do #965 no mesmo docstring está em `erasure_plan.py:45-51`).

- **Ponte A:** `titular_pseudo_id` → `fhir_patient_id` (o processo DSR carrega pseudônimo; as duas colunas de titular da cadeia Alembica são `fhir_patient_id` — `agent_memory` 0001:69, `erasure_log` 0004:67).
- **Ponte B:** `thread_id` → `fhir_patient_id` (a camada de trabalho é chaveada por `thread_id`/`checkpoint_ns`, não por paciente).

Propriedades declaradas no módulo (`identity_bridges.py:17-46`):

- Cada ponte atrás da SUA flag, **OFF por padrão**: `MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED` / `MAEZO_ERASURE_BRIDGE_THREAD_ENABLED`; só o literal `true` habilita (1/yes/on/vazio/typo = OFF — `identity_bridges.py:49`). Virar flag é ato da onda ROLL, com janela de evidência e revisor nomeados; nenhum `deploy/**` pode ligá-las neste programa.
- **NAO decide nada:** a travessia só acontece mediante a decisão do DPO registrada no formato fechado existente — o plano de eliminação RATIFICADO (`load_erasure_plan`). As 16+ decisões por relação permanecem HUMANAS; nenhuma existe no PR (`identity_bridges.py:25-28`).
- **NAO elimina dado nenhum:** é travessia de LEITURA de identidade; `ErasureManager.erase()/verify()` seguem sempre-raising, intocados (`identity_bridges.py:35-39`).
- **NAO tem fonte própria nem chamador de produção:** a origem do mapeamento é um `ErasureBridgeSource` que o CHAMADOR provisiona (sem fonte = recusa `source_unbound`, nunca inventa mapeamento); nenhum caminho de runtime chama `resolve_*` — pinado por teste (43 novos em `tests/unit/platform/test_identity_bridges.py`) (`identity_bridges.py:40-44`).
- Fail-closed com recusas tipadas (`flag_off`, `source_unbound`, `no_mapping`, `relation_coverage_divergence`, `tenant_divergence`, `resolution_divergence` etc. — `identity_bridges.py:47-69`); zero PHI novo em log/erro/detalhe de recusa (`identity_bridges.py:70-74`).

### 3.2 Qual decisão habilita o quê

`identity_bridges.py:28-34` — as decisões que CADA ponte espera consumir (todas do plano D-25a, hoje `PENDENTE`):

| Ponte | Decisões por relação que a travessia consome | Relações (§1.3) |
|---|---|---|
| **A** (`titular_pseudo_id → fhir_patient_id`) | as decisões das relações chaveadas por `fhir_patient_id` | **`agent_memory` (#7) e `erasure_log` (#12)** |
| **B** (`thread_id → fhir_patient_id`) | as decisões das relações da camada de trabalho chaveadas por thread | **`checkpoints` (#1), `checkpoint_blobs` (#2), `checkpoint_writes` (#3)** |

Ou seja: **D-26 é a decisão de aceitar o substrato como a travessia contratada** — o ato que o habilita a ser usado é a ratificação do plano (D-25a) MAIS o flip de flag na onda ROLL com revisor nomeado e amarração de fonte criada na mesma onda. Ratificar o plano sozinho continua não ligando nada (`execution_mechanism_absent` — §1.2). Nenhuma aprovação é preenchida neste pacote.

- **Impacto do silêncio:** nenhum (dark é o estado seguro) — mas as 5 relações consumíveis pelas pontes ficam decisões que o DPO toma sem o instrumento contratado, e o resíduo "PONTE_AUSENTE" dos relatórios de dry-run permanece a única resposta possível.

---

## 4. Revisões de habilitação do Lucas (operação/financeiro)

Quatro portões separam o Lucas do ambiente declarado. A cerca de CI `scripts/ci/check_roteador_lucas.py` mecaniza a regra e nomeia as três condições humanas: "aceitavel em `dev-sa-east-1` … e em nenhum outro lugar enquanto nao existir **fonte real, DMN aprovada e DPA do provedor do Lucas**" (`scripts/ci/check_roteador_lucas.py:9-10`).

| # | Portão | Estado hoje | Revisor |
|---|---|---|---|
| 4.1 | **DMN `lucas_billing_admissibility.dmn`** — limiar `ciclos_sem_conciliacao >= 1` como indício de inadimplência (política de conciliação CNAB); mapeamento `tipo_solicitacao` → RESPONDER/LEMBRETE/ESCALAR_HUMANO; catch-all fail-safe; invariante {RESPONDER, LEMBRETE, ESCALAR_HUMANO} — nenhum caminho cobra/suspende/nega | `DRAFT — requires human review (financeiro/PO)` (shape FINAL) — `docs/review-queue.md:15` | financeiro/PO |
| 4.2 | **DMN `lucas_escalation_routing.dmn`** — mapeamento `motivo`/`intencao` → grupo humano (CONTRATOS_HUMANO/COBRANCA_HUMANO/ATENDIMENTO_HUMANO); taxonomia de grupos; catch-all conservador | `DRAFT — requires human review (financeiro/PO)` (shape FINAL) — `docs/review-queue.md:16` | financeiro/PO + gestao assistencial |
| 4.3 | **Fonte real de cobrança** — o Lucas atende cobrança com fonte SIMULADA (`MAEZO_LUCAS_FONTE_COBRANCA`); validação de offsets CNAB 240/400 contra arquivos de retorno reais do banco contratado segue DRAFT | `DRAFT — requires bank-file validation before production` — `docs/review-queue.md:144` (revisor: WC.3/Lucas + operações financeiras) | operações financeiras |
| 4.4 | **DPA do provedor do Lucas** — condição literal da cerca (4. acima); na família de pendências jurídicas o DPA de endpoint/provedor é bloqueador humano registrado (`docs/Tarefas_Pendentes.md:365`, `:734`) | pendente (humano-gated) | jurídico/DPO |
| — | **Flag de roteamento** — `roteador_lucas_enabled` default `False` literal, só liga em dev, roda em SOMBRA quando ligado (`src/maezo/platform/webhooks/whatsapp/settings.py:265-274`; cerca reprova default diferente de `False` — `check_roteador_lucas.py` item 4) | OFF; flip = decisão da onda ROLL com revisor | dono + operação |

Relacionado (mesma janela, outro gate): **BRDG-3.6 / D-21** — "ingresso Lucas Compras/Suporte vs autoridade real" pós-ponte comercial (plano `:89`, `:126`) — depende da autoridade congelada C6-4 e das admissões A1-A10, fora do escopo deste pacote.

**Nota de escopo (nomes):** as linhas de privacidade CODEOWNED citam `@lucasreisEvah` (`.github/CODEOWNERS:120`, `:138`, `:142`); a ativação da security-team (GOV-0.10) pede ao dono "confirmar/vetar lucasreisEvah nas linhas de privacidade" (plano `:44`, `:52`). Esta pendência é do dono, não deste pacote — registrada aqui porque as contras-assinaturas de §1 passam por essas linhas.

---

## 5. R1–R5 — cadeia de ratificação que bloqueia OP17 (`commission.settle`)

Estado do fato: OP17/OP19 permanecem `candidate_not_live`; 0 hits de "commission" em `src/` (grep 2026-10-24) — `docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md:96` e `:134`. Nenhuma DMN `comissao_alcada`, nenhum worker, nenhum ledger existe (`docs/audits/JOURNEYS-COMPLETENESS-2026-10/TRACEABILITY.md:193`).

A cadeia (definição registrada no dossiê vendor; transcrita verbatim do snapshot do programa — `docs/audits/JOURNEYS-COMPLETENESS-2026-10/input-snapshots/OP-REGISTRY-VENDOR-V1.md:91`, bundle gitignored; transcrição para sobreviver à edição concorrente do bundle):

> "**authority_gate (cadeia R1–R5, A1-b):** R1 contrato/política de comissão = SME comercial + jurídico · R2 escada de alçada = finanças + compliance · R3 tetos por tenant = diretoria AMH + finanças (até lá FAIL-CLOSED: teto zero ⇒ todo payout a comitê) · R4 fórmula por produto/contrato versionada = dono de produto + finanças · R5 relato RN 518/2022 3.1 e–f + trilha RN 528/2022 6.3.9.1,a = conformidade (dever da OPERADORA para o REGULADOR). Todos **RATIFY-LATER**; fences análogas a `tests/unit/sec/test_pagto_valor_fence.py` são PRÉ-CONDIÇÃO de sign-off (E-3)."

Pontos de ancoragem no registro vivo: `RATIFICATION_MISSING` (R1–R4 não assinados ⇒ nenhum payout real; estado fail-closed prolongado é operável — precedente D-07) e `GENERATING_FACT_UNPROVEN` (bloqueio AML, RN 529/2022 art. 5º, f) como recusas projetadas (`OP-REGISTRY-VENDOR-V1.md:93,98`); a DMN `comissao_alcada` só existe com R1–R4 ratificados e nenhum número (percentual, faixa, janela, share, teto) existe no registro (`:98`).

Relevância para este pacote: R2/R3/R4 têm **finanças** como co-decisor — a mesma operação/financeiro do bloco 4. R1/R5 puxam jurídico/compliance (pacote jurídico já estacionado em `docs/compliance/legal/`). Nenhum dos cinco é pré-condição do muro 2027-02-09; bloqueiam apenas a implementação de OP17 (que o programa mantém fora do escopo — plano `:146` "implementar OP17 (só disposição)").

---

## 6. Formato de resposta esperado (decisão assinável — você preenche, agente não preenche)

**D-25a (plano de eliminação)** — PR de DADOS em `spec/policies/retention/erasure-plan.template.yaml`, cobrindo as 41 relações (o carregador recusa cobertura parcial):

```yaml
status: RATIFICADO
ratificado: true                      # booleano literal true
dpo_review: APPROVED
dpo_reviewer: "<nome, designação>"    # humano
dpo_review_date: "AAAA-MM-DD"
evidence_ref: "<linha do docs/evidence-ledger.md>"
notes: "<registro do DPO>"
camadas:
  - camada: trabalho
    tabela: checkpoints
    decisao_dpo: ELIMINAR | ANONIMIZAR | RETER_COM_BASE_LEGAL
    base_legal: "<base legal quando não eliminar>"
    retencao: "<prazo/regra>"
  # ... para as 41 relações — nenhuma pode permanecer placeholder
```

**D-25b** — assinatura do bloco §7 GP11 no VW4-ANSWERS (ato do ratificador nomeado, registrado no VW0-DECISION-REGISTER) + revisão DPO formal para promoção do SP-OP-SUPP-001 a FINAL (encarregado art. 41 §4ºd + conteúdo da `suppression_routing`).

**D-26** — disposição registrada aceitando (ou recusando) o substrato #965 como a travessia contratada; nenhum campo de código muda. O efeito real fica condicionado a D-25a + flip ROLL com revisor nomeado.

**Habilitação do Lucas** — uma linha por DMN em `docs/review-queue.md` (ratificado/recusado/condicionado) + registro da validação de banco (4.3) + DPA assinado (4.4).

**R1–R5** — um registro de ratificação por letra, nomeando quem assina de cada papel (SME comercial+jurídico; finanças+compliance; diretoria AMH+finanças; produto+finanças; conformidade), com as fences E-3 como pré-condição de sign-off.

## 7. Impacto do silêncio — resumo

| Pendência | Silêncio custa |
|---|---|
| D-25a | Chave A do erasure nunca vira; dry-run segue recusando; fila de privacidade 100% pendente contra o muro do programa |
| D-25b | SP-OP-SUPP-001 não promove a FINAL; pacotes de engenharia vendor (GP5–GP11) permanecem destraváveis-por-assinatura e destravados por ninguém |
| D-26 | substrato dark sem consumidor; as 5 relações consumíveis ficam sem instrumento contratado |
| Lucas | agente segue sem fonte real, DMNs DRAFT, flag OFF — nenhum atendimento de cobrança real |
| R1–R5 | OP17 permanece `candidate_not_live` (sem custo de prazo; bloqueia apenas a implementação futura) |

## 8. Contagens conferidas

- **41 relações** com `decisao_dpo: PENDENTE` no template = 41 entradas `camadas:` = 41 em `PERSISTENCE_LAYERS` (`src/maezo/platform/lifecycle/erasure_plan.py`) — anti-drift por teste nos dois sentidos. ✓ (rótulo "16" do plano: deriva registrada em §0.)
- **2 pontes** de identidade (#965): A consome 2 relações (`agent_memory`, `erasure_log`); B consome 3 (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) — 2 + 3 = 5 relações consumíveis. ✓
- **7 blocos** de ratificação VW4 §7 (GP5–GP11), sendo GP11 o do DPO. ✓
- **2 DMNs do Lucas** DRAFT na fila (`docs/review-queue.md:15-16`). ✓
- **5 letras** R1–R5; 3 delas com finanças como co-decisor (R2, R3, R4). ✓
