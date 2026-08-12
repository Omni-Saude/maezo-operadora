# Onda 4 — Ancoragem externa da cadeia de auditoria (leg 1: o ESCRITOR, INERTE)

**Status:** construído, testado e **INERTE**. Nada em `src/maezo/` importa este código; a flag
`MAEZO_AUDIT_ANCHOR_ENABLED` está OFF por padrão em todo lugar. Ativação é mudança de **dado/config
+ wiring do dono**, nunca mudança de código.

**Escopo desta perna:** o escritor de checkpoint assinado + os dois seams (signer, store) + as
implementações rotuladas de dev/teste. **Fora de escopo, deliberadamente:** o job periódico de
comparação contínua Postgres↔âncora (perna 2) e os drills de tamper/restore (perna 3).

Artefatos: `src/maezo/gateway/audit_anchor.py`, `tests/unit/gateway/test_audit_anchor.py`.

---

## 1. A ameaça (auditoria externa §4)

As três defesas da cadeia — hash-link SHA-256 (`gateway/audit.py`), advisory lock por tenant
(`gateway/audit_postgres.py`) e `UNIQUE(prev_record_hash)`
(`platform/migrations/versions/0002_audit_chain.py`) — vivem **todas dentro do banco**. Um ator
privilegiado que reescreve o banco inteiro e recomputa a cadeia satisfaz todas elas: ele controla
cada byte que os verificadores leem.

Remédio prescrito: raiz de cadeia **por tenant**, assinada, ancorada **fora** do banco (object
storage com retention-lock/WORM, conta de segurança separada, ou ambos), chave em **KMS/HSM**,
contendo `{último record hash, contagem de registros, janela temporal, schema/versão}`, com
comparação contínua e drills.

## 2. O que a cadeia existente NÃO perde

`audit.py` e `audit_postgres.py` **não foram tocados**. `audit_anchor.py` importa três nomes puros e
**nada mais** — pinado por `test_anchor_module_imports_only_pure_names_from_the_audit_modules`, que
pina **nome E escopo**:

| nome | de | escopo | por quê |
| --- | --- | --- | --- |
| `GENESIS_PREV_HASH` | `maezo.gateway.audit` | módulo | `audit` é stdlib-only, não carrega driver |
| `AuditRecord` | `maezo.gateway.audit` | módulo, sob `if TYPE_CHECKING:` | só tipo; custo zero em runtime |
| `schema_for_tenant` | `maezo.gateway.audit_postgres` | **função** (`AnchorCheckpoint.__post_init__`) | `audit_postgres` importa `asyncpg` no topo; ver abaixo |

**Peso de import (por que o terceiro é preguiçoso).** `gateway/__init__.py` linhas 11-15 já declaram
a regra da casa: `PostgresAuditSink` **não** é re-exportado do pacote porque puxaria `asyncpg` no
import, e o caminho `AuditSink` em memória não deve adquirir dependência dura incondicional do
driver. Um import de `schema_for_tenant` em escopo de módulo **recriava exatamente esse acoplamento
um arquivo adiante**: `import maezo.gateway.audit_anchor` — um canonicalizador puro que não abre
socket — trazia `asyncpg` e ~310 módulos. Medido: **310 → 277** módulos, `asyncpg` presente → ausente;
o custo **marginal** sobre `maezo.gateway.audit` caiu de **34 para 1** módulo (o próprio
`audit_anchor`). A validação continua sendo a **mesma** de `audit_postgres` — uma segunda cópia da
gramática de schema divergiria daquela que de fato guarda a interpolação de `SET search_path` /
advisory-lock (controle: `test_constructing_a_checkpoint_still_uses_the_real_schema_validator`).
O orçamento é expresso **relativo** a `audit`, não em contagem absoluta: o absoluto é dominado por
`structlog`, dependência de repo inteiro, e viraria alarme de bump de dependência em vez de canário
de acoplamento.

Não há SQL, nome de tabela, INSERT/UPDATE/DELETE nem conexão asyncpg no módulo — pinado por
`test_the_anchor_module_carries_no_chain_mutating_literal` (docstrings excluídos via a mesma técnica
de `scripts/ci/check_effect_chokepoint_fence.py::_docstring_constant_ids`) e, para o asyncpg, por
`test_importing_the_anchor_module_does_not_drag_in_asyncpg` (interpretador **limpo**, via
subprocesso — este arquivo de teste já importa `audit_postgres`, então só um processo novo consegue
responder à pergunta).

