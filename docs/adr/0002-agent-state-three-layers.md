# ADR-0002: Estado de agentes em 3 camadas

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Dados

## Contexto
Agentes precisam de memoria de trabalho, episodica e semantica — LGPD-erasavel, isolada por
tenant e sobrevivente a trocas de modelo LLM.

## Decisao
1. **Working:** LangGraph checkpointer (PostgreSQL, schema `agents`); expurgo pos-tarefa.
2. **Episodica:** transcricoes/decisoes/eventos particionados por tenant, chave `fhir_patient_id`; anexos S3.
3. **Semantica:** anotacoes derivadas em texto estruturado + embeddings pgvector. Conteudo canonico
   NUNCA mora aqui — sempre referencia a recurso FHIR. Embeddings sao descartaveis/re-indexaveis.
4. **Erasure LGPD:** delete por `fhir_patient_id` cascateia pelas 3 camadas; verificacao mensal.

## Consequencias
**Positivas:** troca de modelo nao perde memoria; direito ao esquecimento em SQL; zero infra nova.
**Negativas (aceitas):** pgvector tem teto de escala — revisitar acima de ~5M embeddings/instancia.

## Supersedes
—
