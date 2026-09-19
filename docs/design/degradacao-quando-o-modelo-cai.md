# O que a Helena faz quando o modelo cai

**Origem:** Frente 7.2 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md` (14/09/2026).
**Estado:** proposta. **A decisão de mérito é de quem assina as regras clínicas, e está marcada.**

O passo 7.1 (teto de volume) já entrou. Este documento trata da outra metade: o que acontece
quando a inferência fica indisponível.

## O comportamento de hoje, medido no código

Sem modelo, `_classify_llm` falha, e o grafo faz o que foi desenhado para fazer: **escala tudo**.
Cada mensagem vira `falha_tecnica` e abre SP-OP-ESCALATION-001.

Está certo, e a razão está escrita no próprio grafo: uma falha de classificação **não pode** ser
lida como "sem bandeira vermelha". Fail-closed é a única postura defensável quando a alternativa é
deixar de escalar uma emergência.

**O que ninguém dimensionou é o volume.** Com o modelo fora do ar, a fila humana recebe uma tarefa
por mensagem — e continua recebendo enquanto durar a indisponibilidade. Não há número medido para
isso.

## O que o teto de volume já mudou

O 7.1 põe um piso na conta: com 120 mensagens por minuto no tenant, a fila recebe no máximo 120
tarefas por minuto, não o que o mundo mandar. **O teto transforma "ilimitado" em "conhecido", e
isso já é a maior parte do risco.**

Mas 120 por minuto ainda é muito para uma fila de plantão clínico, e o teto não distingue quem
tinha sinal clínico de quem escreveu "bom dia".

## A proposta

Quando a inferência estiver indisponível, **separar dois casos** em vez de escalar os dois:

| Caso | Hoje | Proposta |
|---|---|---|
| A mensagem tem sinal clínico reconhecível sem o modelo | escala | **continua escalando** |
| A mensagem não tem | escala | resposta honesta dizendo que o canal está com problema e como buscar atendimento |

**O ponto difícil, e é ele que precisa de assinatura clínica:** "sinal clínico reconhecível sem o
modelo" tem de ser decidido por alguma regra determinística — e qualquer regra determinística sobre
texto livre erra nos dois sentidos. Uma lista de palavras erra por falta (quem escreve "tá muito
ruim aqui no peito" não casa "dor torácica") e por excesso (quem escreve "meu pai teve infarto, o
plano cobre check-up?" casa e escala à toa).

**Errar por falta aqui é deixar de escalar uma emergência durante uma queda de modelo.** É
exatamente o cenário em que a margem é menor.

## Decisões de dono clínico — não resolvidas aqui

1. **Vale a pena separar?** A alternativa honesta é não separar: manter o escalonamento universal
   e aceitar o volume, agora com teto. Menos código, nenhum falso negativo novo, e a fila recebe
   no máximo o teto.
2. **Se vale, qual a régua?** Uma lista de termos de alarme, revisada e versionada como as 34
   regras — não uma heurística escrita por engenharia.
3. **A régua vale para quem já tinha caso aberto?** Uma pessoa com escalonamento em andamento que
   escreve durante a queda talvez não deva receber "o canal está com problema".

## Recomendação

**Não implementar a separação agora.** O 7.1 já fechou a parte dimensionável do risco, e a
separação introduz um falso negativo novo exatamente no pior momento. O caminho seguro hoje é
escalar tudo; trocá-lo é decisão clínica com custo assimétrico, e o ganho é de fila, não de
segurança.

O que vale fazer sem decisão nenhuma é o **passo 7.3**: derrubar o provedor em dev e medir quantas
tarefas por minuto a fila recebe. **Medir, não supor** — e é esse número que torna a pergunta 1
respondível.

## O ensaio (7.3), quando houver ambiente

Derrubar a inferência em dev é reversível e não exige código novo: basta apontar a Helena para um
provedor que recusa (`helena_zona_phi=false` devolve o `bedrock` comum, e o grafo dela marca toda
chamada como PHI, então todo turno cai em `falha_tecnica` — é o mesmo caminho da indisponibilidade,
provocado de propósito).

Medir, durante uma janela curta:

- tarefas criadas por minuto em `plantao-clinico` e nas demais filas;
- `maezo_agent_desfecho_total{desfecho="escalado_humano",motivo_categoria="falha_tecnica"}`;
- `maezo_webhook_mensagem_limitada_total`, para ver se o teto chega a atuar.

**Sem o coletor da Frente 5, esses números só existem enquanto a task viver.** Ensaiar antes do
coletor é possível lendo o endpoint direto, mas o registro fica melhor depois.
