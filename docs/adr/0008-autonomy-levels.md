# ADR-0008: Niveis de autonomia L0-L3 por acao

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Governanca
**Amended by ADR-0034:** a clausula "avaliada pelo PEP a cada tool call" (§Decisao) descreve o modelo
intencao/donor; no v2 a avaliacao de autonomia e enforce-ada ESTRUTURALMENTE via BPMN (ADR-0018) e o
PEP e um objeto de validacao/readiness de politica, nao um gate por-call — logo a "revisao por
amostragem" L2 esta intencionalmente fora do v2 (ADR-0034, gap #24). ADR-0034 amends, nao supersede.

## Contexto
"Quanto o agente faz sozinho" deve ser politica explicita, versionada, auditavel e diferente por
tenant (modo Copilot vs Full-stack) — nao decisao difusa de prompt.

## Decisao
Matriz **acao x nivel** em YAML versionado (`src/maezo/policies/autonomy/`), artefato federado
L0-L3 (ADR-0004), avaliada pelo PEP a cada tool call:
- **L0** somente humano (agente prepara dossie): negativa de autorizacao, acusacao de fraude,
  cancelamento de contrato, QUALQUER decisao clinica (hard, nao rebaixavel por tenant — CI rejeita).
- **L1** agente propoe, humano aprova: pagamento de alcada, descredenciamento, resposta NIP, envio ANS.
- **L2** agente executa, revisao por amostragem: aprovacao de auth com DMN favoravel sob teto, glosa padrao.
- **L3** autonomo com telemetria: triagem, agendamento, respostas informativas, lembretes.

O "dial" Copilot -> Full-stack e diff de YAML revisavel — nunca mudanca de codigo.

## Consequencias
**Positivas:** ANS-defensible; evolucao de autonomia auditavel.
**Negativas (aceitas):** manutencao da matriz exige ownership (compliance humano + Gustavo).

## Supersedes
—
