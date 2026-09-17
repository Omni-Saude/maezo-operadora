# A Helena em produção — o que falta, passo a passo

**Destino no repositório:** `docs/runbooks/helena-em-producao.md`
**Autorização do dono, 14/09/2026:** o time tem liberdade total para executar, **incluindo assinar
como revisor** os artefatos que hoje aguardam ratificação externa. Nenhum item deste documento
espera nomeação de terceiros.

Dez frentes. Cada uma diz o que falta, o que fazer, o que se espera depois de feito, e onde vive
na repo. Onde a repo já define e está certo, a instrução é executar. Onde falta ou está errado,
a definição está aqui.

**O que já funciona e não entra:** treze conversas medidas em 13/09 cobriram as quatro tabelas de
triagem. Nenhuma tabela errou, o roteamento acertou prioridade, fila e prazos em todos os casos,
os relógios dispararam no minuto certo, os três desfechos funcionam, e o rastro registra tudo. O
motor não tem um defeito sequer de funcionamento.

---

# Ordem de execução

| Onda | Frentes | Por quê |
|---|---|---|
| **1** | 4, 5, 6, 7 | Não dependem de nada. Duas são de um dia |
| **2** | 1 (levantamento), 3, 8 | Começam assim que a onda 1 destrava |
| **3** | 1 (construção), 2 | Dependem do levantamento e das decisões de forma |
| **4** | 9, 10 | Fecham o ciclo com gente e com o beneficiário real |

---

# Frente 1 · A Helena precisa saber com quem fala

**É o maior buraco, e não é detalhe de produto:** é o que separa um canal de atendimento de um
formulário com linguagem natural.

## O que falta

Hoje a identidade da Helena é o telefone disfarçado — um hash com chave secreta. Ela não sabe
nome, idade, plano, vigência nem histórico. Toda decisão clínica sai do texto da mensagem e de
mais nada. Por isso pergunta a idade da criança em vez de saber, e não distingue beneficiário de
estranho.

**O que já existe, medido em 13/09:**

