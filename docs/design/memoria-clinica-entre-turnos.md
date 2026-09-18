# O que a Helena deve lembrar de um turno para o outro

**Origem:** defeito 3 de `ACHADOS_CONVERSA_PEDIATRICA_13-09-2026.md` (diretor, 13/09/2026).
**Estado:** proposta. **Quatro decisões de mérito estão em aberto e estão marcadas abaixo.**

Formato de `docs/design/triage-suficiencia-coleta.md`: descreve o que construir e deixa explícito
o que ainda não foi decidido, em vez de escolher em código.

## O que aconteceu

Cinco turnos, uma mãe, uma criança de 2 anos. No turno 3 ela disse *"minha filha tem 2 anos"*. No
turno 5 a Helena avaliou **`triage_redflag_adult`** com `population=adult`, e respondeu
perguntando *"como **você** está se sentindo"* — tratando a mãe como paciente.

As regras pediátricas (letargia, recusa alimentar, petéquias com febre, dificuldade respiratória)
nunca foram consultadas. **A DMN não errou:** ela respondeu corretamente para febre, intensidade
desconhecida e população adulta. A entrada é que estava errada.

Clinicamente: criança de 2 anos, 15 dias de secreção, febre que não cede a antibiótico, resolvida
automaticamente cinco vezes.

## Por que ligar a coleta não conserta

Era a premissa que valia até aqui, e ela é falsa. Verificado no código:

```
_HELENA_MEMORIA_DE_CONVERSA = {"coleta_rodadas", "coleta_pendente", "coleta_contexto"}
```

Três campos, e nenhum é clínico. `population`, `sintoma_codigo` e os três campos de idade estão em
`_HELENA_NEUTRAL_OUTPUTS`: `receive` os zera no início de **todo** turno.

Com a coleta ligada, a Helena pergunta "me conte mais", recebe a resposta, e continua sem saber que
a paciente é uma criança. **A memória guarda que ela perguntou, não o que já descobriu.**

Há um detalhe a mais: a preservação dos três campos só acontece quando `coleta_enabled` é
verdadeiro, e o default é falso. Hoje **nenhum** campo atravessa um turno.

## O que o reset defende, e por que ele não é um descuido

`receive` zera todo campo de saída para derrotar valor plantado pelo chamador (T1.11, camada 1). A
prova viva registrada no próprio código é a sonda anti-escalonamento: um `error` mais
`next_kind="inform"` plantados numa mensagem com bandeira vermelha faziam o grafo pular a lógica
de fail-closed. O reset limpa o plantado, `classify` roda, e a trava volta a engatar.

Aplicado a este caso, o reset defende de algo pior: **um chamador que plante `population="adult"`
numa mensagem sobre uma criança faz a triagem consultar a tabela errada de propósito.** É
exatamente o defeito 3, provocado em vez de acidental.

**O desenho resolveu segurança e não resolveu continuidade clínica. Não é descuido: é um requisito
que ninguém escreveu.**

## A dificuldade central

O estado persistido no checkpoint e o estado vindo do chamador **entram pela mesma porta**. O
despacho monta só os cinco campos de entrada (`new_helena_state`), e o LangGraph mescla isso no
estado da thread — então um `population` preservado viria do checkpoint, não do chamador. Mas o
reset não distingue as duas origens, e é por isso que ele zera tudo.

Qualquer ampliação precisa responder: **como um valor que o grafo derivou no turno anterior
sobrevive, enquanto um valor que alguém plantou continua morrendo?**

## Isto é o mesmo problema do passo 4 (PR #383)

O desenho de `conversa-com-escalonamento-aberto.md` deixou uma medição pendente: o checkpoint
carrega `escalation_business_key` do turno anterior? **A resposta é: carrega, e `receive` o
apaga** — o campo é neutro e não está na memória de conversa. Foi por isso que o log do turno 2
mostrou `None`.

Ou seja, a opção B daquele desenho (ler o próprio estado persistido) e este documento pedem **a
mesma peça**: um conjunto de campos que sobrevive ao reset. Vale decidir os dois juntos, e vale
que `escalation_business_key` entre na mesma conversa sobre o que merece sobreviver.

## Decisões de dono — não resolvidas aqui

| | Pergunta | Por que não decidimos |
|---|---|---|
| 1 | A população identificada num turno vale no seguinte? | É a pergunta de fundo. Vale significa triar a criança pela tabela certa; não vale mantém o defeito. |
| 2 | Por quantos turnos, ou até quando? | Uma conversa de WhatsApp não tem fim declarado. Um limite curto perde a criança; um longo faz a Helena tratar como criança alguém que mudou de assunto. |
| 3 | O que fazer quando muda? | A mãe pode passar a falar de si mesma no meio da conversa. Vence a nova, a mais grave, ou escala por ambiguidade? |
| 4 | O dado lembrado deve ser confirmado com o beneficiário antes de decidir? | "Só confirmando: ainda é sobre sua filha de 2 anos?" é seguro e custa um turno a cada conversa. |

A jornada diz *"conversa para entender a demanda"* e para aí. Nenhum documento do repositório
responde a nenhuma das quatro.

## Ponto de partida para a proposta

Não é uma decisão, é o material sobre o qual decidir.

**Conjunto mínimo candidato:** `population`, `sintoma_codigo`, `idade_anos`, `idade_meses`,
`idade_gestacional_semanas`. `intensidade` provavelmente **não**: ela descreve a mensagem daquele
turno, não a pessoa, e lembrá-la faria "grave" de ontem decidir o caso de hoje.

**Como manter a defesa T1.11 de pé:** o valor sobrevive apenas quando o grafo o derivou, o que
sugere marcar a origem em vez de marcar o campo — por exemplo, um único campo de memória clínica
escrito só por `classify`, que `receive` preserva, e cujos campos individuais continuam sendo
zerados. Um chamador que plante `population` continua sendo ignorado, porque a leitura passa a ser
do campo de memória, não do campo de entrada.

**O que a regra vale:** decidir a tabela da DMN pela população lembrada é decisão clínica, e vai
junto com o material do médico-auditor — pela mesma razão que as 34 regras vão.

## Ordem, se as decisões vierem

1. Decisões 1 a 4.
2. Ampliar a memória com a marcação de origem, e um teste que prove que valor plantado continua
   morrendo (a sonda anti-escalonamento, com `population` no lugar de `next_kind`).
3. Só então ligar a coleta, que depende da ratificação de `triage_sufficiency`.

## O que este documento não resolve

A Helena continua sem perguntar de verdade. Lembrar a criança entre turnos evita triá-la pela
tabela errada; não faz a conversa existir. Isso é o estado 2 da jornada e depende do médico
auditor, que segue sem revisor nomeado.

E, como o próprio documento do diretor diz, este diálogo é o melhor material que existe para essa
ratificação — não como falha técnica, mas como a pergunta de **o que a Helena deveria ter
respondido** a uma mãe com uma criança de 2 anos, 15 dias de sintomas e febre que não cede a
antibiótico.
