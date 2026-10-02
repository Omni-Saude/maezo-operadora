# ADR-0062: Roteamento de conversa por agente no número único

**Status:** Proposto
**Data:** 2026-10-01
**Área:** Orquestração | Dados | Segurança

> **Condição de habilitação.** Este ADR não liga nada. O interruptor `MAEZO_ROTEADOR_LUCAS` nasce
> desligado (default literal `False`), e `scripts/ci/check_roteador_lucas.py` reprova ligá-lo fora
> de `dev-sa-east-1`. Na onda (c) do plano, mesmo ligado, o roteador roda em **sombra**: grava o
> agente ativo e o motivo, loga os sinais léxicos e não muda resposta nenhuma. O DL-0053 (texto em
> `docs/plans/lucas-numero-unico.md` §1.3) segue aguardando assinatura.

## Contexto

Decisão do Filipe (01/10/2026): número único, com a Helena na entrada. Pedido de pessoa escala a
qualquer momento (P5), saúde e risco ficam sempre com a Helena (P2), cobrança passa para o Lucas
(P3), o Lucas atende até acabar ou a pessoa mudar de assunto (P4), e o resto recebe o texto fixo
(P6). Isso exige lembrar, entre uma mensagem e a seguinte, **quem está com a conversa**. O plano
completo, com as ondas, é `docs/plans/lucas-numero-unico.md`.

Alternativas para guardar esse estado, medidas ou descartadas no plano (§2.1):

- **No checkpoint do LangGraph:** rejeitada. Medido em 01/10 com langgraph 1.2.9 e
  `InMemorySaver`: o `checkpoint_ns` no grafo raiz é ignorado, e dois grafos no mesmo `thread_id`
  se sobrescrevem (o contador seguiu 1..5 entre os dois e só existiu a chave `''`).
- **Em variável de processo do motor:** rejeitada. Conversa comum não abre processo, e o estado
  precisa existir antes de qualquer caso.
- **Classificador próprio do Lucas:** rejeitada (§2.5). Ele teria de ver o texto do beneficiário
  (PHI) e duplicaria a triagem, que precisa rodar antes de qualquer outra coisa.

## Decisão

1. **Tabela própria `conversa_agente_ativo`** (migration 0017), uma linha por
   `(tenant, conversation_id)`, fora do estado dos dois grafos. Colunas: o agente ativo
   (`helena`/`lucas`), o motivo da última transição, duas colunas tipadas de continuidade do
   Lucas (subtipo de cobrança em domínio fechado e competência `YYYY-MM`) e três instantes
   (`ativo_desde`, `ultimo_turno_em`, `expira_em`). Nenhum telefone, nenhum texto.
2. **Escrita por compare-and-set** (`revisao`): `INSERT ... ON CONFLICT DO NOTHING` na primeira
   vez, `UPDATE ... WHERE revisao = :esperada` depois. Uma escrita por mensagem recebida, **depois**
   do turno; se o turno cai, nada é gravado. Quem perde o CAS relê e recalcula — o roteador não
   chama LLM, então recalcular é barato.
3. **A máquina de estados é uma função pura**,
   `src/maezo/platform/webhooks/whatsapp/roteamento.py::decidir_transicao`, com a tabela de §2.2
   do plano. O vencimento (`expira_em = ultimo_turno_em + MAEZO_LUCAS_INATIVIDADE_MINUTOS`, 60 por
   padrão) e a linha ausente levam à Helena.
4. **A camada determinística só puxa para a Helena ou para um humano, nunca para o Lucas** (§2.3).
   `src/maezo/platform/webhooks/whatsapp/pre_roteamento.py` aplica dois léxicos versionados
   (`spec/policies/roteamento/pre-roteamento.yaml`, `status: DRAFT`), normalizados sem acento e
   sem caixa: pedido de pessoa (P5) e sinal forte de saúde (P2). O sinal de saúde **bloqueia** o
   handoff; ele não decide triagem (quem decide é a DMN). Ir para o Lucas exige a saída tipada da
   Helena **neste mesmo request**, e qualquer combinação que não case uma linha da tabela volta
   para a Helena — nunca se fica no Lucas por omissão.
