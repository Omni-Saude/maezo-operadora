# Contrato — SP-OP-SUPP-001 (Direitos de Não Contato — supressão vendor, GP11)

**Status:** DRAFT (v1.0.0) — wiring GP11 materializado (insumos aceitos sha `ab262f7b…`); `DRAFT — requires human review before any deploy` (docs/review-queue.md, linha SP-OP-SUPP-001)
**BPMN:** `spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn` · **DMN:** `spec/processes/dmn/suppression_routing.dmn`
**Fonte canônica do contrato:** OP20 `suppression.record` — `docs/audits/VENDOR-XP-2026-10/OP-REGISTRY-VENDOR-V1.md` (sha `2a5a92c8…`)
**Admissão:** GP11 ADMITIDO pelo dono em 2026-10-07 (VW0-DECISION-REGISTER §"Decisões de Fechamento", item 1) e INSUMOS DPO **ACEITOS PELO DONO em ato próprio** na sequência (VW0-DECISION-REGISTER §"INCORPORAÇÃO VW4-ANSWERS", sha `ab262f7b…`): k-anon `k=100` sobre TUPLA C2/C6 (§4a), taxonomia C1–C6 com regras de chokepoint (§4b), SLA `T_total`=15 dias úteis com deadline NO NASCIMENTO do registro + escalonamento 50/80/100 (§4c), encarregado art. 41 (§4d), KPI `phi_egress_violations==0` (§4e). Este pacote de wiring materializa os insumos.

## Contrato tipado (request → result)

`SuppressionCommand → SuppressionRecord` — registro preventivo, honrado na **construção de lista** (antes do contato). Implementação tipada: `src/maezo/gateway/vendor_suppression.py` (pydantic fechado, recusas `SuppressionRefusalReason`, business key nomeada, relógio `EscalationClock` computado no nascimento, calendário own-code seg–sex **sem feriados** — limite documentado; store 0021 `PostgresSuppressionStore`).

## Business key (NO-DUPLICATE-INSTANCE, correlação por NOME)

`SUPP-{tenant_id}-{suppression_ref}` onde `suppression_ref` = digest canônico de `(subject_ref, contact_channel)` cunhado no plano de aplicação (`gateway/vendor_suppression.py#suppression_ref_of` — worker, plano de lista e store cunham o MESMO digest) — projeção fiel da chave do registry `tenant + suppression + subject_ref + contact_channel`. Reenvio retorna a instância/registro ativo; nunca segunda trilha, nunca contagem dupla de SLA.

## Relógio por registro (§4c — o relógio visível)

`t_deadline`/`t_lembrete`/`t_escalonado` são computados **NO NASCIMENTO** do registro pela camada de aplicação (`EscalationClock.compute`) e gravados na migration 0021. Registro sem deadline = recusa do store (`DEADLINE_UNCOMPUTABLE` — coluna NOT NULL é a verdade de storage). Os três timers da `UT_RotearEncarregado` são `timeDate` ABSOLUTOS sobre as variáveis do registro (idioma CONTAS GAP-4: âncora = fato gerador, nunca o attach da UT): **50%** lembrete (7º dia útil — piso de 7,5, nunca depois da metade), **80%** escalonamento ao encarregado (12º, exato), **100%** timeout `ANALISE_HUMANA` (= `t_deadline`, 15º). Notificação a agentes de uso compartilhado (art. 18 §6º) pertence a `T_apply` — nunca pós-SLA.

## Eventos de domínio (via `operadora.events.publish` — tópico consumido, ADR-0003 dispatch-por-fato)

| Evento | Momento |
|---|---|
| `agents.events.vendor.suppression.recorded` | início da instância |
| `agents.events.vendor.suppression.honored_in_list` | só com `rota == "REGISTRO_HONRADO"` EXPLÍCITO |
| `agents.events.vendor.suppression.complaint_routed` | pós-decisão humana do encarregado |
| `agents.events.vendor.suppression.completed` | recusas (desfecho = `sujeito_irresolvivel` \| `roteamento_indisponivel`) |
| `agents.events.vendor.suppression.sla_lembrete` | boundary timer 50% do SLA (não-interrupting — decisão humana continua) |
| `agents.events.vendor.suppression.sla_escalonado` | boundary timer 80% (escala ao encarregado, grupo `dpo`) |
| `agents.events.vendor.suppression.sla_estourado` | boundary timer 100% — `ANALISE_HUMANA` (interrupting; registro visível e atrasado, nunca silencioso) |

