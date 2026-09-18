# Três decisões e um bloqueio estrutural — Frentes 2.4, 9.1 e 10.2

**Status: MEDIÇÃO + PROPOSTA DE DECISÃO, 15/09/2026.** Fecha os três itens de
`HELENA_EM_PRODUCAO_O_QUE_FALTA.md` que não eram implementáveis sem uma decisão — e um que não é
implementável **de jeito nenhum** hoje, por uma razão que ninguém tinha medido.

---

# 10.2 · A conversa retoma — e por que ela não pode retomar

**Este é o achado, e ele não estava no documento.**

O passo 10.2 diz: *"o processo publica `process_completed` pedindo que a conversa retome, e ninguém
consome esse evento. Quando o humano devolve o caso ao agente, a conversa morre ali."*

Isso está certo, e é metade do problema. A outra metade é que **construir o consumidor não faria a
conversa retomar**.

## O que foi medido

O evento existe e carrega o necessário:

```
SP-OP-ESCALATION-001, ST_PublishProcessCompleted
  event_topic: agents.events.process_completed
  payload:     tenant_id, agent_id, conversation_id, resultado
```

O `conversation_id` é `wa:{tenant}:hk1_{hmac}`. E o `hk1_` é o problema: é um **HMAC-SHA256 com
chave**, e portanto **irreversível**. Não existe caminho de volta ao número de telefone.

A pergunta seguinte é onde o número bruto estaria guardado. A resposta está escrita no próprio
código, como propriedade deliberada:

> `_ScopedWhatsAppSender` — *"closes over the RAW recipient number for exactly one inbound turn —
> never stored on `HelenaState`, never persisted past this call."*

**Então a plataforma não consegue enviar mensagem a uma conversa que não esteja respondendo a uma
mensagem de entrada.** Não por falta de código: por uma decisão de privacidade (ADR-0006, Zona
Geral) que está funcionando exatamente como projetada.

## O que isso significa para a frente

Um consumidor de `process_completed` que tentasse mandar *"seu caso voltou para o canal"* teria de
**persistir o telefone bruto** em algum lugar indexado por `conversation_id`. Isso não é um detalhe
de implementação — é reverter a propriedade que faz o `conversation_id` poder ser `thread_id` de
checkpoint e chave de negócio visível no Cockpit sem virar PHI.

**Recomendo não fazer isso**, e a razão é proporcional: o ganho é uma mensagem de cortesia; o custo
é passar a guardar o telefone de todo beneficiário que já escreveu, para sempre, num banco que hoje
não o tem.

## A metade que é implementável, e ela entrega o resultado esperado

O resultado que o documento pede é *"o beneficiário que foi atendido por uma pessoa e devolvido ao
canal **continua de onde parou**, em vez de recomeçar do zero"*.

Isso **não exige** mensagem de saída. Exige que, quando a pessoa escrever de novo, a conversa saiba
o que aconteceu. E isso é alcançável sem guardar telefone nenhum:

1. um consumidor de `process_completed` registra, **indexado pelo `conversation_id`**, que um humano
   atendeu e devolveu o caso — nenhum dado novo, só o fato;