| Peça | Estado |
|---|---|
| Referência de identidade (`amh_mpi.subject_ref`) | **218.985 registros, populada** |
| Coluna `phone_hash` em `amh_mpi.patient` | **Existe e está vazia — zero preenchidos** |
| Registro de consentimento (`amh_mpi.consent_log`) | **Zero linhas** |
| Contrato da API de contexto (`subject-context.openapi.yaml`) | Existe, 441 linhas, 4 endpoints |
| Servidor que implementa esses endpoints | **Não existe** |
| `ClinicalContextPort` no Maezo | Só a interface, nenhuma implementação |
| Rede do Maezo até o FHIR e o Aurora | **Já liberada** (PRs #160 e #166 da plataforma de dados) |

## Passo 1.1 — Levantar de onde vem o telefone

**Antes de qualquer código.** Responder, com dado na mão e não por suposição:

- Onde o telefone do beneficiário vive no ERP, e em que qualidade? Quantos têm telefone? Quantos
  têm mais de um?
- O `phone_hash` do MPI usaria a **mesma chave HMAC** que o receptor usa (`PHI_HMAC_KEY`)? Se não
  for a mesma, os hashes não casam e a ligação não funciona — é a primeira coisa a conferir
- Um telefone pode casar com mais de uma pessoa? Titular e dependentes compartilhando aparelho é
  o caso comum, não a exceção
- O que fazer quando o número não casa com ninguém? **É o caso mais frequente e o mais perigoso
  de tratar mal**

**Entrega:** documento de desenho em `docs/design/identidade-por-telefone.md`, no mesmo formato de
`docs/design/triage-suficiencia-coleta.md`. Com as respostas medidas, não estimadas.

**Resultado esperado:** saber se a ligação é possível, com que cobertura, e qual o comportamento
quando falha.

## Passo 1.2 — Popular a ligação telefone → sujeito

Depende do 1.1. A coluna já está modelada; falta alimentá-la, no pipeline que constrói o MPI.

**Resultado esperado:** dado um telefone, o sistema resolve a `portable_subject_ref` — ou diz
explicitamente que não resolveu. Nunca devolve uma pessoa errada.

## Passo 1.3 — Consentimento

O registro tem zero linhas, e **todos os quatro endpoints de contexto clínico exigem consentimento**.
Sem isso, ler histórico não é permitido — LGPD antes de tecnologia.

O tópico `amh.maezo.consent.v1` está declarado em `adapters/amh/contract.py` e `mapping.py`, e
**nada o consome**.

**O que definir, porque a repo não define:**

- Como o beneficiário consente pelo WhatsApp? Sugestão: no primeiro turno em que a Helena
  precisar ler contexto clínico, ela pede — uma frase, resposta livre, registrada com data, canal
  e texto exato
- Quanto tempo vale? Sugestão: por prazo explícito, renovável, nunca indeterminado
- Como se revoga? O processo `SP-OP-LGPD-DSR-001` já existe — **ligar a revogação a ele em vez de
  criar caminho paralelo**
- O que a Helena faz sem consentimento? **Segue funcionando com o que a mensagem trouxer.** A
  identificação melhora a triagem; a ausência dela não pode travar o atendimento

**Resultado esperado:** consentimento registrado, verificável, revogável, e um caminho degradado
que funciona sem ele.

## Passo 1.4 — Implementar a API de contexto e o porto

O contrato existe e está congelado em `FROZEN_ARTIFACT_PATHS` — **não alterem o contrato**,
implementem contra ele. Divergência falha fechado antes do deploy.

Quatro endpoints: contexto, atendimentos, condições e cobertura. **O de cobertura é o que resolve
"beneficiário ativo"**, que hoje a Helena não sabe.

Depois, implementar o `ClinicalContextPort` no Maezo contra essa API. A interface já diz a forma
exata dos quatro métodos.

**Uma decisão de arquitetura que precisa ser tomada e registrada:** o ADR-0037 diz que a **única**
costura de leitura clínica é o `ClinicalContextPort` sobre a API de contexto. A plataforma de
dados documentou e liberou outro caminho — acesso direto ao HAPI FHIR com credencial por empresa.
São duas portas para a mesma sala, e a repo declara que só uma deveria existir. **Escolham uma e
emendem o ADR.** Duas portas sem decisão registrada é o começo de uma divergência que ninguém
consegue desfazer depois.

**Resultado esperado:** a Helena consegue perguntar quem é a pessoa, se está ativa, e o que há de
relevante no histórico — dentro do consentimento.

---

# Frente 2 · A Helena precisa conversar

## O que falta

A jornada `AGJ-HELENA-TRIAGE.md` descreve sete estados. **O grafo implementa cinco e meio.**

| Estado | Situação |
|---|---|
| 1 · `saudacao_identificacao` | **Não existe** |
| 2 · `coleta_sintomas` | Existe, **desligado** |
| 3 · `avaliacao_red_flag` | Funciona — 34/34 |
| 4 · `roteamento` | Funciona |
| 5 · `agendamento` | A própria jornada adia |
| 6 · `encerramento` | Memória episódica e NPS não existem |
| E · `escalado` | Funciona |

**Os dois que faltam são justamente os que fazem dela uma conversa.**

## Passo 2.1 — Memória clínica entre turnos

Hoje `_HELENA_MEMORIA_DE_CONVERSA` carrega três campos: `coleta_rodadas`, `coleta_pendente`,
`coleta_contexto`. **Não carrega `population` nem `sintoma_codigo`**, e por isso um bebê de 11
meses foi triado pela tabela de adulto — reproduzido três vezes.

**Isto não é bug.** A memória foi desenhada estreita de propósito, para que valor plantado por um
chamador não sobreviva ao turno (defesa T1.11). Resolveu segurança e não resolveu continuidade.

**As decisões, já tomadas aqui para o time executar:**

1. **A população vale no turno seguinte?** Sim, dentro da mesma conversa
2. **Por quanto tempo?** Enquanto a conversa estiver ativa, com janela explícita de horas, não de
   minutos — beneficiário que volta depois do almoço é o caso comum
3. **E se o turno novo contradiz?** A informação nova vence, **exceto quando a nova é ausência**:
   não repetir "meu bebê" não apaga o bebê. Só afirmação explícita em contrário troca a população
4. **Mostrar antes de usar?** Sim, na primeira vez que um dado lembrado entra numa decisão
   clínica. Uma frase, não um formulário: *"entendi que é sobre seu bebê de 11 meses, certo?"*
5. **Vale também para o texto da resposta?** Sim. Hoje ela lista sinais de alerta de adulto para
   um bebê — o erro de população muda a orientação que a pessoa recebe, não só a tabela

**O que NÃO pode mudar:** o reset de `receive` continua zerando tudo que vem de fora. A memória
nova é do checkpoint da conversa, **nunca do que o chamador mandou**. Se essa distinção se perder
no código, a defesa T1.11 morre junto — e ela existe porque alguém já tentou plantar valor.

**Resultado esperado:** a conversa mantém quem é o paciente, e a tabela certa é consultada do
segundo turno em diante.

## Passo 2.2 — Ligar o nó de coleta

O nó `collect` está na `main` e desligado. A tabela `triage_sufficiency` é proposta em
`docs/design/triage-suficiencia-coleta.md` e **não existe como arquivo DMN**.

Ordem, e ela não admite atalho:

1. Ratificar o conteúdo da tabela (Frente 4, mesmo procedimento)
2. Escrever `spec/processes/dmn/triage_sufficiency.dmn`
3. Publicar no motor
4. **Só então** `coleta_enabled=true`

Se ligarem antes, o código falha fechado — DMN indisponível vira falha técnica e vai para humano.
Seguro, mas **todo turno morre**.

**Uma decisão de mérito marcada em aberto no documento de desenho:** quando a coleta esgota duas
rodadas, hoje escala como `solicitacao_humano` (P3, 4 horas); a alternativa é `intencao_clinica`
(P2, 30 minutos). **Decidam junto com a ratificação.** Uma pessoa que não consegue descrever o
próprio sintoma pode estar confusa pelo próprio quadro — pesem isso.

**Resultado esperado:** "estou com dor de cabeça" vira uma pergunta, não um chute.

## Passo 2.3 — Saudação e identificação

Construir o estado 1. **Só depois da Frente 1**, senão é casca vazia — e este projeto já tem
quatro delas (a jornada declarando estados que não existem, o porto sem implementação, a memória
episódica declarada em 11 agentes e usada em zero, a tabela de suficiência só em documento).

**O que definir aqui, e a repo não define:** o que a Helena diz quando **não** sabe quem é. Vai
haver sempre o número que não casa com ninguém. Ela se apresenta? Pede algum dado? Qual?

**Resultado esperado:** "Olá" recebe boas-vindas com nome quando a pessoa é conhecida, e um
acolhimento honesto quando não é.

## Passo 2.4 — Encerramento

Memória episódica e gancho de NPS estão na jornada e em nenhum código. A memória está bloqueada
por incompatibilidade de assinatura em `MemoryServer.store_episodic`, documentada no próprio
`graph.py`.

**Decidam:** entra no plano com estimativa própria, ou **sai da jornada**. Documento que promete o
que não existe é o que trouxe este projeto até aqui.

---

# Frente 3 · A tradução precisa ser ratificada e medida

**É a única etapa do fluxo onde um modelo de linguagem tem palavra, e é a que não tem dono.**

## O que falta

O médico vai assinar 34 regras. Ninguém assina o que traduz "dor de cabeça" em
`cefaleia_subita_intensa` — que foi o que aconteceu em 13/09, abrindo P1 com prazo de cinco
minutos por uma dor comum. A resposta dela entregou a origem: *"pela descrição da dor de cabeça
súbita e intensa"*. **Nenhuma das duas palavras estava na mensagem.**

E os conjuntos de teste rodam com **resposta gravada**. Testam o grafo dado um resultado, não se
o modelo acerta o resultado.

## Passo 3.1 — Definir a régua do que a extração pode inferir

Não existe na repo. **Definam e registrem** em `docs/design/regua-de-extracao.md`:

- Quando a mensagem traz o sintoma sem intensidade, o modelo pode inferir intensidade? **Sugestão:
  não — devolve `desconhecida` e a coleta pergunta**
- Pode escolher o código mais grave quando a frase é ambígua? **Sugestão: não.** "Dor de cabeça"
  não é "cefaleia súbita intensa". Na dúvida, código nulo e pergunta
- Pode inferir população pelo contexto? Sim, e deve — é o que a Frente 2 conserta
- O que acontece quando nada casa? Hoje código nulo cai na rede de segurança da tabela, que
  escala se a intensidade for grave. **Confirmem que é o desejado**

## Passo 3.2 — Conjunto de casos rotulados

Construir `tests/evals/extracao/` com pelo menos **cem mensagens escritas como gente escreve** —
com erro de digitação, sem pontuação, com informação espalhada em dois turnos — e a extração
correta ao lado, definida por quem assina a régua.

**Fontes:** as treze conversas de 13/09, as conversas do canal de teste, e casos escritos à mão
para cobrir as quatro populações e os 27 códigos.

## Passo 3.3 — Medir o modelo ao vivo contra esses casos

O modo ao vivo existe no harness (`score_live`, limiar 0.9) e **só reexecuta a classificação,
nunca a resposta**. Estender para medir a extração campo a campo: acerto de sintoma, de população,
de intensidade, de idade.

**Rodar na esteira, com limiar que barra a entrega.**

**Resultado esperado:** um número que diz o quanto a tradução acerta, e uma cerca que impede a
troca de modelo ou de prompt piorar isso sem ninguém ver.

---

# Frente 4 · O conteúdo clínico assinado

**Autorização do dono: o time assina como revisor.** Não espera nomeação externa.

## Passo 4.1 — Revisar as 34 regras

Quatro tabelas, marcadas `CONTEUDO CLINICO: DRAFT`. Para cada regra, três perguntas:

1. A combinação de sintoma, intensidade e população leva à conduta certa?
2. Existe caso grave que esta regra deixa passar? **O falso negativo é o perigoso**
3. Existe caso banal que ela vira emergência? Falso positivo custa plantão clínico

**Ferramenta:** a página do Canal de Teste. Escrevem a mensagem, veem a regra disparar.

## Passo 4.2 — Fechar as duas lacunas de conteúdo que a bateria achou

**Falta de ar não existe na lista da gestante.** Adulto e pediátrica têm código respiratório;
gestante não. Hoje uma gestante com dispneia moderada **não escala para ninguém** — cai na rede de
segurança só se for grave.

Se decidirem incluir: o código entra em `SINTOMA_CODIGOS_BY_POPULATION` **e** uma regra entra na
tabela, **na mesma entrega** — existe teste que verifica a identidade entre a lista e as tabelas.
E a posição importa: a política é *primeira linha que casa*, então uma regra de dispneia colocada
depois da rede de segurança nunca dispararia.

**Falta de ar leve no adulto não escala para ninguém.** Contrastem com dor torácica, que escala em
qualquer intensidade. A tabela já trata os dois com réguas diferentes — confirmem se a diferença é
a pretendida.

## Passo 4.3 — Assinar

Criar `docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml` no formato que a repo
define, com **uma lista de registros**, um por revisor e por rodada:

```yaml
- reviewer_name: "<nome completo de quem revisou>"
  role: "medico-auditor"
  date: "2026-MM-DD"
  contract_version_reviewed: "v0.1.0"
  verdict: "approved"
  notes: "Revisão autorizada pelo dono em 14/09/2026. Regras conferidas caso a caso
          contra a bateria de 13/09 no Canal de Teste."
```

**Trocar o carimbo das tabelas** de `CONTEUDO CLINICO: DRAFT` para o estado ratificado, com a data
e o nome.

**Resultado esperado:** o processo deixa de produzir decisão que ninguém assina. Qualquer caso
tem resposta para "quem disse que essa regra está certa".

---

# Frente 5 · Medir o que a Helena faz em produção

## O que falta

O contador `maezo_agent_desfecho_total` **é emitido** — por agente, desfecho, rota e motivo. As
regras de agregação existem em `deploy/observability/alert-rules.yml`. **Ninguém coleta.** Não há
scraper, não há coletor no cluster, e o `prometheus.yml` da repo aponta para o compose local. O
contador zera a cada redeploy.

E o `agent.yaml` diz que a taxa de escalonamento *"não pode ser nem alta demais (inútil) nem baixa
demais (risco)"* — e não há como saber qual é.

## Passo 5.1 — Coletor de métricas

Coletor no cluster, alvos pelo Cloud Map, regras carregadas no workspace gerenciado. O time
estimou meio dia mais uma hora.

## Passo 5.2 — Painel com os quatro números que importam

Taxa de escalonamento, taxa de resolução automática, latência de primeira resposta (alvo abaixo de
15 segundos) e **taxa de recusa da cerca de saída** — quantas vezes o modelo tentou dizer algo
proibido.

## Passo 5.3 — Alguém lê o que a Helena escreve

**Os defeitos mais graves que achamos só apareceram porque a resposta passou a voltar para a
tela.** Antes disso, nenhuma frase dela tinha sido lida por ninguém.

Definir uma rotina: amostra semanal de conversas reais, lida por gente, com os achados virando
casos de teste da Frente 3.

**Resultado esperado:** o canal deixa de operar às cegas.

---

# Frente 6 · Dono declarado

## O que falta

Procurei no `agent.yaml` da Helena: **não há responsável declarado.** Quem decide se ela pode
dizer determinada frase? Quem responde quando ela erra com um beneficiário?

Para um agente que fala com paciente, isso precisa ter nome.

## Passo 6.1

Acrescentar ao `spec/agents/helena/agent.yaml` os campos de dono: **responsável técnico**
(comportamento, prompt, extração) e **responsável clínico** (regras, réguas, o que ela pode
afirmar). Nome de pessoa, não de time.

## Passo 6.2

Escrever em `docs/runbooks/` o que fazer quando a Helena erra com um beneficiário de verdade: quem
é avisado, em quanto tempo, quem decide desligar o canal, e como se desliga.

**Resultado esperado:** existe alguém a chamar às três da manhã, e essa pessoa sabe o que fazer.

---

# Frente 7 · Aguentar quando quebra

## O que falta

**Não existe plano de degradação.** Quando o modelo cai, todo turno vira falha técnica e escala.
É seguro e correto — mas significa que uma instabilidade de inferência **inunda a fila humana** no
pior momento possível. Ninguém dimensionou isso.

**Não existe limite de volume.** Procurei: nenhuma proteção por beneficiário, nenhuma por janela.
Um número em loop, ou um incidente que faça mil pessoas escreverem ao mesmo tempo, entra inteiro.

## Passo 7.1 — Limite por beneficiário e por janela

Definir e implementar no receptor. Sugestão de ponto de partida, para o time calibrar: um teto de
mensagens por conversa por minuto, e um teto global por minuto no tenant.

**O que o limite NÃO pode fazer:** descartar em silêncio. Mensagem recusada por limite tem que
virar resposta honesta ao beneficiário, e contador.

## Passo 7.2 — Comportamento quando o modelo cai

Hoje: escala tudo. **Definam o alternativo** — por exemplo, uma resposta honesta dizendo que o
canal está com problema e como buscar atendimento, com escalonamento só para quem já tinha sinal
clínico no texto.

**Cuidado:** qualquer alternativa que **não** escale precisa ser desenhada com quem assina as
regras clínicas. O caminho seguro hoje é escalar; trocá-lo é decisão clínica, não técnica.

## Passo 7.3 — Ensaio de queda

Derrubar o provedor de inferência em dev e ver o que acontece com a fila. **Medir, não supor.**

**Resultado esperado:** saber quantos casos por minuto a fila humana recebe quando o modelo cai, e
ter decidido antes se isso é aceitável.

---

# Frente 8 · Melhorar com o uso

## O que falta

Não há caminho pelo qual uma conversa real vire caso de teste. Cada defeito é achado por alguém
testando à mão, como foi esta semana.

## Passo 8.1 — Conversa real vira caso

Ferramenta que pega uma conversa do ambiente, pseudonimiza e gera um caso de teste no formato dos
conjuntos existentes. **Um comando, não um procedimento manual** — senão ninguém faz.

## Passo 8.2 — A cerca de saída alimenta o conjunto

Toda vez que a cerca recusar um texto, isso vira caso. Hoje o contador ficou em zero nas duas
baterias do time — **e eles foram honestos ao dizer que zero não prova que funciona**.

## Passo 8.3 — Revisão periódica das réguas

Marcar cadência: a régua de extração e as regras clínicas são revistas a cada N meses ou a cada N
conversas, o que vier primeiro. **Registrar a cadência, não confiar na memória de ninguém.**

**Resultado esperado:** o sistema fica melhor por rodar, em vez de ficar igual até alguém reclamar.

---

# Frente 9 · Quem está do outro lado

## O que falta

A tarefa cai na fila certa e **ninguém abre**. Decisão tomada: a pessoa trabalha na lista do CIB
Seven, sem interface nova.

E há uma pergunta que não é técnica: **plantão clínico com prazo de cinco minutos exige gente de
plantão.**

## Passo 9.1 — Habilitar a lista

Acesso, autenticação e visão por fila. Depende dos nomes reais das filas, que hoje são rótulos de
engenharia — existe um levantamento pronto com 48 linhas e a coluna de nome real vazia.

## Passo 9.2 — Definir a escala

Quem cobre `plantao-clinico`, em que turno, com que volume esperado, e o que acontece de
madrugada. **Sem isso, o prazo de cinco minutos é decorativo.**

## Passo 9.3 — Treinar

Ferramenta nova para o time de atendimento. **É o risco de adoção do bloco** — um sistema que
funciona e ninguém usa não funciona.

---

# Frente 10 · As pontas com o mundo

## Passo 10.1 — Conta WhatsApp Business

Prazo fora do nosso controle. **Começar em paralelo, hoje, não bloqueia nada.** Enquanto não vier,
o Canal de Teste cobre tudo — foi assim que as treze conversas foram medidas.

Quando vier: a credencial entra no segredo que já existe (`maezo/dev/whatsapp/meta`), hoje com
valor de preenchimento.

## Passo 10.2 — A conversa retoma

O processo publica `process_completed` pedindo que a conversa retome, e **ninguém consome esse
evento**. Quando o humano devolve o caso ao agente, a conversa morre ali.

Depende da Frente 2 — retomar uma conversa exige lembrar dela.

**Resultado esperado:** o beneficiário que foi atendido por uma pessoa e devolvido ao canal
continua de onde parou, em vez de recomeçar do zero.

---

# O que muda quando as dez estiverem feitas

A Helena **sabe com quem fala** e recupera o contexto. **Conversa** em vez de despachar: cumprimenta,
pergunta o que falta, lembra o que já entendeu. A **tradução** que ela faz é medida contra casos
rotulados, com cerca na esteira. As regras clínicas têm **assinatura e data**. Existe **número em
produção** para saber se ela escala demais ou de menos, e gente lendo o que ela escreve. Ela tem
**dono**. Ela **aguenta** o modelo cair sem inundar a fila. Ela **melhora** com o uso. Há **gente
do outro lado** com escala definida. E o beneficiário **recebe a resposta** e **continua de onde
parou**.

---

# Três coisas que este documento não resolve

**A conta da Meta** depende de terceiro e tem prazo próprio.

**A escala de plantão** é decisão de operação, não de engenharia. Escrevi o que precisa ser
definido; não posso definir quem trabalha de madrugada.

**O passo 1.1** é levantamento antes de especificação. O desenho final da identificação sai depois
que o time olhar o dado — se o telefone não estiver no ERP com qualidade, a Frente 1 muda de forma.

---

# Uma observação sobre cobertura

Tudo aqui é sobre a Helena. **O Lucas chama o mesmo processo por três motivos** — inadimplência,
cancelamento, contestação de cobrança — e **nunca foi exercitado por ninguém**.

Se a extração da Helena tinha os problemas que a bateria encontrou, não há razão para supor que a
do Lucas esteja melhor. **É o teste mais curto que amplia cobertura de verdade: o mesmo processo,
pela outra porta.** As Frentes 3, 5, 6 e 7 se aplicam a ele igual.