## Recusas (registry OP20 → erros modelados)

| Registry | Erro modelado | Boundary | Semântica |
|---|---|---|---|
| `SUBJECT_UNRESOLVED` | `ERR_SUPP_SUBJECT_UNRESOLVED` | `ST_VerificarSujeito` | store/identidade ausente = UNKNOWN, nunca zero (molde `erasure_plan.py#IdentityResolution` L193) |
| `PHI_IN_COMMERCIAL_INPUT` | (camada aplicação — PR-2; G-PHI estrutural: nenhum campo clínico existe no processo) | — | dado de beneficiário nunca é input de targeting |
| `AUDIT_UNAVAILABLE` | (camada aplicação — PR-2: audit-before-write) | — | recusa sem trilha = zero efeito |
| — (fail-closed de admissão) | `ERR_SUPP_ROUTING_UNAVAILABLE` | `ST_AvaliarRoteamento` | DMN/`CibSevenDmnTransport` não servida ou insumos pendentes |

`STALE_REVISION`/`CHANNEL_STATUS_INELIGIBLE`/`CONTRACT_MISMATCH`/`SOURCE_UNAVAILABLE`: recusas da camada de aplicação (PR-2), não do processo engine.

## Roteamento (DMN `suppression_routing`, engine-side)

`CibSevenDmnTransport` ONLY (evaluator local proibido — `dmn_transport.py:1-8`); inputs/outputs por NOME. Tabela com **partições da taxonomia C1–C6** (insumos aceitos; hitPolicy FIRST — ordem semântica) e **catch-all ÚLTIMA row → `RECLAMACAO_ENCARREGADO`/`dpo`** (humano; fail-closed operante — precedente D-07). `k_piso` é INPUT/PARAM — a cifra ratificada vive na constante aceita (`gateway/vendor_suppression.py#K_ANON_FLOOR`), nunca na tabela; `canal` permanece coluna declarada sem partição nesta versão (disclosure na fence de colunas mortas). `GW_Rota`: só `REGISTRO_HONRADO` explícito honra; ausente/branco/desconhecido → humano — nunca honra por omissão. Worker via seam `dmn=` (ADR-0028) com guard de seams — transport ausente mantém `ERR_SUPP_ROUTING_UNAVAILABLE`.

## Invariantes

- Minimização art. 10 §1º: identificador + data + canal, nada mais; `subject_ref` OPACO (ADR-0006).
- RELÓGIO POR REGISTRO (§4c, insumos aceitos): deadline computado NO NASCIMENTO (calendário own-code seg–sex SEM feriados — limite documentado); escalonamento 50/80/100 com timers `timeDate` absolutos; registro sem deadline = recusa do store. NENHUM outro timer de SLA. TTL de limpeza de histórico do engine (`historyTimeToLive`: BPMN 1825 / DMN P180D = precedente LGPD-DSR) NÃO é o prazo de retenção de negócio — o engine roda `enforceTTL` (ENGINE-12018, prova local: sem TTL o deploy é RECUSADO); retenção de negócio segue VW0-D12 RATIFY DPO (downgrade da 0021 recusa store populado).
- Sem PHI em nenhuma superfície (G-PHI absoluto — OP20 é a superfície de teste da invariante); sem RATE/comparação entre operadoras (G-CADE).
- Honrar registro = `REGISTRO_HONRADO` EXPLÍCITO da DMN + plano de lista — nunca automática por omissão; recusa tipada ≠ violação (§4e critério 4).
- KPI: `supressões honradas no SLA / total` observável pelos eventos `sla_*`; `phi_egress_violations == 0` (§4e — violação é conteúdo proibido que PASSOU a fronteira, nunca a recusa).
