# Contrato — SP-OP-SUPP-001 (Direitos de Não Contato — supressão vendor, GP11)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md, linha SP-OP-SUPP-001)
**BPMN:** `spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn` · **DMN:** `spec/processes/dmn/suppression_routing.dmn`
**Fonte canônica do contrato:** OP20 `suppression.record` — `docs/audits/VENDOR-XP-2026-10/OP-REGISTRY-VENDOR-V1.md` (sha `2a5a92c8…`)
**Admissão:** GP11 ADMITIDO pelo dono em 2026-10-07 (VW0-DECISION-REGISTER §"Decisões de Fechamento", item 1) — envelope + DMN com **valores FAIL-CLOSED**; taxonomia de classes (VW0-D11), piso k-anon e SLA de honra (VW0-D20) permanecem insumos DPO pendentes.

## Contrato tipado (request → result)

`SuppressionCommand → SuppressionReceipt` — registro preventivo, honrado na **construção de lista** (antes do contato). Implementação tipada (pydantic fechado, recusas `refusals`, business key nomeada) vive no pacote de wiring (PR-2); este envelope pousa o processo + roteamento engine-side + worker fail-closed.

## Business key (NO-DUPLICATE-INSTANCE, correlação por NOME)

`SUPP-{tenant_id}-{suppression_ref}` onde `suppression_ref` = digest canônico de `(subject_ref, contact_channel)` cunhado no plano de aplicação — projeção fiel da chave do registry `tenant + suppression + subject_ref + contact_channel`. Reenvio retorna a instância ativa; nunca segunda trilha.

## Eventos de domínio (via `operadora.events.publish` — tópico consumido, ADR-0003 dispatch-por-fato)

| Evento | Momento |
|---|---|
| `agents.events.vendor.suppression.recorded` | início da instância |
| `agents.events.vendor.suppression.honored_in_list` | só com `rota == "REGISTRO_HONRADO"` EXPLÍCITO |
| `agents.events.vendor.suppression.complaint_routed` | pós-decisão humana do encarregado |
| `agents.events.vendor.suppression.completed` | recusas (desfecho = `sujeito_irresolvivel` \| `roteamento_indisponivel`) |

## Recusas (registry OP20 → erros modelados)

| Registry | Erro modelado | Boundary | Semântica |
|---|---|---|---|
| `SUBJECT_UNRESOLVED` | `ERR_SUPP_SUBJECT_UNRESOLVED` | `ST_VerificarSujeito` | store/identidade ausente = UNKNOWN, nunca zero (molde `erasure_plan.py#IdentityResolution` L193) |
| `PHI_IN_COMMERCIAL_INPUT` | (camada aplicação — PR-2; G-PHI estrutural: nenhum campo clínico existe no processo) | — | dado de beneficiário nunca é input de targeting |
| `AUDIT_UNAVAILABLE` | (camada aplicação — PR-2: audit-before-write) | — | recusa sem trilha = zero efeito |
| — (fail-closed de admissão) | `ERR_SUPP_ROUTING_UNAVAILABLE` | `ST_AvaliarRoteamento` | DMN/`CibSevenDmnTransport` não servida ou insumos pendentes |

`STALE_REVISION`/`CHANNEL_STATUS_INELIGIBLE`/`CONTRACT_MISMATCH`/`SOURCE_UNAVAILABLE`: recusas da camada de aplicação (PR-2), não do processo engine.

## Roteamento (DMN `suppression_routing`, engine-side)

`CibSevenDmnTransport` ONLY (evaluator local proibido — `dmn_transport.py:1-8`); inputs/outputs por NOME. Tabela nasce com **catch-all único → `RECLAMACAO_ENCARREGADO`/`dpo`** (humano; fail-closed operante — precedente D-07). Particionamento (vendedor/lead × canal) entra SOMENTE com a taxonomia ratificada. `GW_Rota`: só `REGISTRO_HONRADO` explícito honra; ausente/branco/desconhecido → humano — nunca honra por omissão.

## Invariantes

- Minimização art. 10 §1º: identificador + data + canal, nada mais; `subject_ref` OPACO (ADR-0006).
- NENHUM prazo/timer/SLA no processo ("sem prazo que corre em silêncio" — VW0-D20); NENHUM `historyTimeToLive` (retenção = VW0-D12, RATIFY DPO).
- Sem PHI em nenhuma superfície (G-PHI absoluto — OP20 é a superfície de teste da invariante); sem RATE/comparação entre operadoras (G-CADE).
- Honrar registro = decisão do plano de lista + encarregado — nunca automática (out-of-band `REGISTRO_HONRADO` da DMN exige insumos ratificados + wiring PR-2).
- KPI: `supressões honradas no SLA / total` = **0/0 hoje** (baseline honesto); `phi_egress_violations == 0`.
