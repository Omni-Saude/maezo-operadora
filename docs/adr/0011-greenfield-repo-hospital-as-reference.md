# ADR-0011: Repo greenfield; hospitalar como referencia; licoes estruturais

**Status:** Proposed · **Data:** 2026-06-12 · **Area:** Produto/Engenharia

## Contexto
`Omni-Saude/Maezo` e hospital-first, BPM-first, e carrega dividas estruturais (arvore duplicada
com drift, camadas de arquivo morto, acoplamento Tasy/Oracle, testes que mockavam o engine).

## Decisao
Este repo nasce greenfield. O hospitalar e **fonte de referencia** (colher ideias, padroes e
conteudo — nunca importar codigo):
- Colher: contract_extraction (porte), conteudo DMN payer-relevante (porte com SME), clients de
  integracao TISS/ANS/WhatsApp/FHIR (copia adaptada), padroes multi_tenant/webhooks, CI de
  validacao BPMN, federacao DMN.
- Licoes obrigatorias aqui: arvore unica `src/`; sem `.archive` no main; validacao de artefatos
  como blocker de CI desde o commit 1; integracao testada contra engine real; deps de agentes
  no pyproject desde o inicio; CDC desenhado para o core da operadora (nao Tasy).

## Consequencias
**Positivas:** zero heranca de premissas BPM-first; codebase do tamanho do problema.
**Negativas (aceitas):** custo de porte; duas bases durante transicao; anti-drift via pattern docs.

## Supersedes
— (substitui, para o produto operadora, o ADR-009 hospitalar de mono-repo)
