# ADR-0048: `driver_idempotency` re-ancorada como registro de dedup por `wamid` do webhook WhatsApp — re-anchor de ADR-0024 e nota de obsolescencia sobre ADR-0041 §1(d)

**Status:** **Accepted — decisao do dono DEC-27 (issue #1084), 2026-10-31** (ver "Decisao de aceite" ao
final). Redigido por AGENTE (unlock-executor R1, rodada 5 de fechamento de gaps). `/docs/adr/` e
dono-gated (`.github/CODEOWNERS`). · **Data:** 2026-09-04 · **Area:**
Orquestracao / Runtime / Dados

> **Por que uma ADR NOVA e nao um adendo dentro da ADR-0024.** A decisao aprovada do dono (R-073)
> pede "nota de re-anchor em `docs/adr/0024-durable-idempotency-resume-inbound-drivers.md`". Escrever
> essa nota DENTRO do arquivo da 0024 e impossivel sem violar duas regras vivas deste repo:
> `docs/adr/README.md:8` ("ADR aceito so muda por novo ADR") e a cerca de CI
> `tests/unit/docs/test_adr_amendments.py`, que fixa o sha256 das sete ADRs `Accepted` reconciliadas
> pela ADR-0041 — a 0024 entre elas — em `71dd4da`. A INTENCAO da decisao (registrar formalmente o
> novo destino da tabela, em docs/adr, sob revisao do dono) e cumprida aqui; a forma segue a
> convencao do repo, exatamente como a ADR-0041 fez com as sete emendas que a antecederam.

**Marcadores:** `re-anchors`: ADR-0024 (Decisao 3 — backing store) · `obsolete-section`: ADR-0041
§1 (d) ("`driver_idempotency` e hoje uma tabela ORFA"), verdadeira quando escrita em 2026-09-03,
falsa a partir da migracao `0010`.

## Contexto

A ADR-0024 decidiu que a idempotencia duravel dos drivers inbound/resume usaria o padrao Postgres
schema-por-tenant, e prescreveu a tabela `driver_idempotency`. A ADR-0041 §1 documentou o que
sobrou disso na arvore: a classe prescrita (`PostgresDedupeStore`) nunca existiu, os dois drivers
que eram o SUJEITO da ADR foram removidos, a tabela nasceu na migracao `0003` (nao na `0008`) e —
§1(d) — ficou **orfa**: fora das migracoes, o unico consumidor em `src/` era o inventario de
retencao LGPD (`platform/lifecycle/erasure_plan.py`), que ja a classificava
`SEM_COLUNA_DE_TITULAR`.

Ao mesmo tempo, o caminho inbound que HOJE existe — o webhook WhatsApp com dispatch em processo
(`platform/webhooks/whatsapp/`) — carregava exatamente o defeito que a ADR-0024 nomeia no seu
Contexto como LANDMINE ("dupla resposta ao beneficiario"): capturava o `wamid` e apenas o LOGAVA.
Uma re-entrega da Meta rodava um segundo turno completo da Helena.

Duas decisoes do dono, aprovadas em 2026-09-04, fecham as duas pontas:

- **R-073** (gap `DRIVER-IDEMPOTENCY-ORPHAN-TABLE`), opcao 2: **REPROPOR** `driver_idempotency`
  como o registro de dedup do webhook, "na mesma migracao que declara o novo uso" — nem `DROP`,
  nem manter sem uso.
- **R-071** (gap `WEBHOOK-WAMID-DEDUP`), opcao C: registro duravel compartilhado com TTL para
  dedup por `wamid`, **mais** idempotencia na saida `send`, tratadas como uma entrega so.

## Decisao

1. **A tabela `driver_idempotency` e o registro de dedup do canal WhatsApp.** Nao ha tabela nova.
   A migracao `0010_webhook_wamid_dedup.py` declara o novo uso no proprio schema (`COMMENT ON
   TABLE`/`COLUMN`) e adiciona a coluna `status` (`pending`/`processed`, com CHECK) que o protocolo
   claim/commit exige, mais o indice parcial `ix_driver_idempotency_pending`.

2. **A ADR-0024 continua valendo no que decidiu de fato** — Postgres schema-por-tenant, DDL nao
   qualificada, TTL, `expires_at` com indice por idade, o padrao `setup=`/`search_path` do fix #55.
   O que esta ADR re-ancora e apenas o **sujeito**: o consumidor da tabela nao sao mais os drivers
   Kafka->grafo removidos, e sim `platform/driver_idempotency.PostgresDriverIdempotencyRegistry`,
   consumido pelo receptor do webhook e pelo cliente de saida. A classe `PostgresDedupeStore` que a
   0024 prescrevia **continua nao existindo** e nao deve ser construida — a cerca da ADR-0041 que
   garante isso permanece verde e nao foi tocada.

