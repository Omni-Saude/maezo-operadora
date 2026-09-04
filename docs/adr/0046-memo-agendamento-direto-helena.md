# ADR-0046: MEMO ao dono — agendamento direto (HEL-11). Sem recomendacao de engenharia

**Status:** **Proposed — MEMO. Decisao PENDENTE DO DONO. SEM RECOMENDACAO DE ENGENHARIA.**
Redigido por AGENTE (`adr-drafter`, R1) no programa de remediacao do Agent Fleet Audit; um agente nao
ratifica ADR. `/docs/adr/` e dono-gated (`.github/CODEOWNERS:61`). · **Data:** 2026-09-04
· **Area:** Produto / Autonomia / Orquestracao

> **Este documento NAO e uma decisao, e nao propoe uma.** Ele existe porque a proposta ADR-P-001 do
> relatorio de auditoria, tomada ao pe da letra, colide com a matriz de autonomia viva de uma forma
> que o proprio relatorio nao registra. Um agente nao tem autoridade para resolver essa colisao —
> ela e de produto e de politica de autonomia. O que segue e a pergunta, com os fatos verificados
> necessarios para responde-la, e nada alem disso.

---

## O achado (HEL-11, P1, dimensao 10)

`spec/agents/helena/agent.yaml:12-16` registra `agendamento_direto` como `out_of_scope`, com
disposicao «recusa honesta + handoff humano real». O caminho e real:
`src/maezo/agents/helena/graph.py::HelenaGraph.schedule` roteia por
`::HelenaGraph._start_escalation` para SP-OP-ESCALATION-001 com
`motivo_categoria="solicitacao_humano"` (GAP 9.2 fechado). Helena nao devolve um beco sem saida.

A auditoria observa que agendamento e o trabalho numero 1 do beneficiario (D9/D11) e propoe, para a
Fase 1, converter o handoff em jornada de agendamento real.

## A colisao que o relatorio nao registra

O relatorio propoe «agendamento passivel de automacao **L2** com DMN de elegibilidade». Mas a matriz
de autonomia viva ja classifica a acao em **L3**:

```
grep -n 'scheduling' spec/policies/autonomy/L0-core.yaml
31:  scheduling:                   { level: L3 }
```

L3 e mais autonomo que L2. **A proposta da auditoria, aplicada literalmente, e um REBAIXAMENTO de
autonomia**, nao uma habilitacao. Nao ha, na matriz, nada que impeca Helena de agendar hoje: o que
falta e a capacidade construida (contrato, BPMN, DMN, worker, wire FHIR), nao a permissao.

Essa distincao muda a natureza da pergunta ao dono. Ela nao e «podemos automatizar agendamento?» — a
matriz ja respondeu que sim, em L3. Ela e: **a politica L3 vigente para `scheduling` esta certa, dado
que agora existiria efeito colateral real (slot de agenda) em vez de um handoff?**

## O que a construcao exigiria (fatos, nao recomendacao)

Independentemente da resposta sobre o nivel, a capacidade exige tres itens, e os tres tem gate:

1. **Contrato novo `SP-OP-AGENDAMENTO-001`** em `docs/processes/contracts/`, com BPMN e DMN
   correspondentes — spec-first (AGENTS.md regra dura 1). Uma DMN de elegibilidade
   (`triage_elegibilidade_agendamento`) e o que manteria a decisao fora do prompt (ADR-0012, regra
   dura 5).
2. **Chave nova em `KNOWN_PROCESS_KEYS`** (`src/maezo/tools/process_allowlist.py:21-42`, hoje 15
   chaves). O arquivo e **dono-gated**: `.github/CODEOWNERS:65` o atribui a `@rodaquino-OMNI` **e**
   `@Omni-Saude/security-team`. Sem essa chave, o effect-PEP nega o start — o processo nao pode
   nascer.
3. **ADR proprio**, porque a capacidade abre um efeito colateral externo novo (reserva de slot de
   agenda) que nenhuma decisao aceita cobre hoje.

E, se o nivel de autonomia for revisitado, `spec/policies/autonomy/L0-core.yaml` e dono-gated com
security-team (`.github/CODEOWNERS:84`).

## Invariantes que a resposta precisa preservar

- **D2 (particao determinismo/LLM):** a elegibilidade tem de ser DMN, nunca prompt (regra dura 5,
  `AGENTS.md:31`).
- **Fail-closed:** DMN indisponivel, cobertura nao verificavel ou elegibilidade ambigua tem de cair no
  handoff humano atual — nunca agendar por default.
- **Regra dura 7 (`AGENTS.md:33`):** agendamento nao pode se tornar um caminho lateral que toque
  qualquer item `hard` (`spec/policies/autonomy/L0-core.yaml:6-13`) — em particular, elegibilidade de
  agendamento nao pode virar, na pratica, uma decisao de cobertura (`authorization_denial` e L0
  `hard: true`). A fronteira entre «ha slot e o beneficiario esta apto a marcar» e «este procedimento
  esta coberto» precisa ser explicita na DMN.

## A pergunta ao dono

1. `scheduling` deve permanecer **L3** na matriz viva, ser **rebaixada a L2** (como a auditoria
   propoe, aparentemente sem notar que e rebaixamento), ou outro nivel?
2. Agendamento direto entra no escopo de Fase 1, ou o handoff humano atual permanece como disposicao
   registrada?
3. Se entrar: autoriza a criacao de `SP-OP-AGENDAMENTO-001` e a inclusao da chave em
   `KNOWN_PROCESS_KEYS` (arquivo dono-gated + security-team)?

**Nenhuma das tres tem recomendacao de engenharia neste documento, por decisao deliberada:** a
primeira e politica de autonomia, a segunda e roadmap de produto, e a terceira depende das duas.

## Criterio de aceite deste MEMO

Este documento esta cumprido quando o dono registrar a resposta as tres perguntas — em emenda a este
ADR ou em ADR proprio. **Nenhuma linha de codigo, spec ou politica deve ser escrita a partir deste
documento antes disso.**

## Supersedes

—. Nao emenda nem supersede nada. Levanta uma questao sobre `spec/policies/autonomy/L0-core.yaml:31`
e sobre o escopo de Fase 1 de Helena; relacionado a `ADR-0008` (niveis de autonomia), `ADR-0012`
(DMN como ferramenta deterministica unica) e `ADR-0016` (allowlist de process_key).