Este módulo **não é um quarto verificador de cadeia**. `AuditSink.verify_chain()` e
`audit_postgres.verify_chain()` recomputam hashes de registro; a âncora nunca. Ela atesta *como a
cadeia estava*, não *se a cadeia é internamente consistente* — e é justamente a COMPARAÇÃO posterior
(perna 2) que transforma uma reescrita silenciosa em uma reescrita detectada.

## 3. Relação com ADR-0029 (que permanece **Proposed**)

ADR-0029 §2 define um checkpoint que vive **dentro de `audit_chain`**, ocupa o slot
`GENESIS_PREV_HASH` liberado por um prune de prefixo, e existe para tornar um prune *lícito*. Isso
está **BLOQUEADO** atrás de ratificação DPO + a emenda ADR-0020 (registro de legal-hold), e **nada
disso é construído aqui**: nenhuma coluna nova, nenhuma migração, nenhum DELETE habilitado.

O que se toma emprestado de ADR-0029 é apenas o **idioma**: um checkpoint que se compromete com
`{head hash, contagem, janela, schema/versão}`, serializado canonicamente, assinado, e tratado como
**tamper** quando a assinatura falha (§2 verbatim: *"a checkpoint that fails signature verification
is treated as tamper"*).

> **Proposta de emenda redacional para um humano (NÃO aplicada — o status do ADR-0029 e suas seções
> relevantes para ratificação não foram editados):** ADR-0029 hoje trata "checkpoint assinado"
> exclusivamente como linha *interna* de `audit_chain`. Se o dono quiser que o ADR cubra também a
> âncora externa, a redação mínima seria acrescentar em §2, como parágrafo final:
> *"O mesmo idioma de checkpoint assinado (canonicalização determinística + assinatura sobre os
> bytes canônicos + tratamento de falha de assinatura como tamper) aplica-se a um segundo uso,
> independente e não bloqueado por esta ADR: a **âncora externa periódica** da auditoria §4, que
> **não** escreve em `audit_chain`, **não** habilita DELETE e **não** depende do registro de
> legal-hold. Os dois usos compartilham o formato e a custódia de chave; apenas o uso interno
> (prune) permanece bloqueado."*
> O item de custódia/rotação de chave já listado em "Open items blocking ratification" do ADR-0029
> passa a ser compartilhado pelos dois usos.

## 4. O contrato de canonicalização (byte-estabilidade)

```
json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
           allow_nan=False).encode("utf-8")     # sem newline final
```

| escolha | razão |
| --- | --- |
| `sort_keys=True` | ordem de chave seria ordem de inserção, i.e. propriedade do código-fonte; um reordenamento inócuo invalidaria toda assinatura anterior |
| `separators=(",",":")` | sem whitespace; o default insere `", "`/`": "` e convida a "reformatação cosmética" |
| `ensure_ascii=True` | stream puramente ASCII independente de locale/encoding do filesystem |
| `allow_nan=False` | `NaN`/`Infinity` não são JSON; a stdlib os emite mesmo assim por padrão |
| **sem `default=str`** | única divergência deliberada de `audit.hash_input()`. Lá o fallback leniente é correto (`details` vem do chamador e a alternativa é recusar auditar um efeito que já aconteceu). Aqui todo campo é primitivo tipado do próprio módulo, então um valor não-serializável é BUG, e `str()` cravaria um `repr` instável (`<object at 0x7f…>`) dentro de uma raiz assinada. Levanta `AnchorSerializationError`. |
| `datetime` → `astimezone(UTC).isoformat()`, naive recusado | mesma razão de `AuditRecord.__post_init__`: instante de timestamp naive é ambíguo e adivinhar zona fabricaria a janela que a âncora atesta |

**Sem prefixo de separação de domínio externo:** `anchor_format` é a **primeira** chave (ordem
alfabética) do mapping canônico e está *dentro* dos bytes assinados. Uma versão de formato
incompatível bumpa essa constante, e assinaturas antigas não podem ser replayed como novo formato.

