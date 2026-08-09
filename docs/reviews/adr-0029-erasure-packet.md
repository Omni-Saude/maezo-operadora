# ADR-0029 — DPO review packet: per-layer erasure, retention and the audit-chain tension

**Status:** awaiting DPO review. Nothing in this packet is an approval, and nothing in this
repository has been approved by anyone on the strength of it. Every retention VALUE, every legal
basis and every per-relation verdict is an explicit `PENDENTE` placeholder; only the DPO (with
jurídico) may fill them in.

**What is being asked of the reviewer:** for each of the 15 persistence relations enumerated in
§3, decide ONE of `ELIMINAR` / `ANONIMIZAR` / `RETER_COM_BASE_LEGAL`, and — where the verdict is
not `ELIMINAR` — the legal basis and the retention period. That is the whole ask. The mechanics
are written out for each relation so the decision can be made on evidence rather than on trust,
and §5 states plainly the one place where **no available option is clean**.

| Item | Reference |
|---|---|
| Gating ADR | `docs/adr/0029-audit-chain-pruning-reanchor.md` (**Proposed** — `:3`, "requires DPO + orchestrator ratification (no self-certification)") |
| Companion | `docs/compliance/ADR-0020-amendment-draft.md` (legal-hold registry) — a hard precondition ADR-0029 names at `:213-214` |
| Ratification artifact | `spec/policies/retention/erasure-plan.template.yaml` (DRAFT, CODEOWNERS-gated) |
| Seam + dry-run | `src/maezo/platform/lifecycle/erasure_plan.py` |
| Tests | `tests/unit/platform/test_erasure_plan.py` |
| Related, NOT ratified by this packet | `spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml` (per-CATEGORY legal bases) |

---

## 1. What this packet is, and what it deliberately is not

This is a **review-instead-of-author** deliverable, on the same precedent as
`docs/reviews/mzo-060-dba-review-packet.md`. The structural work — which relations exist, what
each one is keyed on, in what order they would have to be touched, and what breaks if they are —
is a FACT about the schema, and it is done. The decisions are untouched.

**It is not the retention matrix.** `spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml`
is a per-CATEGORY table (`dados_saude_prontuario`, `auditoria_nao_repudio`, …) loaded by
`src/maezo/platform/lifecycle/legal_bases_matrix.py`. This packet's artifact is per-RELATION.
They meet at ratification — one category will cover several relations here — but neither ratifies
the other, and this work did not touch the matrix template.

**It does not enable erasure.** `ErasureManager.erase()` and `.verify()` raise
`ErasureNotImplementedError` unconditionally (`src/maezo/platform/erasure.py:152`, `:187`), and
this change does not alter that by one line. The only executable thing added is a dry-run that
issues `SELECT count(*)` and nothing else.

**Tier, stated plainly:** PHI-adjacent but **inert**. No engine path, no worker topic, no BPMN
element, no composition root imports the new module. Its entire SQL surface is three `SELECT
count(*)` statements which no shipped caller supplies an executor for.

## 2. The two switches

| Switch | Thrown by | State today |
|---|---|---|
| **A. A per-relation decision exists** | the DPO editing `spec/policies/retention/erasure-plan.template.yaml` | `ratificado: false`, `dpo_review: PENDENTE`, all 15 verdicts `PENDENTE` |
| **B. An execution mechanism exists** | engineering work that does not exist | `ErasureManager` refuses; both identity bridges (§3.1) are absent |

**Both must be thrown, and throwing A alone is deliberately not enough.** `assert_dry_run_only()`
refuses a non-dry mode in *both* states, and which reason it gives is itself the disclosure:
`plan_unratified` before ratification, `execution_mechanism_absent` after. A governance act must
never double as a destructive trigger, so ratifying this artifact is provably incapable of causing
an effect on its own. Pinned by
`test_erasure_plan.py::test_execute_mode_is_still_refused_once_the_plan_is_ratified`.

## 3. The persistence inventory

Enumerated from migrations `0001`–`0007` plus the relations the custody/erasure design names but
Alembic does not create. The same 15 rows appear in the artifact and in
`erasure_plan.PERSISTENCE_LAYERS`; a test asserts the two sets are EQUAL in both directions, and a
second test asserts the enumeration covers every `CREATE TABLE` in the migration chain, so a future
migration that adds a relation fails CI until a DPO decides for it too.

