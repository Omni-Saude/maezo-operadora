# ADR-0012: DMN como ferramenta deterministica unica

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Regras de negocio

## Contexto
O repo hospitalar provou que ~80% das regras vivem bem em DMN (federada, versionada, testavel).
No paradigma agents-first, quem consome a regra e primariamente o agente.

## Decisao
- DMN e a **fonte unica de regra de negocio deterministica**. Duas vias de consumo, mesma tabela:
  `mcp-dmn` (agentes) e service tasks (processos SP-OP).
- LLM **nunca** decide o que uma regra deterministica pode decidir; o agente raciocina sobre o
  RESULTADO da DMN (explica, pendencia, recomenda) — nao a substitui.
- Conteudo payer-side: DUT/ROL/carencia (novo) + porte re-semantizado do conteudo hospitalar
  (DOMED, elegibilidade, glosa) com SME.
- Pipeline `contract_extraction` (portado) gera DMN a partir de contratos do tenant.

## Consequencias
**Positivas:** regras auditaveis fora do LLM; simetria agente/processo; menos alucinacao em decisao.
**Negativas (aceitas):** porte de conteudo e esforco real de SME (semanas).

## Supersedes
—

## Complemento (2026-10-25): trava de entrada da Helena so' com frase afirmada clara

**Decisao do dono (25/10/2026), depois do teste dele na Helena.** A decisao de urgencia continua
deterministica (este ADR): o modelo nunca decide sozinho um 192. O que muda e' **sobre qual texto** a regra
fixa decide.

- A trava de entrada (lexico de emergencia, alarme deterministico, vocabulario da tabela de alarme, termos de
  alarme da base de saude) le' **so' as frases afirmadas** da mensagem: o relato no presente (ou recente), da
  propria pessoa ou de quem ela fala por ("estou com dor no peito", "meu pai ta com falta de ar", "ela
  desmaiou").
- **Nao contam como sinal relatado**: o sinal negado (inclusive com a negacao antes: "nao tenho dor no
  peito"); o sinal dentro de PERGUNTA ("o que dor no peito tem a ver?", "falta de ar pode ser isso?"); o
  "nao, mas ..." que so' repete os sinais; e o ECO da ultima pergunta da Helena (a pessoa copiando a lista de
  sinais que a Helena perguntou).
- Do lado seguro: pergunta que pede o que fazer ("estou com falta de ar, o que eu faco?") e qualquer marca
  de afirmacao mantem a frase; "sim" mantem a mensagem inteira; risco psicossocial (CVV 188) nao passa por
  este filtro.
- O codigo de sintoma que o classificador tira so' de sinais nao afirmados, ou do HISTORICO depois de o
  alerta ja' ter sido respondido (sem sintoma nem sinal na mensagem do turno), nao vai a tabela de alarme.
- A urgencia e' decidida por regra fixa sobre (a) essas frases afirmadas e (b) os sinais que o modelo
  extraiu (`alarm_signs_reported` com confianca `clear`), validados contra o vocabulario da tabela. O modelo
  so' acrescenta urgencia; nunca a retira.

**Por que.** No teste do dono, "nao, mas o que dor no peito ... tem a ver" (resposta a pergunta de alerta)
abriu P1 e mandou ligar 192: a trava lexica achou os sinais que a propria Helena tinha listado. Na
avaliacao r5b, "posso tomar dipirona?" depois do "nao" ao alerta da dor de cabeca recebeu 192: o codigo do
historico foi a tabela.

**Consequencia aceita.** Uma pergunta sobre um sinal, sem nenhuma marca de que a pessoa o sente, nao da' 192
na hora pelo texto: ela segue o fluxo (a pergunta de alerta, a resposta de saude ou a equipe) e o modelo
ainda pode acrescentar a urgencia. Alarme real (afirmado, de terceiro, com erro de digitacao) continua 192
na hora. Conteudo clinico em rascunho, revisao medica pendente.
