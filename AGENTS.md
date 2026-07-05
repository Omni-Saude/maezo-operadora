# AGENTS.md — Maezo Operadora (Greenfield v2)

> **Repo:** Omni-Saude/maezo-operadora · **Orquestrador:** Parreira
> **Estado:** Greenfield Day 0 — especificação pronta, implementação a construir

## Contexto essencial (ler primeiro)

1. `PLANS.md` — **Este é o artefato de coordenação central.** 15 milestones, dependências, gatekeepers. Consulte antes de qualquer ação.
2. `docs/` — **A especificação canônica.** ~205K palavras, ~1000 regras de negócio. TODO código deve ser rastreável a um contrato, ADR ou DL.
3. `spec/` — Artefatos portados do repo anterior como aceleradores (BPMN, DMN, autonomy, agent contracts). Não são código de implementação.
4. `PROJECT.md` — Arquitetura em 30s e mapa do repo (a ser atualizado conforme o código é construído).
5. `docs/adr/README.md` — Índice de decisões; **nunca contrarie um ADR aceito sem propor um novo.**

## O que é diferente neste repo (vs Maezo-Healthcare-Plan)

| Característica | Maezo-Healthcare-Plan (v1) | Maezo Operadora (v2) |
|---------------|---------------------------|----------------------|
| Origem | Construído por AI swarms incrementalmente | Greenfield a partir de spec completa |
| Spec | Escrita depois do código | Escrita antes do código |
| Cobertura funcional | ~30-40% dos contratos | Alvo: 100% dos contratos |
| Código inicial | 55K LOC Python portado | Zero — tudo reconstruído |
| ADRs | 14/24 "Proposed" | Todos "Accepted" antes de implementar |
| Qualidade alvo | B+ (pré-produção) | A (production-ready) |

## Regras duras para geração de código

1. **Spec-first: código é derivado dos contratos.** Cada worker, cada DMN, cada variável de processo deve ser rastreável ao contrato SP-OP correspondente em `docs/processes/contracts/`.
2. **Não criar arquivos de tracking** (`PROGRESS.md`, `STATUS.md`, `TODO.md`) — use `PLANS.md`, `RUNBOOK.md`, `CHECKPOINT.md`.
3. **Não criar árvore paralela.** Código vai em `src/maezo/`; sem duplicação.
4. **Não mockar o engine em testes de integração.** Se a stack docker não está disponível, marque o teste como `integration` e deixe o CI rodá-lo.
5. **Não inventar regra de negócio em Python/prompt.** Regra determinística → tabela DMN; nível de permissão → `spec/policies/autonomy/`; prazo regulatório → timer BPMN.
6. **Não importar SDK de LLM fora de `runtime/inference.py`** nem credencial fora do gateway.
7. **Itens `hard` da matriz de autonomia são intocáveis** (negativa, decisão clínica, acusação de fraude).
8. **82 findings da auditoria são checklist "não repetir".** Consulte `docs/reports/predeploy-findings.json` antes de implementar qualquer componente.
9. **34 DL entries são padrões de design obrigatórios.** Consulte `docs/decisions-log.md` — cada DL contém uma lição paga com bugs reais.
10. Geração em massa (swarm): cada lote termina com `make lint type test validate-artifacts` verde **e poda do que não é usado**.

## Gestão de contexto e memória

Este é um desenvolvimento extenso. **Nunca carregue toda a spec de uma vez.**

### Carregamento progressivo de contexto

```
Para cada milestone:
1. PLANS.md (a milestone atual)                  → sempre carregado
2. Contrato(s) SP-OP relevantes (1-3)            → carregar sob demanda
3. ADRs relevantes (2-5)                         → carregar sob demanda
4. DLs relevantes (3-8)                          → carregar sob demanda
5. BPMN/DMN correspondentes em spec/processes/   → carregar sob demanda
6. Histórico de sessão                           → session_search apenas decisões relevantes
```

### Anti-padrões de contexto

- ❌ Carregar todos os 24 ADRs de uma vez
- ❌ Carregar todos os 16 contratos SP-OP de uma vez  
- ❌ Ler arquivos sem search primeiro (`search_files` antes de `read_file`)
- ❌ Repetir contexto que já está no PLANS.md ou RUNBOOK.md
- ❌ Começar a codar sem verificar o contrato SP-OP correspondente

### Estado durável (fora da conversa)

| Artefato | Propósito |
|----------|-----------|
| `PLANS.md` | Plano completo, progresso das milestones |
| `RUNBOOK.md` | Log de cada milestone, decisões, bloqueios |
| `CHECKPOINT.md` | Status da milestone atual, evidências |
| `SESSION.log` | Timeline de comandos e outputs |

**Princípio:** Se a sessão cair, outro agente deve conseguir continuar lendo esses arquivos.

## Vocabulário do domínio

| Termo | Significado |
|-------|-------------|
| Operadora | Plano de saúde (payer) — nosso cliente/tenant |
| Beneficiário | Cliente final da operadora |
| Prestador | Hospital/clínica/médico que atende o beneficiário |
| Guia TISS | Padrão ANS de troca de informação (autorização, cobrança) |
| Glosa | Recusa (parcial/total) de pagamento de conta médica |
| DUT/ROL | Diretrizes de Utilização / Rol de procedimentos ANS |
| NIP | Notificação de Intermediação Preliminar (reclamação ANS) |
| Negativa | Recusa de cobertura — SEMPRE decisão de médico auditor humano |
| SP-OP-* | Processo BPMN compliance |
| AGJ-* | Jornada conduzida por agente (sem BPMN) |
| L0–L3 | Níveis de autonomia (ADR-0008) |
| Zona Geral / Zona PHI | Zonas de segurança de dados (ADR-0006) |

## Referência rápida: mapeamento milestone → agentes

| Milestone | Agentes |
|-----------|---------|
| M0 | `ops-cicd-github`, `ops-containers-k8s` |
| M1 | `arch-system-design`, `security-manager` |
| M2 | `ops-compliance-gate`, `scout-explorer` |
| M3 | `ops-containers-k8s`, `tdd-london-swarm` |
| M4 | `security-manager`, `tdd-london-swarm` |
| M5 | `tdd-london-swarm`, `ops-containers-k8s` |
| M6-M8 | `tdd-london-swarm`, `ops-compliance-gate`, `production-validator` |
| M9 | `ops-compliance-gate`, `tdd-london-swarm` |
| M10 | `ops-iac-terraform`, `ops-cloud-provisioning` |
| M11 | `ops-observability`, `performance-monitor` |
| M12 | `security-manager`, `ops-secrets-management` |
| M13 | `production-validator`, `ops-incident-response` |
| M14 | `release-manager`, `ops-compliance-gate` |