**Campos do checkpoint (7, set-equality pinada em teste):** `anchor_format`, `chain_head_hash`,
`chain_schema_version`, `record_count`, `tenant_id`, `window_end`, `window_start`.

**Envelope armazenado (4 chaves):** `anchor_format`, `checkpoint` (o mapping canônico verbatim),
`root`, `signature` (`algorithm`/`key_id`/`value`). A assinatura cobre os bytes do **checkpoint**,
não do envelope: editar o campo `root` armazenado quebra a recomputação enquanto a assinatura
continua válida — as duas checagens são independentes de propósito.

**Chave de armazenamento:** `<tenant>/<window_end compacto UTC>-<root>.anchor.json`. O `root` na
chave faz uma re-ancoragem idêntica **colidir** (o WORM recusa: duplicata é no-op que precisa ser
alta, não silenciosa); o timestamp na frente faz listagem lexical == ordem cronológica, que é o que
a perna 2 precisa para achar "a âncora mais recente" sem parsear todo envelope.

**PHI:** hashes, contagens, timestamps e strings de versão — só. Nenhum campo livre, nenhum conteúdo
de registro, nenhum identificador de titular. `tenant_id` é o nome do schema Postgres
(`[a-z][a-z0-9_]*`), dado organizacional.

## 5. Os dois seams (o que o DONO precisa ligar)

| seam | default de produção (shipped) | duplo de dev/teste (shipped) | o que o dono liga |
| --- | --- | --- | --- |
| `AnchorSigner` / `AnchorSignatureVerifier` | `RefusingAnchorSigner` — levanta `AnchorSignerUnavailableError` incondicionalmente | `LabeledFakeKmsAnchorSigner` (HMAC-SHA256) | cliente **KMS/HSM** onde a chave privada nunca sai do módulo; papéis `sign` e `verify` separados de propósito (o escritor precisa de assinar; o job de comparação só de verificar) |
| `AnchorStore` | `RefusingAnchorStore` — levanta `AnchorStoreUnavailableError` em toda operação | `LabeledFakeWormAnchorStore` (simulação local de WORM) | bucket com **retention-lock**, idealmente em **conta de segurança separada** |

`resolve_anchor_signer(None)` / `resolve_anchor_store(None)` selecionam os *refusing* — um seam não
ligado **recusa** em vez de fabricar. Ligar a flag sozinha, sem wiring real, falha alto (pinado por
`test_enabled_writer_without_a_signer_refuses_rather_than_fabricating`).

### Por que o fake assina com HMAC e não com `cryptography` (que É dependência declarada)

Precisamente porque um fake **não pode parecer o real**. Uma assinatura de âncora real é
**assimétrica** e sua metade privada nunca sai de um HSM; esta é simétrica, o material de chave é um
`bytes` em processo, e qualquer um que o tenha forja qualquer âncora. Produzir um Ed25519 aqui
geraria um blob **byte-indistinguível** de um genuíno — exatamente a masquerade que a classe existe
para impossibilitar. `cryptography` continua disponível e **não usada**, para o signer real do dono.

Três rótulos independentes viajam com cada assinatura —
`LABELED-FAKE-HMAC-SHA256-NAO-VINCULATIVO` (algoritmo), `FAKE-KMS-NAO-VINCULATIVO:` (key id) e
`FAKE-SIG-NAO-VINCULATIVO:` (o **próprio valor**) — e `verify` exige os três, então uma assinatura
despida de rótulo **não verifica**: o fake recusa ser lavado. A construção também recusa um
`key_label` contendo `arn:`, `kms:`, `hsm:` ou `projects/`.

### O que o WORM fake realmente ENFORCE (e o que não)

