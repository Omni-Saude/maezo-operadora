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

## As seis regras

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

### R6 · O quadro clínico e o pedido são campos diferentes

Uma mesma mensagem pode **descrever um sintoma** e **pedir outra coisa** no mesmo fôlego.
`sintoma_codigo` e `intensidade` dizem o que a pessoa **está sentindo**; `intent` diz o que ela
**está pedindo**. Os dois campos de sintoma são preenchidos **sempre** que a mensagem descrever um
sintoma, qualquer que seja o pedido — inclusive quando ela está perguntando, pedindo um atendente
ou querendo marcar consulta.

*"Tenho 45 anos e estou com dor no peito. O que eu tenho? É infarto?"* é uma pergunta clínica
**com** `sintoma_codigo=dor_toracica` e `idade_anos=45`.

**Por que isto é clínico, e não arrumação de campo:** um código nulo ali esconde uma dor torácica
da tabela de red flag. A mesma dor, dita **sem** a pergunta, era emergência — P1, plantão clínico,
cinco minutos. Com a pergunta, e só por causa dela, o caso saía P2 / enfermagem / 30 minutos, sem
tabela nenhuma consultada. Foi medido em 22/09/2026 (caso `E1`). **Perguntar não pode rebaixar o
quadro de quem perguntou** — e quem pergunta *"é infarto?"* costuma ser exatamente quem está com
medo do que sente.

> **O limite, que é o mesmo de sempre:** esta regra manda EXTRAIR, nunca RESPONDER. A Helena
> continua sem responder pergunta clínica — isso é conduta, é o gatilho de `intencao_clinica`, e
> segue indo para gente. R6 governa o que ela **entende** da frase, não o que ela **diz** de volta.

---

## O que a régua NÃO cobre, e é deliberado

**Ela não diz se um código está clinicamente certo.** Se `cefaleia_subita_intensa` deveria ou não
ser P1 é decisão das 34 regras. A régua governa a **tradução** — da frase para o código —, não a
**conduta** — do código para a prioridade. São dois donos e duas assinaturas.

**Ela não é um prompt.** O prompt (`classify-v5`) é como se pede; a régua é o que se cobra. A lição
de 12 e 13/09 é que os dois não são a mesma coisa: proibir no prompt não segura, e foi por isso que
a cerca de saída existe. Aqui a cobrança é o conjunto de casos rotulados (3.2) e a medição ao vivo
(3.3).

---

## Como isto é cobrado

| Peça | O que faz | Onde |
|---|---|---|
| Corpus rotulado | ≥100 mensagens escritas como gente escreve, com a extração correta ao lado | `tests/evals/extracao/casos.json` (`extracao-v2`, 128 casos) |
| Cerca do corpus | o corpus não pode apodrecer: vocabulário fechado, cobertura das 4 populações e dos 25 códigos, toda regra com caso, justificativa obrigatória | `tests/unit/evals/test_corpus_de_extracao.py` |
| Medição ao vivo | roda o `classify` real contra o corpus e pontua **campo a campo** | `tests/evals/test_extracao_live.py` |

**A versão do corpus fica no próprio arquivo, e é cobrada aqui.** O campo `versao` existia
desde 15/09 e nenhum teste o lia — um rótulo decorativo. Ele é a única coisa que distingue duas
medições: *"a extração está em 0,89"* só quer dizer algo ao lado do conjunto de casos contra o qual
foi medida. A cerca do corpus agora exige que o número apareça nesta página, então um corpus novo
custa uma linha de documento — e duas rodadas contra corpora diferentes param de parecer
comparáveis.

**21/09/2026 — `extracao-v2`, os pares de qualificador.** O defeito de 13/09 voltou: *"estou com
dor de cabeça"* saiu `cefaleia_subita_intensa` outra vez (caso `C1` da bateria), e a resposta ainda
citou a pessoa entre aspas dizendo *'a pior da vida'*. O corpus ganhou dez casos — o par medido
`C1`/`C4` e mais três pares mínimo/máximo (sangramento × sangramento ativo, falta de ar leve ×
dispneia grave, contração isolada × contrações regulares), além dos negativos que faltavam para
`cefaleia_alteracao_visual` e `movimentos_fetais_reduzidos`. O lado negativo agora se declara no
próprio caso (`"nao_pode_casar": ["<código>"]`), e a cerca cobra **os dois lados** para todo código
que carrega qualificador no nome: só o negativo ensinaria a extração a nunca produzir o código, o
que trocaria este falso positivo por um falso negativo numa emergência real.

O prompt (`classify-v4` na época; hoje `classify-v5`) passou a cobrar, **por código**, qual
qualificador a mensagem precisa ter dito — a lista mora em `prompts.py::QUALIFICADORES_OBRIGATORIOS` e o texto do prompt é gerado dela,
pelo mesmo motivo que a allow-list de códigos já era. **Isto não é ratificação:** o status desta
página continua o de 15/09, e a primeira revisão continua sendo também a primeira assinatura.

**22/09/2026 — `classify-v5`, e a régua passa a ter seis regras.** A bateria mediu o caso `E1`:
*"tenho 45 anos e estou com dor no peito. o que eu tenho? é infarto?"* saía **sem código de
sintoma**, porque a extração tratava a pergunta como se ela apagasse o quadro. A régua ganhou a
**R6** e o prompt passou a `classify-v5`. O caso vive no corpus como
`e1-pergunta-sobre-dor-toracica`, com a extração correta escrita ao lado — é ele que cobra a R6 na
medição ao vivo.

> **Uma honestidade sobre a cerca, para quem assina não ser surpreendido:** a cerca do corpus ainda
> conhece **cinco** regras e exige três casos para cada uma. O caso `E1` está rotulado com as
> regras antigas que ele também exercita, então **a R6 é medida por aquele caso, mas ainda não é
> cobrada por nome**. Fechar isso é rotular três casos e estender a cerca — trabalho de engenharia,
> não decisão clínica, e não altera nenhuma regra desta página.

**Por que campo a campo, e não "acertou o caso":** um caso em que o modelo acerta o sintoma e erra a
população conta como erro em produção (tabela errada) e contaria como erro binário aqui — mas a
medida agregada esconderia QUAL campo está ruim. A régua tem seis regras; a medida tem de dizer
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

**Fato, implementado e cercado hoje:** as seis regras estão no prompt e no validador; o corpus
existe com sua cerca; a medição ao vivo roda.

**Proposta, esperando assinatura clínica:**

1. o **rótulo** de cada caso ambíguo do corpus — escrevi a extração que a régua manda, e onde a
   régua não decide sozinha, marquei o caso;
2. o **limiar** que barra a entrega. Comecei em `0.90` por campo, herdando o limiar que o harness já
   usa para classificação, e **esse número precisa ser calibrado com medição real** antes de virar
   promessa. Um limiar escolhido no escritório é uma cerca que reprova o certo ou aprova o errado;
3. a **regra R5**, que o documento pede para confirmar: deixo-a como está e registro o buraco da
   rede de segurança, porque fechá-lo é mexer nas 34 regras, não na extração.