| # | Camada | Relação | Criada em | Identificação do titular | Resolução |
|---|---|---|---|---|---|
| 1 | trabalho | `checkpoints` | **nenhuma migração** — `PostgresSaver.setup()`/`.asetup()` (`0006:14-23`, `:25-35`) | `thread_id` + `checkpoint_ns` | `NAO_PROVISIONADA` |
| 2 | trabalho | `checkpoint_blobs` | idem (`0006:19-21`) | `(thread_id, checkpoint_ns, channel, version)` — BYTEA **portador de PHI** | `NAO_PROVISIONADA` |
| 3 | trabalho | `checkpoint_writes` | idem (`0006:22-23`) | `(thread_id, …, task_id, idx)` — BYTEA | `NAO_PROVISIONADA` |
| 4 | trabalho | `checkpoint_migrations` | idem (`0006:17`) | nenhuma (bookkeeping do saver) | `SEM_REFERENCIA_TITULAR` |
| 5 | trabalho | `agent_checkpoints` | `0001:33-43`; **removida** `0006:69` | — | `RETIRADA` |
| 6 | trabalho | `agent_checkpoint_writes` | `0001:48-58`; **removida** `0006:68` | — | `RETIRADA` |
| 7 | episódica | `agent_memory` | `0001:63-75`; índice parcial `0001:82-86` | `tenant_id` + `fhir_patient_id` (**NULLABLE**, `0001:69`) | `PONTE_AUSENTE` |
| 8 | semântica | `agent_memory.embedding` | `0001:72` (coluna `vector(1536)`); extensão `0001:28` | a MESMA linha da #7 | `PONTE_AUSENTE` |
| 9 | auditoria | `audit_chain` | `0002:27-51` | **nenhuma coluna**; vínculo dentro de `decision_basis` jsonb (`0002:36`) | `SEM_COLUNA_DE_TITULAR` |
| 10 | auditoria | `audit_emit_dedup` | `0005:59-67` | `(tenant, dedup_key)` — derivado do evento | `SEM_COLUNA_DE_TITULAR` |
| 11 | custódia | `custody_bundles` | `0004:31-45` | `evidence_refs` jsonb (`0004:36`) — ponteiros pseudonimizados (`custody.py:11`) | `SEM_COLUNA_DE_TITULAR` |
| 12 | registro | `erasure_log` | `0004:64-79`; índice `0004:82-84` | `tenant_id` + `fhir_patient_id` (**NOT NULL**, `0004:67`) | `PONTE_AUSENTE` |
| 13 | idempotência | `a2a_idempotency` | `0003:32-45` | `(task_id, tenant)`; `result` jsonb (`0003:39`) | `SEM_COLUNA_DE_TITULAR` |
| 14 | idempotência | `driver_idempotency` | `0003:61-67` | `key` text PK (`0003:62`) — chave de NEGÓCIO | `SEM_COLUNA_DE_TITULAR` |
| 15 | fronteira AMH | `amh_inbox` | `0007:127-229` | nenhuma — 5 campos de sujeito excluídos por construção | `SEM_REFERENCIA_TITULAR` |

Not data, listed so the reviewer knows it was considered: `<tenant>_alembic_version` (bookkeeping
de migração) and the `vector` extension (`0001:28`, a type, not storage).

### 3.1 Two identity bridges are missing, and that is the finding under everything else

**Only two columns in the entire chain identify a titular, and both are `fhir_patient_id`**
(`agent_memory` `0001:69`, `erasure_log` `0004:67`). Neither is reachable from what the system
actually holds when a request arrives:

* **`titular_pseudo_id` → `fhir_patient_id` does not exist.** SP-OP-LGPD-DSR-001 carries a
  pseudonym (`src/maezo/tools/workers/lgpd.py:112`); `lgpd.py:293-294` records the absence in
  terms, and `platform/lifecycle/__init__.py:117-123` names it as an independent blocker on
  `verify-erasure` that "stays true regardless of matrix state".
