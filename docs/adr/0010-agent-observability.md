# ADR-0010: Observabilidade de agentes

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Operacoes

## Contexto
Historico BPMN cobre processos; nao cobre raciocinio, custo de tokens, qualidade de conversa,
loops A2A. "Por que Helena respondeu isso ontem?" precisa de resposta em minutos.

## Decisao
- OpenTelemetry com semantica de agentes: trace por conversa/task A2A; span por no LangGraph,
  tool call e chamada LLM (model_id, tokens, custo).
- Metricas por agente: latencia, taxa de escalonamento, **taxa de aprovacao humana de propostas L1**
  (proxy de qualidade/drift), custo por interacao, KPIs do blueprint.
- Logs pseudonimizados (ADR-0006). Dashboards "ficha de funcionario" por agente.
- Replay de conversa: checkpoints + trace. Alertas: budget A2A estourado, queda de aprovacao
  humana, spike de custo.
- Sampling: conversas L3 amostradas; L0-L2 retencao integral.

## Consequencias
**Positivas:** gestores humanos dos agentes ganham instrumento real.
**Negativas (aceitas):** volume de traces LLM — politica de retencao desde o dia 1.

## Supersedes
—