3. **A chave e um pseudonimo COM CHAVE, nunca o `wamid` cru.** Um `wamid` carrega o telefone da
   contraparte em base64 (provado em
   `tests/unit/platform/webhooks/whatsapp/test_dedup_keys.py::test_a_real_shaped_wamid_leaks_the_phone_number_in_base64`),
   entao a chave persistida e o HMAC keyed do `wamid` (`security.py::hash_message_id`, ADR-0035).
   E isso — e so isso — que mantem verdadeira a classificacao `SEM_COLUNA_DE_TITULAR` no plano de
   erasure depois da reproposicao.

4. **ADR-0041 §1(d) fica datada.** A frase "tabela ORFA / nenhum dedup a le ou escreve" era
   verdadeira em `71dd4da` e deixa de ser a partir desta entrega. A ADR-0041 **nao e editada**
   (mesma regra de convencao); esta secao e o marcador.

## Consequencias

**Positivas:** o landmine que a propria ADR-0024 nomeia deixa de estar aberto no unico caminho
inbound vivo; um artefato orfao ganha destino real em vez de `DROP`; o banco passa a DIZER para que
serve a tabela.

**Negativas (aceitas):** a tabela passa a ter dois vocabularios historicos (a `key` de driver da
0024, que nunca chegou a existir em producao, e a `key` de canal desta ADR); a coluna `status` e
uma adicao a um schema que a 0024 desenhou sem ela; e o modo ack-then-queue (R-072) grava linhas
`pending` que, sem consumidor de re-drive, sao evidencia de perda, nao recuperacao — motivo pelo
qual esse modo entra DESLIGADO por padrao (`docs/processes/webhook-whatsapp-ack-then-queue.md`).

## Supersedes

—. Esta ADR **re-ancora** a ADR-0024 (Decisao 3) e **marca como datada** a §1(d) da ADR-0041. Nao
supersede nenhuma das duas, e nao edita os arquivos delas.

## Decisao de aceite (DEC-27, 2026-10-31)

> **Registro datado; historico preservado.** Acima desta secao so o paragrafo de Status mudou, com o
> mesmo numero de linhas. O Status anterior, vigente de 2026-09-04 ate 2026-10-31, era: "Proposto —
> DRAFT/verify. NADA AQUI ESTA RATIFICADO."

**O ato.** Decisao do dono **DEC-27**, opcao (a), 2026-10-31, issue **#1084** (item R-079; achado
F-034). Nenhum bloco de assinatura foi preenchido (ADR-0007). O pacote de revisao esta em
`docs/compliance/human-windows/R-079-dec-adr-proposed.md`. A ADR-0041, cuja §1(d) esta ADR marca como
datada, foi aceita no mesmo ato.

**Revisao independente (2026-10-31, `main` = `2be4515f`).** O revisor leu o texto contra a arvore e
nao e autor da ADR.

Conforme:
- Decisao 1: a migracao `0010` acrescenta `status` com CHECK e declara o novo uso no schema
  (`src/maezo/platform/migrations/versions/0010_webhook_wamid_dedup.py:82-98`).
- Decisao 2: o consumidor e `PostgresDriverIdempotencyRegistry` (`src/maezo/platform/driver_idempotency.py`).
  `PostgresDedupeStore` continua sem existir.
- Decisao 3: a chave e o HMAC keyed do `wamid` (`src/maezo/platform/webhooks/whatsapp/dedup.py:56` ->
  `src/maezo/platform/webhooks/whatsapp/security.py:67`). O teste que prova o vazamento do telefone no
  `wamid` cru existe (`tests/unit/platform/webhooks/whatsapp/test_dedup_keys.py:37`). A classificacao
  `SEM_COLUNA_DE_TITULAR` se mantem (`src/maezo/platform/lifecycle/erasure_plan.py:444-452`).
- O modo ack-then-queue tem o documento citado (`docs/processes/webhook-whatsapp-ack-then-queue.md`).

Divergencia (follow-up conhecido; nao bloqueia o aceite):
1. A Decisao 2 diz que o registro e "consumido pelo receptor do webhook e pelo cliente de saida". Hoje
   ele tambem e construido pelo worker runtime e pelo tool registry
   (`src/maezo/runtime/worker_runtime/service.py:1093`, `src/maezo/gateway/tool_registry.py:1222`). O
   texto descreve menos consumidores do que existem.
