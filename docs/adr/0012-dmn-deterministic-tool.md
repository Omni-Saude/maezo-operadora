# ADR-0012: DMN como ferramenta deterministica unica

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Regras de negocio

## Contexto
O repo hospitalar provou que ~80% das regras vivem bem em DMN (federada, versionada, testavel).
No paradigma agents-first, quem consome a regra e primariamente o agente.

## Decisao
- DMN e a **fonte unica de regra de negocio deterministica**. Duas vias de consumo, mesma tabela:
  `mcp-dmn` (agentes) e service tasks (processos SP-OP).
- LLM **nunca** decide o que uma regra deterministica pode decidir; o agente raciocina sobre o
  RESULTADO da DMN (explica, pendencia, recomenda) — nao a substitui.
- Conteudo payer-side: DUT/ROL/carencia (novo) + porte re-semantizado do conteudo hospitalar
  (DOMED, elegibilidade, glosa) com SME.
- Pipeline `contract_extraction` (portado) gera DMN a partir de contratos do tenant.

## Consequencias
**Positivas:** regras auditaveis fora do LLM; simetria agente/processo; menos alucinacao em decisao.
**Negativas (aceitas):** porte de conteudo e esforco real de SME (semanas).

## Supersedes
—
