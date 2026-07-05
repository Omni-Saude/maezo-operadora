# ADR-0006: PHI em duas zonas; pseudonimizacao no gateway

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** LGPD/Seguranca

## Contexto
LGPD exige minimizacao; PHI em APIs externas de LLM cria risco. Mas agentes de back-office
precisam do dado integral.

## Decisao
- **Zona Geral** (Helena, Lucas, Fernando, Gustavo): PHI pseudonimizado no Tool Gateway ANTES do
  contexto LLM; mapa de reidentificacao so no gateway; modelos cloud com DPA + regiao.
- **Zona PHI/Financeira** (Rafael, Marina, Beatriz, Valentina, Carolina, Andre): PHI integral somente
  em modelo on-prem ou endpoint contratado com residencia BR + zero-retention; **NetworkPolicy K8s
  impede egress fora da allowlist** (garantia de rede, nao de prompt).
- Logs/traces passam pelo mesmo pseudonimizador. Memoria semantica armazena derivados minimizados.

## Consequencias
**Positivas:** minimizacao sem cegar agentes; todo PHI que tocou LLM externo tem registro.
**Negativas (aceitas):** leve degradacao de UX na Zona Geral; custo de endpoint dedicado.

## Supersedes
—
