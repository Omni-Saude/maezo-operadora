# A régua de extração — o que a Helena pode inferir de uma mensagem

**Status: PROPOSTA DE ENGENHARIA, 15/09/2026. Passo 3.1 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`.**
As regras abaixo estão implementadas e cercadas por teste, mas **não estão ratificadas** — quem
assina a régua é quem assina as 34 regras clínicas, e nenhum agente assina no lugar dele. Até lá,
o que existe é uma régua *aplicada e mensurável*, que é melhor que uma régua inexistente e pior que
uma assinada.

## Por que esta página existe

É a **única etapa do fluxo onde um modelo de linguagem tem palavra**, e era a que não tinha dono.

O médico vai assinar 34 regras sobre `sintoma_codigo`. Ninguém assinava o que transforma *"dor de
cabeça"* em `cefaleia_subita_intensa` — e foi exatamente isso que aconteceu em 13/09/2026, abrindo
um P1 com prazo de cinco minutos por uma dor comum. A própria resposta entregou a origem:

> *"pela descrição da dor de cabeça súbita e intensa"*

**Nenhuma das duas palavras estava na mensagem.** A tabela não errou: ela recebeu
`cefaleia_subita_intensa` e fez o que a regra manda. O erro aconteceu uma etapa antes, num lugar que
não tinha régua, não tinha dono e não tinha medida.

---

## As cinco regras

### R1 · Intensidade não se infere

Quando a mensagem traz o sintoma sem dizer a intensidade, a extração devolve **`desconhecida`**.

Não é conservadorismo: é que `intensidade` entra na DMN como dado, e um dado inventado produz uma
decisão com aparência de fundamentada. *"Estou com dor de cabeça"* não diz se a dor é leve ou
insuportável, e as duas levam a condutas diferentes.

**O que fazer com o desconhecido é perguntar** — é para isso que a coleta (Frente 2.2) existe. A
régua e a coleta são a mesma decisão vista de dois lados.

> **Atenção ao que NÃO é inferência:** *"dor muito forte"*, *"não aguento de dor"*, *"insuportável"*
> são a pessoa **dizendo** a intensidade com outras palavras. Traduzir isso para `grave` é leitura,
> não invenção. A régua proíbe supor o que não foi dito, nunca entender o que foi dito de outro
> jeito.

### R2 · Na dúvida, código nulo — nunca o código mais grave

Quando a frase é ambígua entre dois códigos, a extração devolve **`null`**.

É a regra que o defeito de 13/09 viola. `cefaleia_subita_intensa` é um código sobre uma dor de
cabeça **súbita** (que começou de repente, "a pior da minha vida") **e intensa**. *"Dor de cabeça"*,
sozinho, não é esse código — e escolher o mais grave "por segurança" não é segurança: é abrir um P1
de cinco minutos que consome plantão clínico e treina a operação a ignorar P1.

**A escolha do código mais grave parece a opção segura e não é.** O custo de um falso positivo
aqui não é computacional: é um plantonista atendendo uma dor comum enquanto outra pessoa espera.

### R3 · População se infere, e deve

Ao contrário das duas anteriores, aqui a inferência é **obrigatória**. *"Meu filho está com febre"*
é `pediatric` mesmo sem a idade; *"estou grávida de 30 semanas"* é `gestante`.

A população escolhe a **tabela**, e a tabela errada erra todas as regras de uma vez — foi o bebê de
11 meses triado como adulto. A Frente 2.1 estende essa inferência para além do turno: uma população
dita num turno vale nos seguintes, dentro de uma janela.

**O limite:** inferir população não é inferir idade. *"Meu filho"* dá `pediatric`; não dá
`idade_meses`. Um filho pode ter 40 anos.

### R4 · Idade só quando dita, e na unidade em que foi dita

`idade_anos`, `idade_meses` e `idade_gestacional_semanas` são preenchidos **apenas** quando a
mensagem os traz. *"Bebê"* não é `idade_meses=6`. *"Criança pequena"* não é `idade_anos=3`.

Isto importa mais do que parece porque quatro das cinco regras P1 da tabela de gestante, e boa parte
da pediátrica, **dependem da idade** (achado da revisão das 34 regras, Frente 4). Uma idade
inventada não escala um caso que deveria escalar: ela faz a regra casar com o valor errado.

**Converter unidade é leitura, não invenção:** *"um ano e meio"* → `idade_meses=18` é entender o que
foi dito. *"Bebezinho"* → `idade_meses=3` é adivinhar.

### R5 · Quando nada casa, código nulo — e a rede de segurança tem um buraco conhecido

Nenhum dos 25 códigos cobre tudo que uma pessoa pode escrever. Quando nada casa, `sintoma_codigo`
é `null` e o `intent` continua `symptom` — a mensagem É sobre um sintoma, só não é sobre um que a
tabela conhece.

Hoje isso cai na rede de segurança das quatro tabelas, que escala **se a intensidade for `grave`**.

> **A régua confirma o comportamento e registra o buraco, que não é dela para fechar.** A revisão
> das 34 regras (Frente 4) mediu: a rede só pega `grave`, então **todo sintoma `moderada` sem regra
> específica sai sem bandeira**. O pior caso levantado é reação alérgica moderada, que progride. Com
> R1 devolvendo `desconhecida` para quem não disse a intensidade, o caminho honesto é a coleta
> perguntar — e é mais uma razão pela qual a Frente 2.2 não é opcional.

---

## O que a régua NÃO cobre, e é deliberado

**Ela não diz se um código está clinicamente certo.** Se `cefaleia_subita_intensa` deveria ou não
ser P1 é decisão das 34 regras. A régua governa a **tradução** — da frase para o código —, não a
**conduta** — do código para a prioridade. São dois donos e duas assinaturas.

**Ela não é um prompt.** O prompt (`classify-v3`) é como se pede; a régua é o que se cobra. A lição
de 12 e 13/09 é que os dois não são a mesma coisa: proibir no prompt não segura, e foi por isso que
a cerca de saída existe. Aqui a cobrança é o conjunto de casos rotulados (3.2) e a medição ao vivo
(3.3).

---

## Como isto é cobrado

| Peça | O que faz | Onde |
|---|---|---|
| Corpus rotulado | ≥100 mensagens escritas como gente escreve, com a extração correta ao lado | `tests/evals/extracao/casos.json` |
| Cerca do corpus | o corpus não pode apodrecer: vocabulário fechado, cobertura das 4 populações e dos 25 códigos, toda regra com caso, justificativa obrigatória | `tests/unit/evals/test_corpus_de_extracao.py` |
| Medição ao vivo | roda o `classify` real contra o corpus e pontua **campo a campo** | `tests/evals/test_extracao_live.py` |

**Por que campo a campo, e não "acertou o caso":** um caso em que o modelo acerta o sintoma e erra a
população conta como erro em produção (tabela errada) e contaria como erro binário aqui — mas a
medida agregada esconderia QUAL campo está ruim. A régua tem cinco regras; a medida tem de dizer
qual delas está sendo violada.

**Os pesos não são iguais**, e a diferença é clínica, não estatística:

| Campo | Peso | Por quê |
|---|---|---|
| `population` | **alto** | erra a tabela inteira de uma vez |
| `sintoma_codigo` | **alto** | é o defeito de 13/09 |
| `intensidade` | médio | entra na DMN, mas a rede de segurança ainda pega o `grave` |
| idades | médio | decide regras específicas, e a ausência é tratável pela coleta |
| `intent` | alto | decide se a DMN roda |
| `psychosocial_risk` | **alto** | é o gatilho 5, avaliado em **qualquer** mensagem e sempre escalando; um falso negativo aqui é uma ideação que ninguém leu |

---

## O que é proposta e o que já é fato

**Fato, implementado e cercado hoje:** as cinco regras estão no prompt e no validador; o corpus
existe com sua cerca; a medição ao vivo roda.

**Proposta, esperando assinatura clínica:**

1. o **rótulo** de cada caso ambíguo do corpus — escrevi a extração que a régua manda, e onde a
   régua não decide sozinha, marquei o caso;
2. o **limiar** que barra a entrega. Comecei em `0.90` por campo, herdando o limiar que o harness já
   usa para classificação, e **esse número precisa ser calibrado com medição real** antes de virar
   promessa. Um limiar escolhido no escritório é uma cerca que reprova o certo ou aprova o errado;
3. a **regra R5**, que o documento pede para confirmar: deixo-a como está e registro o buraco da
   rede de segurança, porque fechá-lo é mexer nas 34 regras, não na extração.
