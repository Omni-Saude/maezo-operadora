# ADR-0042: `driver_idempotency` re-ancorada como registro de dedup por `wamid` do webhook WhatsApp — re-anchor de ADR-0024 e nota de obsolescencia sobre ADR-0041 §1(d)

**Status:** **Proposto — DRAFT/verify. NADA AQUI ESTA RATIFICADO.** Redigido por AGENTE
(unlock-executor R1, rodada 5 de fechamento de gaps); um agente nao ratifica ADR. Assinatura humana
pendente — `/docs/adr/` e dono-gated (`.github/CODEOWNERS`). · **Data:** 2026-09-04 · **Area:**
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
