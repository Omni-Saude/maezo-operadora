# ADR-0008: Niveis de autonomia L0-L3 por acao

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Governanca

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