Enforce: (a) write-once **na CRIAÇÃO** — `put` em chave existente levanta
`AnchorWormViolationError`, com pré-checagem **e** `open(..., "xb")` (`O_CREAT|O_EXCL`), de modo que
uma corrida que passe pela pré-checagem é recusada pelo kernel; (b) `chmod 0o444` após a escrita —
proteção contra **acidente**, não contra o escritor (ver abaixo); (c) contenção na raiz por
**grafia** (sem path absoluto, sem `..`, sem backslash) **e** por **resolução**
(`path.resolve().is_relative_to(root.resolve())`, os dois lados resolvidos) — a gramática lê
caracteres, não o filesystem, então sem a segunda metade um diretório intermediário symlinkado faz a
chave legal `amh/a.anchor.json` escrever **fora** da raiz; (d) arquivo-marcador auto-rotulado na
raiz. Construção é **pura** (zero I/O) — load-bearing para o dark build.

**`0o444` não é integridade.** O modo é **discricionário**: o **DONO do arquivo** — o mesmo UID não
privilegiado que o escreveu, i.e. o próprio processo — faz `chmod(0o644)` e reescreve os bytes à
vontade, e `get()` devolve a forjada. Não é preciso `root` nem escalação nenhuma. Dizer "só um ator
privilegiado adultera" seria **exagero**, e fake exagerado é pior que fake nenhum: é exatamente a
falha que a auditoria externa §4 nomeia, um andar abaixo. A propriedade que a classe realmente
impõe é `O_EXCL` na **criação**; os testes isolam justamente isso
(`test_worm_store_refuses_overwrite_even_without_the_exists_precheck` faz o arquivo gravável de
propósito, para arrancar a defesa do `chmod` e deixar só o `O_EXCL` em pé), e
`test_worm_store_read_only_mode_is_not_integrity_against_the_owner` **pina a divulgação**: se um dia
a classe passar a resistir ao próprio UID, esse teste fica vermelho e esta seção precisa ser
reescrita para cima.

Não provê, e o store real precisa: retention lock imposto pelo serviço de storage (o **dono** do
arquivo — ou um `root` local — faz `chmod`/reescrita/`rm` à vontade), durabilidade off-host, e
fronteira de conta/credencial separada.

**Residual divulgado (TOCTOU):** a checagem de contenção é *check-then-write*; um adversário que
plante um symlink na janela entre `_resolve` e o `open` ainda escapa por diretório intermediário. O
componente **final** não fica exposto assim (`O_CREAT|O_EXCL` recusa symlink existente). Fechar o
resto exigiria descida `openat`/`O_NOFOLLOW` que este fake deliberadamente não carrega — e quem
escreve dentro da raiz também pode simplesmente `rm` o conteúdo, então o residual não muda a postura
honesta da classe.

## 6. A flag

`MAEZO_AUDIT_ANCHOR_ENABLED`, **default OFF**. Vocabulário truthy `{"1","true","yes","on"}` após
`strip().lower()` — o mesmo `_TRUTHY` de `runtime/agent_runtime/a2a_composition.py`, para o operador
não precisar decorar dialeto por flag.

Com a flag off, `write_anchor()` retorna `AnchorWriteOutcome(written=False, reason="ANCHOR_DESABILITADO")`
**antes** de canonicalizar, **antes** de chamar o signer e **antes** de tocar o store. As duas
metades importam: um writer que pulasse o store mas ainda chamasse o signer vazaria assinatura para
o log de auditoria do KMS (e custaria dinheiro) a cada execução de uma feature "desligada".

Direção de default é o **oposto** das cercas de segurança do repo, de propósito: para uma cerca,
config ausente resolve para o estado RESTRITIVO; para um dark build, config ausente resolve para o
estado INERTE. Ambas as leituras compartilham a regra — **ausência de decisão explícita do operador
nunca é consentimento**.

**Prova de inércia mais forte que a flag:** nada em `src/maezo/` importa `audit_anchor` — AST-scan
com allowlist HARDCODED e **VAZIA** (`test_no_production_module_imports_the_anchor_writer`). Quando
a perna 2 entregar seu módulo de comparação/agendamento, **aquela lista** é onde o novo importador é
declarado — deliberadamente, num diff que o revisor vê, nunca como alargamento silencioso.

## 7. Controles RED — sondas executadas (mutar → vermelho → reverter)

Higiene: `PYTHONDONTWRITEBYTECODE=1`, purge de `__pycache__` antes e depois, e **commit antes de
sondar** (para `git checkout --` não comer trabalho).

