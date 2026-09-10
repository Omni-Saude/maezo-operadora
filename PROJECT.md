# PROJECT.md — Arquitetura e Operação

> **Session paused at user request — 2026-09-10T01:19:37.373948+00:00.** Current resume state: [HANDOFF.md](/Users/familia/code/maezo-completion-evidence/successor-graceful-handoff-20260910/HANDOFF.md). All specialists returned; full completion remains unproved. Resume only on a new user instruction. Read the new `final-ready.json` receipt before using this snapshot. Historical states below are preserved for provenance and do not supersede this handoff.

> Current completion/deployment target is defined in `docs/plan.md` (ECS/Fargate, sa-east-1). Older architecture and deployment statements below are historical, not current deployment evidence.


> Stack: **Python 3.12 · LangGraph · A2A v1.0 · MCP · CIB Seven 2.1.3 · HAPI FHIR R4 · Kafka · PostgreSQL · Kubernetes**

## Arquitetura em 30 segundos

```
Beneficiário (WhatsApp/Portal)        Prestador (Portal TISS)
        │                                   │
        ▼                                   ▼
┌─────────────────── AGENT RUNTIME (LangGraph, por tenant) ──────────────────┐
│  Helena · Rafael · Marina · Lucas · Beatriz · Fernando · Valentina ·       │
│  Gustavo · Carolina · André          ←— A2A v1.0 (delegação de tarefas) —→ │
└──────────────────────────────────────┬─────────────────────────────────────┘
                                       ▼  (TODO tool call passa aqui)
┌────────────────────────── TOOL GATEWAY ────────────────────────────────────┐
│  PEP (autonomia L0–L3) · Pseudonimização PHI · Auditoria assinada ·        │
│  Cofre de credenciais (agente NÃO possui credencial de ação proibida)      │
└───────┬──────────────┬──────────────┬──────────────┬───────────────────────┘
        ▼              ▼              ▼              ▼
   mcp-cibseven     mcp-dmn       mcp-fhir      mcp-whatsapp / mcp-memory
        │              │              │
        ▼              ▼              ▼
┌─ GOVERNANÇA ─────────────────┐ ┌─ REGISTRO ────────────────────────────────┐
│ CIB Seven: processos SP-OP   │ │ HAPI FHIR R4 (canônico) · PostgreSQL      │
│ (SLA RN259, User Tasks HITL, │ │ (estado agentes, memória, auditoria) ·    │
│ auditoria) + tabelas DMN     │ │ Kafka (CDC, agents.events, agents.audit) │
└──────────────────────────────┘ └───────────────────────────────────────────┘
```

**Regra de ouro (ADR-0001):** o agente decide *como* trabalhar; o processo BPMN garante *que* as obrigações sejam cumpridas (SLA, humano obrigatório, trilha). BPMN só existe onde há gatilho regulatório — catálogo em [docs/processes/catalog.md](docs/processes/catalog.md).

## Mapa do repositório

| Pasta | O que é |
|---|---|
| `src/maezo/runtime/` | Harness LangGraph, checkpointer, abstração de inference (ADR-0009) |
| `spec/agents/<id>/` | Agent Definition (`agent.yaml`) — única fonte (T0.3/B14); `_template/` é o contrato |
| `src/maezo/agents/<id>/` | Grafo LangGraph (`graph.py`) do agente — lê a definição em `spec/agents/<id>/agent.yaml` |
| `src/maezo/a2a/` | Registry de Agent Cards, anti-loop (ADR-0003) |
| `src/maezo/gateway/` | PEP, pseudonimização, auditoria, credenciais (ADR-0005/0006/0007) |
| `src/maezo/tools/mcp_*/` | MCP servers — única via dos agentes aos sistemas |
| `spec/processes/` | BPMN SP-OP + DMN (artefatos validados em CI) |
| `spec/policies/autonomy/` | Matriz L0–L3 (ADR-0008) — mudança = PR com revisão de compliance |
| `src/maezo/tools/mcp_memory/` | Memória episódica/semântica (ADR-0002) — store + MCP server |
| `src/maezo/platform/` | Tenancy, integrações portadas, webhooks, validação de artefatos |
| `tests/evals/golden/` | Golden datasets por agente — gate de promoção (ADR-0009) |
| `deploy/` | K8s (namespace por tenant) + Helm |

## Como rodar (dev)

```bash
make setup        # pip install -e ".[dev]"
make dev-stack    # postgres, cibseven, hapi-fhir, kafka
cp .env.example .env  # preencher chaves
make test         # unit
make test-integration # contra engine real
make validate-artifacts
```

## Multi-tenancy

Produção: **namespace K8s por tenant**, instância própria de engine/banco/FHIR. Dev local espelha um tenant (`TENANT_ID=amh`). Provisionamento automatizado via Helm a partir do Phase 2.

## Fases (resumo)

| Fase | Entrega | Agentes |
|---|---|---|
| 0 | Helena live no WhatsApp AMH (piloto), gateway v0, SP-OP-ESCALATION | Helena |
| 1 | Prior auth fim-a-fim com médico auditor; A2A; SP-OP-AUTH | +Rafael |
| 2 | Contas/compliance em escala; 2º tenant | +Marina, Gustavo, Lucas |
| 3 | 10 agentes; pacote full-stack | +Carolina, Fernando, Valentina, Beatriz, André |

Plano completo: `implementation-plan-v2.md` (projeto de arquitetura).
