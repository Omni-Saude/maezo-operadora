# Nota de design — ack-then-queue no webhook do WhatsApp

**Data:** 2026-09-04 · **Autor:** agente (unlock-executor R1) · **Status:** implementado com a
flag DESLIGADA por padrao · **Decisao do dono:** R-072 (aprovada 2026-09-04) · **Gaps:**
`WEBHOOK-WAMID-DEDUP` (R-071), `DRIVER-IDEMPOTENCY-ORPHAN-TABLE` (R-073)

> Esta nota e uma recomendacao de engenharia com a implementacao correspondente — **pendente de
> assinatura de dono/operacao** para ser LIGADA em producao. A flag existe e e testada nos dois
> estados; o que nao existe e o consumidor de re-drive descrito na secao 5.

## 1. O que a decisao aprovada diz

> "Adotar ack-then-queue como forma alvo no MESMO PR da dedup por `wamid` (R-071), sem rodada
> separada de investigacao: o registro duravel que a dedup cria e a perna de entrada da fila, e o
> dispatch sincrono continua atras de uma feature flag desligavel em
> `src/maezo/platform/webhooks/whatsapp/app.py`."

## 2. O problema que ela ataca (que a dedup NAO ataca)

A dedup por `wamid` trata o SINTOMA: quando a Meta re-entrega, nada roda duas vezes. A CAUSA da
re-entrega e outra — o dispatch e sincrono: `POST /webhook` so responde depois do turno completo da
Helena (LLM + DMN + start de processo + envio). Se esse turno passar do orcamento da Meta, ela
considera a entrega falha e re-entrega, independentemente de o turno ter dado certo.

O `docs/runbooks/whatsapp-webhook.md` §1 registra a expectativa da Meta ("Return 200 OK (Meta
requires < 20s)"). Um turno com chamada de LLM + engine nao tem garantia nenhuma de caber ai.

## 3. A forma implementada

`WHATSAPP_WEBHOOK_ACK_THEN_QUEUE=true` (`WhatsAppWebhookSettings.ack_then_queue`, default
`false`):

1. o receptor **reivindica** o `wamid` no registro duravel (`driver_idempotency`, linha `pending`);
2. responde `200 {"status": "queued", "queued": N}` — nunca `dispatched`, porque nada foi
   despachado ainda;
3. roda o turno numa task de fundo, que sela a linha (`processed`) em caso de sucesso e a retira
   (`release`) em caso de falha.

A **perna de entrada da fila e a propria linha reivindicada** — e o que a decisao do dono chama de
"o registro duravel que a dedup cria e a perna de entrada da fila". Nao ha broker: nao ha producer
Kafka neste build (`platform/webhooks/service.py`), e inventar um topico que ninguem consome seria
pior do que a task de fundo honesta.

## 4. Contrato de re-entrega (o que o `200` promete e o que NAO promete)

| Estado | Significa | Meta re-entrega? |
|---|---|---|
| `200 {"status": "queued"}` | a entrega foi **aceita e registrada duravelmente**; o turno ainda nao rodou | Nao |
| `200 {"status": "duplicate"}` | ja havia reivindicacao viva para este `wamid` | Nao |
| `500 dedup_unavailable` | o registro nao pode responder; **nada** foi aceito | Sim (desejado) |
| `500 dispatch_failed` / `partial_failure` | modo sincrono; algo falhou de fato | Sim (desejado) |

O `200` de `queued` **nao** afirma que o beneficiario foi respondido. Essa e a diferenca material
entre os dois modos, e o motivo pelo qual o corpo da resposta mudou de nome de campo (`queued`, nao
`dispatched`): um ack que dissesse "despachado" antes de despachar seria fato fabricado.

## 5. Orcamento de latencia do ack e o que ainda NAO existe

- **Orcamento do ack (modo ligado):** o caminho ate a resposta e verificacao de assinatura + parse
  + UMA reivindicacao por mensagem (um `INSERT ... ON CONFLICT` por mensagem). Ordem de grandeza
  de milissegundos, sem LLM e sem engine — dentro de qualquer orcamento razoavel da Meta, contra os
  segundos-a-dezenas-de-segundos do turno completo no modo sincrono.
- **Lease em voo:** `WHATSAPP_WAMID_DEDUP_LEASE_S` (default 120s) e o tempo que uma linha `pending`
  suprime re-entrega antes de ser considerada abandonada e re-reivindicavel. Precisa ser MAIOR que
  o turno mais longo honesto e MUITO menor que o TTL (24h).
- **O que NAO existe:** nenhum consumidor varre `status = 'pending' AND created_at < now() - lease`
  para re-executar turnos abandonados. O indice parcial `ix_driver_idempotency_pending` (migracao
  `0010`) existe justamente para tornar essa varredura barata quando ela for construida.

**Consequencia honesta, e a razao da flag ficar DESLIGADA:** com a flag ligada, um turno que morre
depois do ack esta perdido — a Meta ja recebeu `200` e nao vai re-entregar, e ninguem re-executa a
linha `pending`. A perda fica AUDITAVEL (a linha `pending` velha e a evidencia, e a query esta
escrita acima), mas perda auditavel continua sendo perda. No modo sincrono (default), a mesma morte
de processo resulta em re-entrega da Meta, que a dedup absorve corretamente.

**Criterio para ligar em producao:** existir um consumidor de re-drive das linhas `pending`
expiradas, com o proprio teste de reexecucao. Ate la, `ack_then_queue` e um modo pronto e testado,
nao um modo recomendado.

## 6. Criterio de virada do lote misto (R-100), registrado aqui porque depende desta mesma guarda

Com a guarda de dedup ATIVA no caminho de entrada, um lote misto (algo deu certo, algo falhou)
passa a responder `500` — a Meta re-entrega o lote inteiro, a parte que ja deu certo e suprimida
como duplicata e apenas a mensagem que falhou roda de novo. Sem guarda, o `500` re-enviaria o que ja
foi entregue, e por isso o comportamento permanece `200` com o rotulo `status="partial_failure"`.
Isso e a condicao tecnica que a decisao R-100 fixou ("sem nova decisao do dono"), avaliada em
codigo em `app.py`, e nao uma segunda rodada de decisao.