| # | neutralização aplicada | resultado observado |
| --- | --- | --- |
| D1 | remover `"record_count"` de `to_canonical_mapping()` | **10 failed, 92 passed** — `test_canonical_mapping_keys_match_the_field_set`, `test_checkpoint_bytes_match_pinned_literal`, `test_checkpoint_root_matches_pinned_literal`, `test_every_checkpoint_field_changes_the_root[record_count]`, `test_utc_normalization_makes_the_root_a_function_of_the_instant`, `test_fixture_chain_root_matches_an_independent_recomputation`, `test_verify_rejects_a_tampered_payload`, `test_enabled_writer_seals_a_verifiable_anchor`, `test_re_anchoring_an_identical_checkpoint_is_refused_loudly`, `test_envelope_shape_is_pinned` |
| D6 | `sign()` emitir o digest **sem** `FAKE_ANCHOR_SIGNATURE_PREFIX` | **4 failed, 98 passed** — `test_fake_signature_carries_all_three_labels`, `test_signature_round_trip_verifies`, `test_enabled_writer_seals_a_verifiable_anchor`, `test_envelope_root_field_is_independent_of_the_signature` |
| D7 | remover o guard `path.exists()` **e** trocar `"xb"` por `"wb"` | **3 failed, 99 passed** — `test_worm_store_refuses_overwrite`, `test_worm_store_refuses_overwrite_even_without_the_exists_precheck`, `test_re_anchoring_an_identical_checkpoint_is_refused_loudly` |
| D8 | remover o early-return de `anchor_writes_enabled()` em `write_anchor` | **3 failed, 99 passed** — `test_disabled_writer_touches_no_seam_and_no_filesystem`, `test_disabled_writer_creates_no_file_even_with_a_real_store`, `test_disabled_writer_is_inert_even_with_no_seams_injected` |
| D9 | `gateway/custody.py` importar `write_anchor` | **1 failed, 101 passed** — `test_no_production_module_imports_the_anchor_writer` (aponta `gateway/custody.py`) |

**Achado da primeira sonda D1 (reparado, commit `d292af6`):**
`test_every_checkpoint_field_changes_the_root[...]` permanecia **VERDE** sob exatamente a mutação
que existe para pegar. Ele comparava contra `_PINNED_ROOT`; ao remover a chave do mapping, a raiz de
base **também** muda, então `mutado != pinado` continuava verdadeiro enquanto os dois checkpoints
passavam a hashar **identicamente**. A base agora é recomputada, e a comparação é entre duas raízes
vivas. Canário que não pode falhar não é canário.

**Nota de forma da sonda D9:** importar `audit_anchor` de dentro de `gateway/audit_postgres.py`
produz `ImportError` de import circular (a âncora importa `schema_for_tenant` de lá) — recusa
estrutural mais forte que a asserção, mas que aborta a coleta antes do teste rodar. A sonda foi
refeita com um importador acíclico (`gateway/custody.py`) para exercitar de fato a asserção.

## 8. Handoff para as pernas 2 e 3

- **Perna 2 (job de verificação/comparação contínua):** consome `AnchorStore.get`/`list_keys`
  (ordem lexical == cronológica), recomputa `sha256(canonical_bytes(envelope["checkpoint"]))` contra
  `envelope["root"]`, e verifica a assinatura via `AnchorSignatureVerifier` — o Protocol de verify é
  separado do de sign exatamente para que o job não receba autoridade de assinatura de que não
  precisa. `test_enabled_writer_seals_a_verifiable_anchor` já executa essa sequência ponta a ponta.
  Ao landar, declarar o novo importador na allowlist HARDCODED de
  `test_no_production_module_imports_the_anchor_writer`.
- **Perna 3 (drills de tamper/restore):** alterar, remover, forkar e reconstruir registros contra um
  Postgres vivo, e provar que a comparação com a âncora externa detecta cada caso. Marcador
  `@pytest.mark.integration` já existente é o lugar.
- **Aberto para o dono (fora de qualquer perna de agente):** custódia/rotação da chave KMS/HSM
  (item já listado em ADR-0029 "Open items"); o bucket WORM + conta separada; a cadência do job
  periódico; e a decisão sobre a emenda redacional proposta em §3 acima.