2. o turno seguinte lê esse registro e a Helena **reconhece** o atendimento em vez de começar fria;
3. a **memória clínica** (Frente 2.1, PR #404) faz população e idades sobreviverem, então "de onde
   parou" é literalmente verdade.

**O `resultado` do processo importa aqui**, e `devolvido_agente` é o único que reabre o canal:
`resolvido_humano` e `emergencia_acionada` terminaram noutro lugar, e tratá-los como retomada faria
a Helena convidar a continuar uma conversa que acabou.

> **Uma restrição de privacidade que vale registrar antes de alguém tentar contorná-la:** as
> `notas_resolucao` do humano **não estão** no payload, de propósito — o fato nunca carrega PHI. A
> Helena, portanto, não pode dizer o que o humano decidiu. Só pode dizer que o caso voltou. Qualquer
> texto que sugira conhecimento do desfecho seria invenção, e cairia na cerca de saída.

**Por que não implementei agora:** depende da Frente 2.1 estar mergeada (é o mesmo mecanismo de
memória), e o consumidor precisa de um broker — o `agents.events.*` vai para o Kafka do cluster, e
qualquer prova de ponta a ponta exige um apply em dev, que está pausado. O desenho acima é
executável no dia em que as duas condições existirem.

---

# 2.4 · Encerramento — a decisão que o documento pede

> *"Memória episódica e gancho de NPS estão na jornada e em nenhum código. **Decidam:** entra no
> plano com estimativa própria, ou **sai da jornada**. Documento que promete o que não existe é o
> que trouxe este projeto até aqui."*

## Recomendo: **sai da jornada**, os dois.

### A memória episódica

Ela está declarada em **11 agentes e usada em zero** — é uma das quatro cascas vazias que o próprio
documento nomeia. E está bloqueada por incompatibilidade de assinatura em
`MemoryServer.store_episodic`, documentada no `graph.py`.

O argumento decisivo não é o custo de implementar. É que **o problema que ela resolveria já tem
dono**: "lembrar da conversa" é a Frente 2.1, que foi feita, é estreita por desenho, e guarda
exatamente os quatro campos cuja ausência muda a tabela consultada. Uma memória episódica genérica
por cima disso seria uma segunda memória com outras regras de retenção — e duas memórias de
conversa é como uma delas vira a que ninguém mantém.

Há também uma razão de LGPD que pesa mais que a de engenharia: memória episódica de conversa de
saúde é **retenção de PHI sem finalidade declarada**. Hoje não há base legal registrada para ela, o
`consent_log` tem zero linhas, e a Frente 1.3 ainda vai definir o que se pode guardar e por quanto
tempo. Implementar antes seria criar o dado primeiro e perguntar depois.

> Existe um ADR aberto sobre isso — `docs/adr/0043-memoria-episodica-semantica-ativar-ou-aposentar.md`.
> Esta recomendação é **aposentar**, e ela cabe naquele ADR, não numa página nova.

### O gancho de NPS

Sai por uma razão diferente e mais simples: **medir satisfação de um canal que ainda erra é medir
ruído.** Nas duas semanas de setembro a Helena triou um bebê pela tabela de adulto, prometeu contato
humano com zero processos abertos e afirmou ausência de alerta. Uma nota de NPS colhida nesse
período diria mais sobre o humor de quem respondeu do que sobre o canal.

E há um custo assimétrico: perguntar *"como foi seu atendimento?"* logo depois de uma triagem é uma
mensagem a mais para alguém que pode estar em sofrimento. O canal ainda não ganhou o direito de
fazer essa pergunta.

**O que substitui os dois, e já existe:** a leitura semanal de conversas reais (Frente 5.3) e a
taxa de recusa da cerca de saída. Os dois medem qualidade do texto **sem pedir nada ao
beneficiário** — e foi lendo texto, não colhendo nota, que todos os defeitos graves apareceram.

### Se a decisão for a contrária

Então a jornada precisa dos dois com **estimativa própria e dono**, e a memória episódica precisa
de base legal antes de linha de código. O que não pode continuar é o estado atual: a jornada
prometendo sete estados e o grafo implementando cinco e meio.

---

# 9.1 · A lista de filas — o que é medível e o que não é

O passo diz: *"Depende dos nomes reais das filas, que hoje são rótulos de engenharia — existe um
levantamento pronto com 48 linhas e a coluna de nome real vazia."*

**Preencher a coluna de nome real não é engenharia** — é dizer qual time da operadora atende cada
fila, e isso nenhum agente descobre lendo código. O que dá para fazer, e fiz, é medir o lado que
está no repositório, para que a conversa com a operação comece de um inventário correto.

## As três filas da Helena, medidas

Derivadas de `spec/processes/dmn/escalation_routing.dmn` — a Helena não escolhe fila, a DMN escolhe:

| Fila | Prazo de ack | Quando |
|---|---|---|
| `plantao-clinico` | **PT5M** | red flag clínico e risco psicossocial |
| `enfermagem-triagem` | **PT30M** | intenção clínica |
| `atendimento-humano` | **PT4H** | solicitação de humano, coleta esgotada |

O `PT24H` que também aparece na tabela é de resolução, não de ack.

**As três precisam de nome real e de gente.** E o `PT5M` é o que torna a Frente 9.2 uma pergunta de
operação e não de engenharia: **cinco minutos de prazo exige alguém de plantão às três da manhã.**

## O resto do inventário

Os 15 processos BPMN do repositório declaram **33 grupos distintos** em `camunda:candidateGroups`
— de `medico-auditor` (5 tarefas) e `coordenacao-rede` (4) a filas de uma tarefa só. Quatro deles
não são grupos: são expressões resolvidas em tempo de execução (`${grupo_humano}`,
`${pagto_alcada.grupo_aprovador}`, `${roteamento.grupo_atendimento}`,
`${roteamento_dsr.grupo_revisor}`) — a Helena usa o primeiro.

> **Vale para a conversa com a operação:** habilitar a lista para as **três filas da Helena** não
> exige resolver as outras 29. Tratar as 33 como um bloco é o que transforma um passo de um dia num
> projeto — e o único prazo que está correndo hoje é o de cinco minutos.

---

# O que fica pendente depois deste documento

| Item | De quem |
|---|---|
| Aprovar "sai da jornada" para memória episódica e NPS | dono do produto (cabe no ADR-0043) |
| Nome real e time das três filas da Helena | operação |
| Escala de plantão para o `PT5M` | operação |
| Implementar o consumidor de `process_completed` | engenharia, **depois** do #404 e de um apply |
| Decidir se vale persistir telefone para mensagem de saída | dono + DPO — **recomendo que não** |
