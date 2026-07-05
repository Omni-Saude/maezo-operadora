# ADR-0007: Identidade de agente, auditoria e nao-repudio

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Auditoria

## Contexto
"O modelo respondeu X" nao e resposta aceitavel para ANS/justica. Toda decisao precisa de
quem/com-base-em-que/sob-qual-versao.

## Decisao
- Identidade de servico por agente+tenant (service account + certificado; Agent Card assinado).
- Todo efeito no mundo registra: `(agent_id, agent_version, tenant, tool, input_hash,
  decision_basis, dmn_versions, model_id, prompt_version, timestamp)` — Postgres append-only +
  topico `agents.audit`.
- Caminhos compliance materializam adicionalmente instancia BPMN (segunda trilha, no engine).
- `decision_basis` estruturado: regras DMN satisfeitas, evidencias citadas, score. Acoes L1
  registram o humano aprovador.

## Consequencias
**Positivas:** cadeia completa e reproduzivel; replay/post-mortem de decisoes.
**Negativas (aceitas):** volume — particionamento + TTL (regulatorio 5+ anos; operacional meses).

## Supersedes
—
