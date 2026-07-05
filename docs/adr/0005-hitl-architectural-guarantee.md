# ADR-0005: HITL como garantia arquitetural (nao prompt)

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Seguranca/Compliance

## Contexto
RN 259 proibe negativa de cobertura sem medico auditor. Prompt nao e mecanismo de enforcement.

## Decisao
Quatro mecanismos independentes:
1. **PEP** no Tool Gateway avalia a matriz de autonomia (ADR-0008) antes de TODO tool call efetivo.
2. Acoes mandatorias-humanas = **User Task BPMN** com timer+escalation; processo nao avanca sem humano.
3. **Separacao de credenciais:** a credencial da acao proibida nao existe no runtime do agente.
4. Toda recusa do PEP gera evento auditavel.

Negativa: agente produz recomendacao estruturada + dossie; medico auditor decide; a decisao sai
em nome do humano. **Decisao clinica/diagnostica por agente: proibida, hard-coded, nao configuravel.**

## Consequencias
**Positivas:** violar exige comprometer infraestrutura, nao convencer um LLM; ANS-defensible.
**Negativas (aceitas):** latencia humana no caminho — o design otimiza o dossie, nao remove o humano.

## Supersedes
—
