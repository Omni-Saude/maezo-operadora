# Phase 0 Report — "Helena fala"

**Status:** Engenharia COMPLETA · main verde em todos os gates (incl. integração vs engine real) · DoD de staging **bloqueado por acesso AWS** ([issue #16](https://github.com/Omni-Saude/Maezo-Healthcare-Plan/issues/16))
**Data:** 2026-06-12 · **Commit:** `77c2ce9` · **PRs:** 15 mergeados (#1–#15)

## O que existe e funciona (evidência = CI)

| Capacidade | Evidência |
|---|---|
| Validação de artefatos como gate real (BPMN/DMN/policies/agent.yaml, hard-items congelados, ordenação XSD, TTL camunda) | `validate-artifacts` verde; 3 classes de defeito de engine agora pegas estaticamente |
| Tool Gateway: PEP fail-closed, pseudonimizador determinístico, auditoria hash-chained | 44 testes + pen-test: 20 casos adversariais (overlay/spoof/homoglifo) todos negados |
| Runtime: harness LangGraph via gateway, inference com guarda estrutural de zona PHI, checkpointer Postgres | resume pós-pod-kill provado contra Postgres real em CI |
| Processos: SP-OP-ESCALATION-001 (FINAL) deployável e executável no CIB Seven 2.1.0 real — timers, User Tasks, DMN de roteamento, fallback de notificação | 10 testes de processo verdes contra engine real |
| Integração: simulador Tasy (contrato ADR-0013), FHIR sync Patient/Coverage, webhook WhatsApp (HMAC+idempotência) | suíte de integração verde; e2e journey verde |
| Helena: grafo triagem (LLM extrai, DMN decide — ADR-0012), 270 conversas golden, adapter PEP-first | evals verdes; e2e red-flag → escalação com business key idempotente |
| e2e DoD (local/CI): WhatsApp → Helena → FHIR → red flag → SP-OP-ESCALATION-001 | `tests/integration/e2e/test_phase0_journey.py` verde |
| PHI leak: nenhum PHI cru chega ao LLM ou à auditoria | `tests/integration/sec/test_phi_leak.py` verde |
| Observabilidade/infra: Terraform validado, Helm + NetworkPolicies de zona, dashboards, runbooks (6) | jobs terraform/helm verdes; docs/runbooks/ |

## Demo

`docs/runbooks/phase0-demo.md` — roteiro completo sobre docker-compose (profiles core+simulator). Staging = mesmo roteiro após `terraform apply` (bloqueado, issue #16).

## Defeitos sistêmicos encontrados e institucionalizados

A doutrina "nunca mockar o engine" pagou: 3 classes de defeito só apareceram no deploy real
(ordenação XSD BPMN; `historyTimeToLive` obrigatório; namespace camunda do TTL) e 4 drifts
de integração só no runtime real (CSV de input params, DTO de Job sem activityId, ações PEP
vs métodos MCP — onde o PEP fail-closed negou corretamente —, fixture de falha não-sticky).
Todas as classes estáticas têm guarda permanente no validate-artifacts.

## Riscos atualizados

1. **Acesso AWS** — único bloqueio do DoD (issue #16). Mitigação: demo CI-equivalente já roda.
2. **Revisão humana regulatória** — 11 itens DRAFT (review-queue) + 9 ações de autonomia (avaliadas pelo SEC como adequadas; 2 endurecimentos para Phase 1).
3. **RNs não confirmadas** — SLAs marcados DRAFT/verify (RN 259/395/424) precisam de jurídico antes de timers em produção.
4. **Versão do engine** — ADRs citam 2.1.3; imagem pública é 2.1.0 (DL-0006). Revalidar no upgrade.
5. **Phase 1 SEC flags** — restringir `start_compliance_process` por process_key; teste arquitetural pseudonimizador-sempre (W8).

## Próximos passos

- Humano: conceder acessos da issue #16; revisar review-queue.
- Swarm: drafting DUT/ROL DMN (longo prazo, sancionado pré-DoD §6) já em andamento; staging apply + demo assim que acesso chegar; Phase 1 kickoff (processos já em DRAFT desde W5).
