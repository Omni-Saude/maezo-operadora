# ADR-0001: CIB Seven como backbone de governanca; LangGraph como runtime de agentes

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Orquestracao

## Contexto
Agents-first exige loops de raciocinio, conversas multi-dia e colaboracao dinamica — que BPMN
expressa mal. Mas a operadora vive sob SLAs regulatorios (RN 259), decisoes mandatorias-humanas
e auditoria ANS — que BPMN expressa melhor que qualquer alternativa. Alternativas avaliadas:
tudo-no-engine; pivot total (Temporal/LangGraph); hibrido com papeis estritos.

## Decisao
Hibrido com papeis estritos:
- **LangGraph (Python) + checkpointer PostgreSQL** = runtime dos agentes (raciocinio, multi-turno, tools).
- **CIB Seven** = exclusivamente governanca: processos SP-OP (SLA regulatorio, User Tasks HITL,
  auditoria de nao-repudio, transacao multi-ator legal) e avaliacao DMN.
- Agentes iniciam/avancam processos via REST (`mcp-cibseven`); processos convocam agentes via
  External Task (sentido residual).
- **Regra de ouro:** agente decide *como* trabalhar; processo garante *que* obrigacoes sejam cumpridas.

**Gatilho de reavaliacao:** >2 casos de jobs longos nao-regulatorios com perda de estado comprovada
→ avaliar Temporal restrito a jobs tecnicos (nunca substituindo BPMN regulatorio).

## Consequencias
**Positivas:** compliance "process-shaped" onde BPMN e imbativel; agentes com runtime adequado;
DMN preservada como ativo central.
**Negativas (aceitas):** dois paradigmas para operar; a fronteira exige disciplina de revisao.

## Supersedes
— (referencia: ADR-001/003 do repo hospitalar, reposicionados)
