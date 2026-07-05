# ADR-0004: Instancia por tenant + Agent Definitions federadas L0-L3

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Tenancy

## Contexto
Multi-tenant e requisito duro. Agentes adicionam estado conversacional, memoria, prompts e
configs de modelo — vazamento entre tenants seria catastrofico.

## Decisao
- Instancia por cliente (engine + Postgres + FHIR), namespace K8s por tenant para o agent runtime.
- Novo artefato federado: **Agent Definition** (`agent.yaml`: prompt + grafo + tools + politica de
  autonomia + KPIs) em camadas **L0** (core plataforma) / **L1** (regulatorio BR) / **L2** (segmento)
  / **L3** (override do tenant) — modelo 80/20 herdado da federacao DMN hospitalar.
- Personalizacao por tenant = L3 de prompt/politica + few-shot. **Proibido** fine-tuning de pesos por tenant.
- Control plane compartilhado sem PHI (observabilidade agregada, provisioning).

## Consequencias
**Positivas:** zero cross-contamination por construcao; upgrade = bump de L0 com diff por tenant.
**Negativas (aceitas):** custo de infra por tenant; mitigar com right-sizing/scale-to-zero.

## Supersedes
—
