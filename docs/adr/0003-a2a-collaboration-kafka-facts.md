# ADR-0003: A2A v1.0 para colaboracao; Kafka para fatos

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Comunicacao

## Contexto
Agentes delegam tarefas entre si (Helena->Rafael) e reagem a eventos (CDC -> Beatriz).
RPC-sobre-Kafka e anti-padrao; HTTP ad-hoc nao da identidade nem lifecycle.

## Decisao
- **A2A v1.0** (Linux Foundation) para delegacao dirigida: Agent Cards assinados, lifecycle de task,
  deadline, `delegation_chain`. HTTP/JSON-RPC + mTLS interno. SDK encapsulado em `maezo.a2a`.
- **Kafka** para fatos broadcast (`agents.events.*`, `agents.audit`) e gatilhos proativos.
- Regra: comando dirigido = A2A; fato assincrono = Kafka.
- Anti-loop (enforced no runtime): chain aciclica, `max_hops=3`, budget tokens/tempo, task_id idempotente.

## Consequencias
**Positivas:** interop futura (operadora<->prestador<->ANS); identidade forte; CDC intacto.
**Negativas (aceitas):** padrao jovem — encapsular SDK; registry e componente novo a operar.

## Supersedes
—