5. **Saúde vence pedido de pessoa** quando os dois aparecem juntos (§1.2): o escalonamento
   clínico também leva a uma pessoa, com prioridade maior.
6. **Isolamento de tenant pelo padrão vigente do repositório:** schema por tenant, coluna `tenant`
   na PK e em todo `WHERE`, e um CHECK amarrando o prefixo `wa:<tenant>:` do `conversation_id` ao
   próprio `tenant`. O repositório não usa RLS em migration nenhuma; introduzir RLS aqui seria
   mudança transversal, fora deste ADR.
7. **Interruptor desligado = byte a byte o de hoje.** Com `MAEZO_ROTEADOR_LUCAS` desligado o
   roteador nem é construído (`service.py`), o despachante recebe `roteador=None` e não faz
   nenhuma chamada a mais. A cerca de CI cobra os itens 1 a 7 de §4 do plano (6 e 7 desde a
   onda f: o handoff so' nasce no no' `handoff_cobranca` da Helena, e o turno do Lucas so' roda
   em `_turno_do_lucas`, depois dos lexicos e do `ainvoke` da Helena, com o `result["handoff"]`
   dela). O `LucasTurno` recusa um handoff cujo `message_ref` nao e' o da entrega corrente
   (`HandoffDeOutraMensagemError`): `helena`/`retorno_falha`, sem envio do Lucas.

## Consequências

**Positivas:** o estado de roteamento fica legível numa tabela pequena, auditável e sem PHI; a
regra que mantém saúde e pedido de pessoa fora do Lucas é estrutural (a função pura, com tabela
de verdade em teste), não convenção de prompt; a sombra permite medir o léxico em dev antes de ele
decidir qualquer coisa.

**Negativas (aceitas):**
- Uma escrita a mais por mensagem no banco do tenant (só com o interruptor ligado).
- Falso positivo do léxico de saúde, quando ligado de verdade (onda f), manda para
  `falha_tecnica`, ou seja, para a fila humana. Na sombra ele só aparece em log.
- O prazo de purga (30 dias depois do último turno, até 100 linhas a cada 10 minutos por
  processo) é proposta de engenharia; a decisão é do DPO (`erasure-plan.template.yaml`, camada
  `roteamento_conversa`, `PENDENTE`).

## Pendências e riscos conhecidos (01/10/2026, revisão do PR #589, onda e)

- **`continua_lucas` não tem produtor.** O §2.2 do plano prevê que o Lucas responda a um
  `greeting`/`information` quando ele já está com a conversa ("ok", "e o de setembro?"), usando as
  três colunas da tabela. Nenhuma onda faz isso ainda: quem responde esse turno é a Helena. Até a
  onda que fizer o Lucas atender sem um `handoff` no mesmo turno, `decidir_transicao` grava
  `helena`/`retorno_falha` nesse caso (`roteamento.py::CONTINUA_LUCAS_ATENDIDO = False`). Gravar
  `lucas`/`continua_lucas` faria a tabela registrar um atendimento que não aconteceu e renovaria
  `expira_em`. O token continua no domínio da coluna e da migration 0017.
- **Risco aceito: duas réplicas podem enviar a frase de passagem.** Se duas mensagens da mesma
  conversa chegarem ao mesmo tempo em réplicas diferentes, as duas leem `helena` antes de qualquer
  escrita, e as duas mandam a frase. A chave de saída é por mensagem de entrada, então ela não
  deduplica entre mensagens distintas. O CAS resolve a linha (quem perde relê e recalcula), mas a
  frase duplicada já foi enviada. O custo é uma mensagem de cortesia repetida, sem efeito adverso.
  Não foi tratado nesta onda.
- **Falha do Lucas depois da frase** vira `falha_tecnica` pelo escalonamento da Helena (processo
  aberto e resposta honesta), e a linha volta para `helena`/`retorno_falha`. O beneficiário não fica
  só com "vou te passar…". Falha do léxico conta como sinal de saúde, ou seja, bloqueia a passagem.
  As degradações do roteador são contadas em `maezo_roteamento_falhas_total{tipo}`.

## Supersedes

—