* **`thread_id` → `fhir_patient_id` does not exist.** The working layer is keyed by thread, not by
  patient (`src/maezo/platform/erasure.py:196-206`, verbatim: "there is no `fhir_patient_id` column
  to filter on directly").

**Consequence the reviewer must weigh:** a dry-run given a `titular_pseudo_id` — the only reference
the DSR process has — can count **nothing, anywhere**. The dry-run reports
`NOT_COUNTED_IDENTITY_BRIDGE_ABSENT` per relation rather than a count of `0`, on purpose: `0` would
read as "nothing to erase", which is the same false-success class `ErasureManager` was built to
refuse. Both bridges are **engineering gaps, not DPO gaps** — ratifying this artifact does not close
them and must not be recorded as if it did.

### 3.2 Documentation that overstates coverage, corrected here

`erasure.py:224-225` describes `DELETE FROM episodes.transcripts` / `episodes.decisions`, and
`erasure.py:240-242` describes `DELETE FROM semantic.embeddings`. **None of those relations exists
in any of the seven migrations.** They are illustrative docstrings on unreachable helpers, not
schema. A reviewer who took them for schema would conclude the cascade covers four relations it
does not. The real episodic and semantic layers are ONE table — `agent_memory`, with `embedding`
as a column on the same row (`0001:72`).

### 3.3 There are no foreign keys

Verified by scan of all seven migration files: **no `REFERENCES` and no `FOREIGN KEY` anywhere in
the chain.** Every "order of operations" in this packet is therefore a *procedural* commitment, not
something the database enforces or a `CASCADE` would carry out. Nothing stops a wrong order; that
is precisely why the order is written down here to be reviewed.

---

## 4. Per-relation mechanics

For each relation: what is retained today, how the titular's rows would be identified, and the
statement each of the three verdicts would require. **Every statement below is a DOCUMENT.** None of
it exists in code: the module holds `SELECT count(*)` and nothing else, structurally guaranteed by
the AST guard in `tests/unit/platform/test_lifecycle.py`, which forbids a destructive literal
anywhere in the `lifecycle` package. Values are bound (`:tenant_id`, `:subject_ref`) and never
interpolated, so no reference ever appears in SQL text — asserted by
`test_a_patient_reference_counts_exactly_the_subject_bearing_relations`.

### 4.1 `agent_memory` — camada episódica (#7) · `decisao_dpo: PENDENTE`

**Hoje:** retenção indefinida. Nada expira, nada é podado; a única varredura por idade que existe
no repositório é a de `audit_chain`, e ela está recusada. Índice parcial por titular em `0001:82-86`.

**Identificação:** `tenant_id = :tenant_id AND fhir_patient_id = :subject_ref`.

```sql
-- ELIMINAR
DELETE FROM agent_memory
 WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref;

-- ANONIMIZAR  (o embedding vai junto — é coluna da mesma linha)
UPDATE agent_memory
   SET payload = '{}'::jsonb, embedding = NULL, fhir_patient_id = NULL
 WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref;

-- RETER_COM_BASE_LEGAL → nenhuma sentença; `base_legal` + `retencao` passam a ser obrigatórios.
```

**Alerta ao DPO — `fhir_patient_id` é NULLABLE (`0001:69`).** Duas consequências: (a) linhas
gravadas sem ele não são alcançadas por predicado nenhum e ficam órfãs de qualquer eliminação —
uma lacuna de cobertura que nenhuma decisão sua conserta; (b) `ANONIMIZAR` anulando a coluna é uma
**porta de mão única**: a linha deixa de ser localizável por qualquer verificação futura, inclusive
a que confirmaria que a eliminação ocorreu.

**Ordem:** 7, antes de `erasure_log` (#12) e depois das camadas de trabalho.

### 4.2 `agent_memory.embedding` — camada semântica (#8) · `decisao_dpo: PENDENTE`

**Hoje:** `vector(1536)` (`0001:72`), na mesma linha da #7. Não é relação própria.

```sql
-- ANONIMIZAR apenas o vetor, preservando a linha episódica
UPDATE agent_memory
   SET embedding = NULL
 WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref AND embedding IS NOT NULL;
```

Existe como entrada separada porque a escolha pode **divergir** da #7: anular só o vetor preserva o
registro episódico, enquanto `ELIMINAR` na #7 leva o vetor junto. Se as duas decisões forem
incoerentes (`RETER` na #7 e `ELIMINAR` aqui, por exemplo), a #7 é a que manda — a linha é uma só.

### 4.3 `erasure_log` — o registro da própria eliminação (#12) · `decisao_dpo: PENDENTE`

**Hoje:** retenção indefinida; `fhir_patient_id` **NOT NULL** (`0004:67`).

```sql
-- ELIMINAR   (apaga a prova de que o direito foi atendido)
DELETE FROM erasure_log
 WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref;

-- ANONIMIZAR (preserva o fato, remove o identificador — exige decidir o token substituto)
UPDATE erasure_log
   SET fhir_patient_id = :anonymized_token, requested_by = :anonymized_token
 WHERE tenant_id = :tenant_id AND fhir_patient_id = :subject_ref;

-- RETER_COM_BASE_LEGAL → a base legal é o próprio dever de comprovar o atendimento.
```

**A tensão, sem eufemismo:** esta tabela É a prova de que a eliminação aconteceu, e ela guarda o
identificador do titular numa coluna NOT NULL. Eliminá-la junto com o resto apaga a evidência do
atendimento ao direito; não eliminá-la mantém um identificador do titular depois da eliminação.
`ANONIMIZAR` é o meio-termo, e tem um custo próprio: exige que o DPO decida o token substituto e
aceite que `verify()` deixa de poder confirmar a limpeza por titular. **Não há terceira via óbvia;
a escolha é do DPO.**

**Ordem:** 12, e a última das relações com coluna de titular — o registro tem de ser escrito depois
de tudo o que ele atesta.

### 4.4 `audit_chain` (#9) · `decisao_dpo: PENDENTE` — ver §5

**Hoje:** DL-0018 ratificou 5 anos, e o `DELETE` que os implementaria está **recusado**
(`platform/lifecycle/__init__.py:101-105`); `retention_query()` tem zero chamadores em produção,
propriedade travada em CI. **Nenhuma coluna identifica o titular.** O vínculo, quando existe, está
dentro de `decision_basis` jsonb (`0002:36`) — que ADR-0006/ADR-0007 exigem que já esteja
pseudonimizado — e resumido em `input_hash` (`0002:35`).

Não há sentença aqui. As opções e por que nenhuma delas é uma eliminação por titular estão em §5.

### 4.5 `custody_bundles` (#11) · `decisao_dpo: PENDENTE`

**Hoje:** retenção indefinida. `bundle_root` é raiz de Merkle sobre `evidence_refs` ordenados
(`custody.py:36-67`), `UNIQUE` em `0004:43`, e `audit_record_hash` (`0004:41`) é o vínculo de volta
para a cadeia.

```sql
-- ELIMINAR   (herda a tensão da cadeia — o bundle é PROJEÇÃO sobre ela, ADR-0020, não um fork)
DELETE FROM custody_bundles
 WHERE tenant_id = :tenant_id AND id = ANY(:bundle_ids);

-- ANONIMIZAR (INVALIDA bundle_root: verify_bundle() recomputa e compara, custody.py:73-86)
UPDATE custody_bundles
   SET evidence_refs = '[]'::jsonb, evidence_count = 0
 WHERE tenant_id = :tenant_id AND id = ANY(:bundle_ids);
```

**Alerta:** `:bundle_ids` não é derivável de uma referência de titular — não há predicado por
titular nesta tabela. Ele viria de uma resolução prévia por `process_instance_id` (`0004:37`) que
hoje não existe. E `ANONIMIZAR` aqui não é neutro: mexer em `evidence_refs` faz `verify_bundle()`
falhar, ou seja, o custo é o mesmo de `ELIMINAR` sem o benefício.

### 4.6 `a2a_idempotency` (#13) e `driver_idempotency` (#14) · `decisao_dpo: PENDENTE`

**Hoje:** `a2a_idempotency` não expira. `driver_idempotency` tem `expires_at` (`0003:64`) com
índice (`0003:69-72`) — **um prazo de fato, que não é uma decisão de retenção ratificada**, e a
diferença importa para este pacote.

```sql
-- ANONIMIZAR o único campo que pode carregar conteúdo derivado
UPDATE a2a_idempotency SET result = NULL
 WHERE tenant = :tenant_id AND task_id = ANY(:task_ids);
```

**Reter aqui PROTEGE a eliminação.** Apagar uma linha de idempotência devolve uma tarefa já
processada ao estado elegível: uma re-entrega re-executaria o trabalho e poderia
**re-materializar** exatamente o dado que a eliminação removeu. É o mesmo raciocínio já registrado
para `amh_inbox` no pacote MZO-060 (§7.3), e ele inverte a intuição de que "eliminar mais é sempre
mais seguro".

**Alerta específico de `driver_idempotency`:** `key` é uma chave de **negócio** (`0003:62`). DL-0043
perna (c) / ADR-0035 tratam exatamente do risco de PHI dentro de chaves de negócio persistidas, e
enquanto `spec/policies/privacy/phi-business-key-remediation.yaml` não estiver ratificado a FORMA
dessa chave não está decidida — logo não é possível afirmar hoje que ela não carrega dado do titular.

### 4.7 `audit_emit_dedup` (#10) · `decisao_dpo: PENDENTE`

**Hoje:** sem expurgo; o índice `ix_audit_emit_dedup_created` (`0005:71-74`) existe explicitamente
para uma varredura por idade que ninguém executa. Guarda `record_hash` (`0005:62`), um digest.
Eliminar linhas reabre a janela de emissão duplicada na cadeia — o efeito é sobre
exatamente-uma-vez, não sobre o titular.

### 4.8 `amh_inbox` (#15) · `decisao_dpo: PENDENTE`

**Sem alvo de cascata.** A análise coluna a coluna já foi feita e revisada em
`docs/reviews/mzo-060-dba-review-packet.md` §7.1-7.3: não há coluna de payload, e
`portable_subject_ref`, `amh_mpi_ref`, `beneficiary_ref`, `consent_decision_ref` e
`protected_source_record_ref` são excluídos por construção, com teste de sentinela lido de volta do
Postgres. A entrada existe aqui para que a AUSÊNCIA de alvo seja uma afirmação **ratificada** e não
um esquecimento — e porque reter estas linhas é o que impede uma re-entrega de re-materializar o
dado eliminado (§4.6).

### 4.9 Camada de trabalho (#1-#4) · `decisao_dpo: PENDENTE`

**Não são criadas por este repositório.** `0006:14-23` lista as quatro tabelas reais do
`langgraph-checkpoint-postgres`, e `0006:25-35` registra que **nada neste código chama `.setup()`
ou `.asetup()`**, logo elas não existem em nenhum ambiente provisionado por aqui. `checkpoint_blobs`
é descrita como portadora de PHI em BYTEA (`0006:19-21`) e é, simultaneamente, a relação de maior
risco da camada e a que menos tem chave de titular.

```sql
-- ELIMINAR, quando (e somente quando) a ponte thread_id -> fhir_patient_id existir
DELETE FROM checkpoint_writes WHERE thread_id = ANY(:thread_ids);
DELETE FROM checkpoint_blobs  WHERE thread_id = ANY(:thread_ids);
DELETE FROM checkpoints       WHERE thread_id = ANY(:thread_ids);
```

Ordem filha-antes-de-pai por convenção, **não** por FK (§3.3). `:thread_ids` é hoje underivável.

### 4.10 Relações retiradas (#5, #6) · `decisao_dpo: PENDENTE`

`agent_checkpoints` e `agent_checkpoint_writes` foram criadas em `0001:33-43`/`:48-58` e removidas
em `0006:69`/`:68`. `0006` registra que nada jamais leu ou escreveu nelas. Enumeradas porque
`erasure.py` citou esses nomes por engano (corrigido) e um revisor pode procurá-los; a decisão
esperada é a trivial, mas ela deve ser **registrada**, não presumida.

---

## 5. A tensão da cadeia de auditoria — apresentada, não resolvida

**A propriedade.** `record_hash` é computado sobre todos os campos persistidos, e o preimage inclui
`details` (a coluna `decision_basis`) e `input_hash` (`src/maezo/gateway/audit.py:266-284`). O
sucessor guarda esse hash em `prev_record_hash`. Portanto:

> **Anonimizar um campo dentro de `decision_basis` quebra a cadeia EXATAMENTE como eliminar a
> linha.** "Anonimizar em vez de eliminar" não é a saída barata que aparenta ser em `audit_chain`.

E o que quebra, quebra para os dois verificadores: o in-memory `AuditSink.verify_chain()`
(`src/maezo/gateway/audit.py:344-378`) semeia a caminhada com `GENESIS_PREV_HASH` (`:353`, e
`GENESIS_PREV_HASH = "0"*64` em `:48`) e exige `record.prev_hash == prev` (`:356-363`), logo falha
**no índice 0**; e o `verify_chain()` do Postgres (`src/maezo/gateway/audit_postgres.py:560-637`)
monta o mapa `by_prev` (`:592-604`), semeia em `by_prev.get(GENESIS_PREV_HASH)` (`:607`) e, sem a
linha genesis, reporta todo sobrevivente como **"unreachable from genesis"** (`:624-633`) —
indistinguível de corrupção. `UNIQUE(prev_record_hash)` (`0002:44-50`) **não** ajuda: ADR-0029
`:42-47` explica que ele é anti-fork e é *ortogonal* à contiguidade — uma cadeia furada continua
satisfazendo o UNIQUE enquanto falha a verificação.

> **Nota de higiene para o revisor.** As citações de linha que a própria ADR-0029 dá para estes
> dois verificadores (`ADR-0029:31-40` — `audit.py:310-344`/`:319`/`:320-329` e
> `audit_postgres.py:333-410`/`:365-377`/`:380`/`:396-406`) estão **desatualizadas**: os arquivos
> se moveram desde 2026-07-17 e esses números hoje apontam para outro código (`audit.py:310`, por
> exemplo, é hoje uma docstring de outro método). A única que continua exata é
> `GENESIS_PREV_HASH` em `audit.py:48`. As citações do parágrafo acima foram re-derivadas contra a
> árvore em 2026-08-09. A **substância** da ADR-0029 continua correta em cada ponto — só os
> ponteiros envelheceram — mas quem for conferir o argumento seguindo os números da ADR vai ler o
> trecho errado, e é melhor saber disso antes de decidir D-1.

**O descompasso central, dito de uma vez.** ADR-0029 §1 (`:64-70`) admite **apenas prefixo a partir
do genesis**, e proíbe em termos um buraco no meio: *"A hole in the middle would create a second
unbridgeable seam and is forbidden."* As linhas de um titular **não formam um prefixo** — estão
espalhadas pela cadeia inteira. **O mecanismo que a ADR-0029 desenha não alcança eliminação por
titular.** Ele foi desenhado para expurgo por IDADE, e a analogia com eliminação por titular não se
sustenta. Esta é a constatação que o pacote existe para colocar diante do DPO.

**As opções que a ADR-0029 já nomeia**, e o que cada uma resolve — e não resolve — para um pedido
de art. 18 VI:

| Opção (ADR-0029) | O que faz | Para eliminação por titular |
|---|---|---|
| **Prefixo com checkpoint assinado** (§1-§3, `:64-134`) | Elimina o prefixo mais antigo e insere, na MESMA transação, um checkpoint assinado que reancora a cadeia | Alcança o titular **apenas** se todas as linhas dele estiverem no prefixo elegível. Para qualquer outro caso, não se aplica |
| **`pruned_merkle_root` + arquivo frio** (§2, `:96-98`) | O prefixo expurgado continua **provável** a partir de um arquivo fora de banda | Corta ao contrário: um arquivo que ainda contém as linhas do titular **não é eliminação**. Uma decisão de expurgo com arquivo frio precisa dizer o que acontece com o arquivo |
| **Mini-cadeia de checkpoints** (§5, `:152-158`) | Expurgos sucessivos encadeiam checkpoints entre si | Não muda a forma de prefixo; herda a mesma limitação |
| **Reter com base legal / diferir** (§6, `:160-178`; ADR-0020-amendment **Opção C**) | Uma linha sob hold ativo torna o prefixo elegível vazio; o ciclo vira no-op e é **reavaliado quando o hold cai** — explicitamente *"not retained forever"* | A forma que de fato cabe num pedido de art. 18 VI hoje. É `RETER_COM_BASE_LEGAL`, e **a base legal e o prazo são do DPO** |
| **`UNIQUE` permanece NON-DEFERRABLE** (§7, `:180-191`) | Rejeitada a alternativa deferrable: reabriria a janela de fork concorrente que o teste real do DL-0018 provou quebrada | Restrição de contorno: fecha a saída "afrouxar a constraint para permitir outra ordem" |

**Três coisas que a ADR-0029 exige e que não existem** (`:212-226`), e que portanto nenhuma decisão
sua pode ser executada contra hoje: o registro de legal-hold (ADR-0020-amendment, precondição
dura), a custódia/rotação da chave de assinatura do checkpoint, e a governança de quem autoriza um
expurgo. A própria ADR também registra que o piso de 5 anos do DL-0018 é **DRAFT pendente de
jurídico/regulatório** (`:224`) — ou seja, o número que hoje aparece como retenção da cadeia ainda
não é uma decisão ratificada.

**`custody_bundles` herda esta seção.** ADR-0020 define o bundle como PROJEÇÃO sobre a cadeia, não
como fork; a integridade dele (`custody.py:73-86`) depende dos mesmos `evidence_refs` que uma
anonimização mexeria.

**`decisao_dpo` para `audit_chain` e `custody_bundles`: PENDENTE.** Este pacote não escolhe. Se a
escolha for `ELIMINAR`, ela é hoje **inexequível** pelo mecanismo ratificado, e isso precisa ser
registrado como tal em vez de virar um item de backlog que parece implementável.

---

## 6. O dry-run executável

`src/maezo/platform/lifecycle/erasure_plan.py` responde, para uma referência de titular, **o que
SERIA tocado por camada — em contagens, nunca em conteúdo**.

* **Só conta.** As três únicas sentenças que o módulo pode emitir são `SELECT count(*)` com binds
  nomeados. Um teste varre todas elas por `DELETE`/`DROP`/`TRUNCATE`/`UPDATE`/`INSERT`/`ALTER`.
* **Não abre conexão.** O executor é um `counter` que o CHAMADOR fornece; sem ele, toda relação
  contável reporta `NOT_COUNTED_NO_COUNTER` e **nenhuma contagem é fabricada**.
* **Ausência de contagem ≠ zero.** Não existe status que signifique "assuma zero". Uma camada
  inalcançável nunca pode ser lida como uma camada vazia.
* **Nada vaza.** A referência do titular chega a um bind e a mais nada: não vai para o relatório,
  para o texto SQL, para o `render_report()` nem para os campos de log. Se o `counter` levantar
  exceção, o relatório guarda o TIPO e **suprime a mensagem** — um erro de driver pode ecoar os
  binds. E a CLI lê a referência do ambiente, nunca de `argv` (que aparece em `ps` e no histórico
  do shell); `allow_abbrev=False` impede que `--subject-ref` caia silenciosamente em
  `--subject-ref-kind`.
* **Recusa fechada.** `assert_dry_run_only()` recusa qualquer modo não-dry, nos dois estados (§2),
  e a recusa acontece **antes** de qualquer probe — provado com um counter que registra chamadas e
  cujo registro fica vazio.

```bash
# Inerte: sem banco, sem counter, sem referência. Relata estrutura + estado do plano + decisões.
python -m maezo.platform.lifecycle.erasure_plan --tenant <schema>
```

Hoje isso imprime `plan=UNRATIFIED (not_ratified)` e `PENDENTE` nas 15 linhas.

---

## 7. Decisões abertas para o revisor

Além dos 15 verdictos, que são o ask principal:

| # | Decisão | Posição do autor |
|---|---|---|
| **D-1** | `audit_chain` e `custody_bundles`: `RETER_COM_BASE_LEGAL` (a única forma exequível hoje, §5) ou registrar `ELIMINAR` como **inexequível** e abrir o trabalho de projeto? | **Sem posição.** É exatamente a escolha que este pacote existe para não tomar. O autor só afirma que `ELIMINAR` não é alcançável pelo mecanismo da ADR-0029 e que dizer o contrário seria falso. |
| **D-2** | `erasure_log`: eliminar, anonimizar (qual token?) ou reter? (§4.3) | **Sem posição.** As três têm custo e nenhuma é neutra. |
| **D-3** | `agent_memory` com `fhir_patient_id` NULL — linhas órfãs de qualquer predicado (§4.1). Exigir `NOT NULL` numa migração futura, ou aceitar a lacuna com base legal? | **Sugere endereçar**, porque é uma lacuna de COBERTURA que nenhuma decisão de retenção conserta. É mudança de schema e sai do escopo deste pacote. |
| **D-4** | A retenção de facto de `driver_idempotency` (`expires_at`, `0003:64`) deve ser **ratificada como** política, ou substituída por um prazo do DPO? | **Sinaliza a diferença**: hoje existe um prazo sem decisão por trás dele. |
| **D-5** | O artefato deve ser force-included na wheel (precedente `spec/policies/amh`, `pyproject.toml:110-116`)? | **Deliberadamente NÃO feito.** Empacotar um placeholder não-ratificado tem seu próprio risco, e nada num contêiner precisa dele enquanto tudo está recusado. Se um dia um consumidor em runtime precisar do plano, a inclusão passa a ser obrigatória — senão o contêiner nunca acha o artefato, que é o modo de falha que MZO-050a já pagou uma vez. |
| **D-6** | Qual categoria da matriz de bases legais cobre cada relação daqui? | **Não mapeado de propósito.** Inventar a correspondência seria decidir pelo DPO; as duas ratificações se encontram, mas nenhuma implica a outra. |

---

## 8. Evidência de verificação

Executado no worktree de autoria em 2026-08-09:

| Verificação | Resultado |
|---|---|
| `pytest tests/unit -q` | verde (contagem no relatório final da entrega) |
| `ruff check` + `ruff format --check` (`src`, `tests`) | verde |
| `mypy --strict` (189 arquivos) | verde |
| `make validate-artifacts` | verde |
| Guard AST do pacote `lifecycle` (`test_lifecycle.py`) | verde **com o módulo novo dentro do escopo do scanner** — nenhum literal destrutivo, nenhum import do caminho de expurgo |
| Varredura de FK nas 7 migrações | **nenhuma** `REFERENCES` / `FOREIGN KEY` (§3.3) |
| Anti-drift artefato ↔ código | conjuntos de relações IGUAIS, asserido nos dois sentidos |
| Não-vacuidade | todo `CREATE TABLE` do encadeamento 0001-0007 está enumerado |
| Recusa com plano DRAFT | `load_erasure_plan()` → `not_ratified` |
| Flip por DADO | fixture ratificado em `tmp_path` carrega, pelo argumento explícito **e** pela env var, **sem uma linha de código alterada** |
| Recusa de modo não-dry, ratificado | `execution_mechanism_absent` (razão diferente, mesmo veredito) |
| Recusa precede o trabalho | counter instrumentado registra **zero** chamadas |
| Referência do titular | ausente de `repr(report)`, de `render_report()`, do texto SQL e do detalhe de erro |
| Ratificação meio-preenchida | uma única `base_legal` placeholder recusa o plano inteiro |
| CLI ponta a ponta | sai 0, imprime `UNRATIFIED (not_ratified)` e `PENDENTE` nas 15 relações; `--subject-ref` é rejeitado como flag inexistente |

---

## 9. O ato de ratificação

Ratificar é uma **edição de YAML**. Não exige mudança de código, migração nem deploy.

**Passo 1 — revisar.** §3 (inventário), §4 (mecânica por relação), §5 (a tensão da cadeia), §7
(D-1..D-6).

**Passo 2 — editar `spec/policies/retention/erasure-plan.template.yaml`.** Para CADA uma das 15
entradas de `camadas`, substituir os três placeholders:

```yaml
    decisao_dpo: ELIMINAR | ANONIMIZAR | RETER_COM_BASE_LEGAL   # vocabulário fechado
    base_legal:  <a base legal, quando não for ELIMINAR>
    retencao:    <o prazo/regra>
```

e os campos de topo:

```yaml
status: RATIFICADO          # era DRAFT
ratificado: true            # era false — o BOOLEANO, nunca a string "true"
dpo_review: APPROVED        # era PENDENTE

dpo_reviewer: <nome e papel>
dpo_review_date: <AAAA-MM-DD>
evidence_ref: <linha do evidence-ledger>
notes: <notas e condições>
```

O carregador recusa qualquer valor que ainda pareça placeholder — **uma ratificação meio-preenchida
não é uma ratificação** — e recusa um veredito fora do vocabulário fechado.

**Passo 3 — registrar** em `docs/evidence-ledger.md`.

**O que o Passo 2 NÃO faz:** ligar qualquer coisa. A chave B (§2) continua não-lançada, e a
eliminação segue recusada — agora por `execution_mechanism_absent`.

### Quem pode fazer isto

**Somente o DPO (com jurídico).** Nenhum agente, orquestrador ou gatekeeper automatizado pode
preencher estes campos ou flipar `ratificado`. Fabricar uma aprovação neste repositório é um evento
de compliance, não um conflito de merge.

### Registro de ratificação — A SER PREENCHIDO PELO DPO

| Campo | Valor |
|---|---|
| DPO revisor | **PENDENTE** |
| Data da revisão | **PENDENTE** |
| Veredito (APROVADO / REJEITADO / APROVADO COM RESSALVAS) | **PENDENTE** |
| D-1 (`audit_chain` / `custody_bundles`) | **PENDENTE** |
| D-2 (`erasure_log`) | **PENDENTE** |
| D-3 (`agent_memory.fhir_patient_id` NULLABLE) | **PENDENTE** |
| D-4 (retenção de facto de `driver_idempotency`) | **PENDENTE** |
| D-5 (inclusão na wheel) | **PENDENTE** |
| D-6 (mapeamento para a matriz de categorias) | **PENDENTE** |
| 15 vereditos por relação | **PENDENTE** |
| Linha do evidence-ledger | **PENDENTE** |

*Mesmo com esta tabela preenchida, nada opera até que
`spec/policies/retention/erasure-plan.template.yaml` seja de fato editado. Este pacote registra o
raciocínio; aquele arquivo é a chave.*
