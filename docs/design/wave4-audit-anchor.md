# Onda 4 — Ancoragem externa da cadeia de auditoria (pernas 1 e 2, INERTES)

**Status:** construído, testado e **INERTE** nas duas pernas. Nada em `src/maezo/` alcança este
código a partir do caminho de auditoria; as duas flags (`MAEZO_AUDIT_ANCHOR_ENABLED` para o
escritor, `MAEZO_AUDIT_ANCHOR_VERIFY_ENABLED` para o verificador) estão OFF por padrão em todo
lugar. Ativação é mudança de **dado/config + wiring do dono**, nunca mudança de código.

**Escopo.** §§1-7 são a **perna 1**: o escritor de checkpoint assinado + os dois seams (signer,
store) + as implementações rotuladas de dev/teste. §9 é a **perna 2**: o job de verificação
contínua Postgres↔âncora, que é o que dá valor de segurança à perna 1 — âncora que ninguém compara
é arquivo. **Fora de escopo, deliberadamente:** os drills de tamper/restore contra Postgres vivo
(perna 3, §8).

Artefatos: `src/maezo/gateway/audit_anchor.py`, `tests/unit/gateway/test_audit_anchor.py`,
`src/maezo/gateway/audit_anchor_verify.py`, `tests/unit/gateway/test_audit_anchor_verify.py`.

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

**Prova de inércia mais forte que a flag:** o único módulo de `src/maezo/` que importa
`audit_anchor` é o que está **declarado pelo nome** na allowlist HARDCODED de
`test_no_production_module_imports_the_anchor_writer` — hoje exatamente um,
`gateway/audit_anchor_verify.py` (perna 2, §9). A lista era **VAZIA** durante toda a perna 1 e
recebeu essa entrada num diff que o revisor viu, que é para isso que ela existe; ela permanece uma
lista de **nomes**, nunca um prefixo ou padrão, então o próximo importador também precisa ser
digitado ali. A entrada não enfraquece a inércia: o importador é **ele próprio escuro** — flag irmã
OFF por padrão e allowlist **VAZIA** para ele em
`test_no_production_module_imports_the_verify_job`. O caminho de auditoria continua sem alcançar o
escritor por rota estática nenhuma; ele só alcança um módulo que ninguém alcança.

**Grafias que a cerca pega** (`_CAUGHT_IMPORT_SPELLINGS`, cada uma exercitada uma a uma por
`test_import_fence_predicate_catches_every_spelling` — a matriz não pode apodrecer longe do código
que a implementa):

| grafia | onde `audit_anchor` aparece |
| --- | --- |
| `from maezo.gateway.audit_anchor import X` (com/sem `as`) | `ImportFrom.module` |
| `from .audit_anchor import X` | `ImportFrom.module` (relativo) |
| `import maezo.gateway.audit_anchor` (com/sem `as`) | `Import.names` |
| `from maezo.gateway import audit_anchor` (com/sem `as`) | **`ImportFrom.names`** |
| `from . import audit_anchor` / `from .. import gateway, audit_anchor` | **`ImportFrom.names`** |

As duas últimas linhas são o **reparo do achado F1** da revisão externa: o predicado original lia
só `ImportFrom.module`, e nessas formas o MÓDULO é um *nome* enquanto `node.module` é o **pacote**
(ou `None`). A cerca ficava **VERDE** com o import plenamente vivo. O ramo de `node.names` casa por
**igualdade exata**; existe um único `audit_anchor` na árvore, e se algum homônimo surgir o modo de
falha é **falso alarme** numa cerca de allowlist vazia — alto e seguro, nunca perda silenciosa.

**Grafias que a cerca NÃO pega, divulgadas** (`_UNCAUGHT_IMPORT_SPELLINGS`):
`importlib.import_module("maezo.gateway.audit_anchor")`, `__import__`, e acesso por atributo através
do pacote pai (`import maezo.gateway` e depois `maezo.gateway.audit_anchor.write_anchor(...)`, que
só resolve se algo já tiver importado o submódulo). São **fora do escopo de AST por construção** —
import por string não é nó de import. O que compensa não é um predicado mais esperto, e sim o
**formato da allowlist**: ela é VAZIA, então nenhum importador é admitido e qualquer rota vira diff
revisado.

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

### 7.1 Segunda rodada — sondas dos reparos de revisão externa (F1–F4)

Mesma higiene. A suíte foi de **102 → 121** testes; as contagens abaixo são pós-reparo.

| # | achado | neutralização aplicada | resultado observado |
| --- | --- | --- | --- |
| D9 | F1 (MAJOR) | `gateway/custody.py`: `from maezo.gateway import audit_anchor` | **ANTES do reparo: 102 passed, 0 failed** com o import VIVO (`maezo.gateway.audit_anchor` em `sys.modules`, `write_anchor` alcançável) — a cerca era vácua nessa grafia. **DEPOIS: 1 failed, 113 passed** — `test_no_production_module_imports_the_anchor_writer` aponta `gateway/custody.py`. Revertido → **114 passed** |
| D9 | F1 (MAJOR) | `gateway/__init__.py`: `from . import audit_anchor` (forma relativa) | **1 failed, 113 passed** — mesma asserção, aponta `gateway/__init__.py`. Revertido → **114 passed** |
| D11 | F2 | remover a checagem `is_relative_to` de `_resolve` | **3 failed, 118 passed** — `..._escapes_via_a_symlinked_intermediate_dir`, `..._refuses_reading_through_a_symlinked_intermediate_dir`, `..._refuses_a_final_component_symlink`. Sonda direta pré-reparo: `put("amh/a.anchor.json", …)` com `<root>/amh` symlinkado gravou **fora da raiz** (`…/outside/a.anchor.json` == `b"ESCAPED-PAYLOAD"`); pós-reparo, `AnchorKeyError` e `outside` vazio |
| D7/F3 | F3 | fazer o store resistir a adulteração pós-escrita pela própria API (cache write-through em `put`/`get`) | **1 failed, 120 passed** — `test_worm_store_read_only_mode_is_not_integrity_against_the_owner`. É o comportamento **projetado**: o pin de divulgação fica vermelho quando a classe passa a resistir ao próprio UID, e a resposta certa é reescrever a divulgação **para cima** |
| D12 | F4 | re-içar `from …audit_postgres import schema_for_tenant` para escopo de módulo | **2 failed, 119 passed** — `test_anchor_module_imports_only_pure_names_from_the_audit_modules` (o pin é por ESCOPO) e `test_importing_the_anchor_module_does_not_drag_in_asyncpg`, cuja saída de interpretador limpo volta a `'34 True True'` (marginal 34, `asyncpg` presente, `audit_postgres` presente) contra `'1 False False'` no estado reparado |

**Achado da primeira sonda D1 (reparado, commit `d292af6`):**
`test_every_checkpoint_field_changes_the_root[...]` permanecia **VERDE** sob exatamente a mutação
que existe para pegar. Ele comparava contra `_PINNED_ROOT`; ao remover a chave do mapping, a raiz de
base **também** muda, então `mutado != pinado` continuava verdadeiro enquanto os dois checkpoints
passavam a hashar **identicamente**. A base agora é recomputada, e a comparação é entre duas raízes
vivas. Canário que não pode falhar não é canário.

**Nota de forma da sonda D9 — corrigida pelo reparo F4.** A redação original dizia: importar
`audit_anchor` de dentro de `gateway/audit_postgres.py` produz `ImportError` de import circular (a
âncora importava `schema_for_tenant` de lá), recusa estrutural mais forte que a asserção mas que
aborta a coleta antes do teste rodar; a sonda foi então refeita com um importador acíclico
(`gateway/custody.py`).

**Isso deixou de valer.** Com o import preguiçoso do F4, `audit_anchor` não importa mais
`audit_postgres` em escopo de módulo, então **não há ciclo**: re-executada agora, a sonda importa
limpo (`import maezo.gateway.audit_postgres` OK) e a cerca faz o seu trabalho — **1 failed, 120
passed**, `test_no_production_module_imports_the_anchor_writer` apontando
`gateway/audit_postgres.py`. A asserção passou a cobrir o caso que antes só o interpretador cobria,
e a cobertura ficou **mais forte**, não mais fraca: um `ImportError` de ciclo é acidente de
topologia de import, não uma cerca — some assim que a topologia muda, exatamente como acabou de
acontecer.

## 8. Handoff para as pernas 2 e 3

- **Perna 2 (job de verificação/comparação contínua) — ENTREGUE, ver §9.** O contrato que ela
  **DEVE** honrar, em imperativo porque é a propriedade de segurança inteira, não uma descrição:
  - O job **DEVE RECOMPUTAR** a raiz ancorada como `sha256(canonical_bytes(envelope["checkpoint"]))`
    e comparar o banco **contra essa recomputação**, sempre, e **NUNCA** contra
    `envelope["root"]`. O campo `root` armazenado **NÃO é coberto pela assinatura** (a assinatura
    cobre os bytes do CHECKPOINT — §4), então quem consegue editar o arquivo de âncora o define
    como quiser: um job que comparasse contra ele entregaria veredito limpo pelo preço de uma
    edição de string. O campo pode ser lido **apenas** como INDICADOR de adulteração — num WORM
    real ele não pode mudar, então divergir dele **DEVE** ser reportado alto, nunca ignorado.
  - O job **DEVE** verificar a assinatura via `AnchorSignatureVerifier` **antes** de qualquer coisa
    a jusante, e **DEVE** tratar assinatura rejeitada — ou verificador que levanta — como tamper,
    nunca como limpo. O Protocol de verify é separado do de sign exatamente para que o job não
    receba autoridade de assinatura de que não precisa.
  - O job **DEVE** recomputar o lado do banco pelo **mesmo caminho do escritor**
    (`checkpoint_for_chain` + `checkpoint_root`) e **NÃO PODE** carregar canonicalizador próprio:
    uma segunda cópia deriva dos bytes sobre os quais as assinaturas foram feitas, e
    canonicalizador derivado alarma em cadeia sã — pior que verificador nenhum, porque o primeiro
    falso alarme é o último em que alguém acredita.
  - O job **NÃO PODE** repousar veredito limpo numa listagem de store: `list_keys()` e `get()`
    discordam na presença de diretório symlinkado (§9), e listagem de object store real é
    eventualmente consistente. `test_enabled_writer_seals_a_verifiable_anchor` executa a sequência
    de assinatura ponta a ponta, mas **não** cobre a seleção da âncora — essa é a parte perigosa.
  - Ao landar, declarar o novo importador na allowlist HARDCODED de
    `test_no_production_module_imports_the_anchor_writer`. **Feito:** entrada única
    `gateway/audit_anchor_verify.py`.
- **Perna 3 (drills de tamper/restore):** alterar, remover, forkar e reconstruir registros contra um
  Postgres vivo, e provar que a comparação com a âncora externa detecta cada caso. Marcador
  `@pytest.mark.integration` já existente é o lugar. Consumir a saída JSON do CLI da perna 2
  (§9.4) — **última linha do stdout**, uma linha só — e os exit codes por classe de outcome.
- **Aberto para o dono (fora de qualquer perna de agente):** custódia/rotação da chave KMS/HSM
  (item já listado em ADR-0029 "Open items"); o bucket WORM + conta separada; a cadência do job
  periódico; e a decisão sobre a emenda redacional proposta em §3 acima. §9.6 acrescenta os que a
  perna 2 levantou.

---

## 9. Perna 2 — o job de verificação contínua (INERTE)

Artefatos: `src/maezo/gateway/audit_anchor_verify.py`,
`tests/unit/gateway/test_audit_anchor_verify.py` (121 testes).

### 9.1 As duas regras estruturais

**RECOMPUTAR, NUNCA CONFIAR** — o contrato imperativo está em §8 e é honrado literalmente: a base
de comparação é `sha256(canonical_bytes(envelope["checkpoint"]))`, recomputada do checkpoint cuja
assinatura acabou de ser verificada. `envelope["root"]` é lido **só** como indicador de adulteração
(num WORM real não pode mudar) e divergir dele é `DIVERGENCE/ANCHOR_ROOT_FIELD_INCONSISTENT`. O
lado do banco é recomputado pelo **mesmo caminho do escritor** — `checkpoint_for_chain` +
`checkpoint_root` sobre `AuditRecord`s remontados de linhas com `audit_postgres._row_to_record`
(importado, nunca recopiado: mesmo argumento anti-drift de `schema_for_tenant` na perna 1). O
módulo **não tem canonicalizador próprio**: existe exatamente um `sha256` nele e seu argumento é o
preimage da perna 1, pinado por AST.

**LISTAGEM É INDICATIVA; VEREDITO LIMPO NUNCA REPOUSA NELA.** Ver 9.2.

### 9.2 O problema da "âncora mais recente" e a posição tomada

"Verificar contra a âncora mais recente" pressupõe que dá para **achar** a mais recente. Não dá, em
geral, por três razões independentes:

| # | causa | medido? |
| --- | --- | --- |
| 1 | (v1) O envelope **não apontava para o predecessor**. **G1 acrescentou o elo `prev_anchor_root`** (v2), então a evidência agora liga cada âncora à anterior; o resíduo é o outro sentido — nada dentro de uma âncora assinada diz "e não existe outra **depois**", logo a truncagem da MAIS NOVA continua sendo propriedade da LISTAGEM do store, não da evidência | estrutural, §4; elo in-band FEITO (§9.2) |
| 2 | `list_keys()` (perna 1) usa `Path.rglob`, que **não desce em diretório symlinkado**, enquanto `get()`/`put()` resolvem através dele (só recusam sair da raiz) | **sim** — `test_labeled_fake_store_listing_omits_what_get_serves_through_a_symlinked_directory`: com `<root>/amh -> <root>/physical`, `get("amh/<x>")` serve o payload e `list_keys()` devolve `("physical/<x>",)`, string diferente |
| 3 | Object store real tem o mesmo buraco por outra causa: `ListObjectsV2` é eventualmente consistente, então uma âncora recém-escrita pode legitimamente faltar numa listagem de um segundo depois | conhecido |

A direção perigosa é uma só: **a âncora omitida pode ser a MAIS NOVA**, e aí o job confere o banco
contra uma âncora **VELHA** e diz MATCH. O cenário completo (que o teste monta): T1 atesta 1..3, T2
atesta 1..5, um ator deleta os registros 4 e 5 e esconde T2 da listagem — verificar contra T1 é
genuína e corretamente limpo, e a deleção **desaparece**.

**Posição.** A seleção é corroborada por uma **segunda enumeração independente** — seam
`AnchorKeyProbe`, **OBRIGATÓRIO** (sem default: um default seria degradação silenciosa exatamente
da propriedade defendida). As duas enumerações são restritas ao prefixo do tenant e comparadas por
**igualdade de conjunto**:

- discordância em **qualquer** direção ⇒ `STORE_LISTING_SUSPECT`, e **nenhum** veredito sobre o
  banco é emitido. Duas enumerações independentes que discordam significam que ao menos uma está
  errada, e verificador que escolhe um lado está **chutando**;
- store que não lista, ou probe que não sonda, é igualmente SUSPECT e nunca limpo;
- chave que a listagem nomeou e o `get` não serve é a mesma classe de mau comportamento.

`FilesystemAnchorKeyProbe` (companheiro do fake da perna 1) é independente **por construção**:
`os.walk(followlinks=True)` contra `Path.rglob` — é a diferença de semântica de symlink que o
permite ver o que a listagem perde. `followlinks=True` é foot-gun (ciclo ⇒ recursão infinita), então
cada diretório é identificado por `(st_dev, st_ino)` e podado ao repetir: store com ciclo produz
enumeração **finita** (e então discordante, logo SUSPECT) em vez de **pendurar** o job.

**Limite divulgado.** Corroborador construído sobre a **mesma** primitiva da listagem é vácuo, e o
módulo **não consegue impor** independência — só exigir que exista uma segunda fonte. Num
deployment real o dono precisa wirar um corroborador independente no mesmo espírito (manifesto de
S3 Inventory contra `ListObjectsV2` vivo, ou visão read-only de uma segunda conta). É premissa
divulgada, não propriedade provada aqui.

**Reparo in-band FEITO (G1, DARK):** encadear as próprias âncoras — o campo assinado
`prev_anchor_root` (bump `ANCHOR_FORMAT` v1→v2) torna a omissão detectável **na evidência** em vez
de só na camada de storage. O verify job caminha os elos **genesis-first** e reporta um elo
omitido/forkado/sem-genesis como `ANCHOR_CHAIN_BROKEN` (§9.4), sem emitir veredito sobre o banco.
Complementa — não substitui — a sonda de corroboração: a truncagem da âncora **MAIS NOVA** continua
fora do alcance in-band (nenhum elo para trás a vê) e segue delegada à retention-lock do storage.
Permanece atrás das flags default-OFF.

### 9.3 A flag IRMÃ, e por que não a do escritor

`MAEZO_AUDIT_ANCHOR_VERIFY_ENABLED`, default OFF, mesmo vocabulário truthy da perna 1
(**importado**, não recopiado — dialeto por flag faria a ativação do operador se aplicar pela
metade). Deliberadamente um **segundo** interruptor: (a) os dois lados são separadamente
implantáveis e separadamente **privilegiados** (sign vs verify — a perna 1 separou os Protocols
exatamente para isso); (b) "ancorando, ainda não verificando" é estado intermediário **real**, e
uma flag só faria a primeira ativação iniciar um laço de leitura de banco contra um store vazio;
(c) este lado consome recursos que o escritor não consome (conexão Postgres por rodada, leitura de
todas as linhas do tenant, e — quando wirado — paginar um humano). Com a flag off,
`verify_latest_anchor` retorna `DISABLED` **antes** de listar, **antes** de ler byte do store e
**antes** de abrir conexão; `run_verification_loop` retorna **sem nunca dormir** (feature desligada
não deve ser dona de uma task).

### 9.4 Vocabulário fechado de outcomes (10), com exit code, evento e nível

| status | exit | evento structlog | nível | significado |
| --- | --- | --- | --- | --- |
| `MATCH` | 0 | `audit_anchor_verification_match` | info | **o único veredito limpo** |
| `DIVERGENCE` | 10 | `audit_anchor_divergence_detected` | error | raiz recomputada ≠ ancorada; ou a janela ancorada não existe mais (fork, deleção, elo quebrado, campo `root` editado) |
| `NO_ANCHOR` | 11 | `audit_anchor_absent` | warning | nada foi atestado ainda |
| `SIGNATURE_INVALID` | 12 | `audit_anchor_signature_invalid` | error | tratado como tamper (idioma ADR-0029 §2); inclui verificador que levanta |
| `STORE_LISTING_SUSPECT` | 13 | `audit_anchor_store_listing_suspect` | error | §9.2 |
| `DB_ERROR` | 14 | `audit_anchor_database_unreadable` | error | cadeia ilegível é cadeia **não verificada** |
| `ANCHOR_UNREADABLE` | 15 | `audit_anchor_envelope_unreadable` | error | bytes que não são âncora usável deste tenant |
| `DISABLED` | 16 | `audit_anchor_verification_skipped_disabled` | debug | **"não rodou" nunca é "rodou e passou"** |
| `ANCHOR_CHAIN_BROKEN` | 17 | `audit_anchor_chain_broken` | error | evidência assinada não é UMA cadeia in-band contígua (elo omitido/forkado/sem genesis); **nenhum** veredito sobre o banco (G1) |
| `SCHEMA_VERSION_MISMATCH` | 18 | `audit_anchor_schema_version_mismatch` | error | conteúdo bateu, mas o `chain_schema_version` **assinado** da âncora ≠ revisão alembic **VIVA** do tenant (G1) |

Os **quatro** últimos **estendem** os seis do brief da perna 2, seguindo a regra do próprio brief
(cada estado de ausência ou discordância estrutural é um outcome): envelope truncado não é
assinatura forjada e job desligado não é job aprovado (`ANCHOR_UNREADABLE`/`DISABLED` — mandam o
operador para incidentes diferentes: integridade de storage vs comprometimento de chave); e **G1**
acrescentou `ANCHOR_CHAIN_BROKEN` (a evidência assinada não é uma cadeia in-band contígua — uma
afirmação sobre a CUSTÓDIA, não sobre o banco) e `SCHEMA_VERSION_MISMATCH` (o conteúdo bateu, mas a
âncora atesta uma revisão de migração que o banco não tem mais). Exit codes começam em **10** de propósito: `1` é traceback e `2` é erro de uso
do argparse, e nenhum dos dois pode virar veredito; `3` é a recusa de wiring do CLI.

`reason` estreita o status dentro de um vocabulário igualmente fechado (`REASON_TO_STATUS`), nunca
prosa livre. PHI: outcome e evento carregam hashes, contagens, **chaves** de âncora, key id de
assinatura e **nome de classe** de exceção — nenhum conteúdo de registro. A **mensagem** da exceção
de banco é deliberadamente descartada: erro de driver cita o DSN, e DSN carrega senha.

**CLI offline** (`python -m maezo.gateway.audit_anchor_verify --dsn --tenant --store-root
--fake-verifier-key-label [--fake-verifier-secret-env]`): roda sem a stack docker. Contrato de
saída: o veredito é a **ÚLTIMA LINHA do stdout, e é uma linha só** (JSON compacto) — este comando
emite evento estruturado do próprio veredito e o repo renderiza structlog em **stdout**
(`platform/observability.py`, `PrintLoggerFactory`), então documento JSON multi-linha intercalado
com linhas de console **não parseia**. `... | tail -n 1 | jq` é o contrato, pinado por teste.
O único verificador que o CLI sabe wirar é o **fake rotulado**: chave real é wiring do dono,
injetado programaticamente, e uma flag `--kms-key-arn` aqui convidaria exatamente a masquerade que
os três rótulos da perna 1 existem para impossibilitar. Sem segredo, o CLI **RECUSA** (exit 3) —
nunca cai para "pular a verificação de assinatura".

### 9.5 Controles RED — sondas executadas (mutar → vermelho → reverter)

Mesma higiene da §7: `PYTHONDONTWRITEBYTECODE=1`, purge de `__pycache__`, commit antes de sondar.
Base verde: **121 + 121 = 242** testes nos dois arquivos.

| # | defesa | neutralização aplicada | resultado observado |
| --- | --- | --- | --- |
| P1 | V1 recomputar | `anchor_root = anchor.stored_root` (confiar no campo armazenado) | **2 failed, 120 passed** — `test_an_envelope_root_edited_to_match_the_database_is_still_not_clean` e `test_the_verify_module_holds_no_canonicalization_of_its_own` (o pin de AST viu o `sha256` sumir) |
| P2 | V3 corroboração | deletar o ramo `listed != probed` | **2 failed, 120 passed** — `..._a_newer_anchor_invisible_to_the_listing_does_not_produce_a_clean_verdict` (`AssertionError: assert 'MATCH' == 'STORE_LISTING_SUSPECT'` — **o veredito limpo silencioso, reproduzido**) e `..._a_listing_that_invents_a_key_the_probe_cannot_see_is_also_suspect` |
| P3 | detecção de divergência | `if database_root != database_root:` (comparação sempre concorda) | **4 failed, 239 passed** — `..._a_rewritten_chain_that_the_in_database_verifier_calls_valid_is_caught`, `test_reordered_records_are_caught`, `test_divergence_event_carries_no_record_content`, `test_loop_keeps_verifying_after_a_divergence` |
| P4 | V6 flag | remover o early-return de `anchor_verification_enabled()` em `verify_latest_anchor` | **3 failed, 118 passed** — `..._disabled_verification_touches_no_seam_and_no_filesystem`, `..._disabled_verification_creates_nothing_with_the_real_seams`, `..._cli_reports_disabled_rather_than_pretending_to_have_checked`. `test_loop_stops_when_the_flag_is_turned_off_mid_flight` foi **deselecionado**: sem a checagem por iteração ele roda **para sempre** (`max_iterations=None`) — evidência a mais, não a menos |
| P5 | V12 independência do probe | `os.walk(self._root)` sem `followlinks=True` (a primitiva da própria listagem) | **1 failed, 121 passed** — `test_filesystem_probe_sees_the_anchor_the_listing_misses` |
| P6 | V9 cerca | `gateway/custody.py`: `from maezo.gateway import audit_anchor_verify` | **2 failed, 241 passed** — `test_no_production_module_imports_the_verify_job` apontando `gateway/custody.py`, **e** `test_importing_the_anchor_module_does_not_drag_in_asyncpg` da perna 1: um importador de produção do job puxa `audit_postgres`/`asyncpg` via `gateway/__init__.py`, então as duas cercas escuras têm tripwires que se sobrepõem |

### 9.6 Aberto para humanos (a perna 2 levanta, não resolve)

- **Cadência.** O laço aceita `interval_seconds` e não escolhe um. A escolha é trade-off de custo
  de leitura vs janela de detecção, e depende da cadência do escritor (§7 da auditoria externa).
- **Sink de alerta.** `on_outcome` é um callback e o módulo **não** decide supressão: divergência
  não se resolve sozinha, então o laço **continua** alarmando (parar silenciaria o alarme depois da
  primeira página). Quem decide de-dup/escalada é o dono.
- **Distribuição de chave de verificação.** O job precisa só da metade **pública**/permissão
  `verify`; o escritor precisa de `sign`. Separar as duas identidades IAM é wiring do dono e o item
  de custódia/rotação já listado em ADR-0029 "Open items" passa a valer para os dois papéis.
- **Encadeamento de âncoras (`prev_anchor_root`) — FEITO (G1, DARK).** §9.2: o reparo in-band do
  problema da "mais recente" foi construído (bump `ANCHOR_FORMAT` v1→v2; elo assinado caminhado
  genesis-first → `ANCHOR_CHAIN_BROKEN`, §9.4). Levantado aqui pela perna 2, resolvido por G1;
  permanece atrás das flags default-OFF.
- **Versão de schema — FEITO (G1, DARK).** A recomputação usa o `chain_schema_version` **da âncora**
  (assinado) para isolar a comparação ao CONTEÚDO da cadeia; a cross-check contra o `alembic_version`
  **vivo** do tenant ("âncora atesta um schema que não é mais o do banco") foi construída como
  `SCHEMA_VERSION_MISMATCH` (§9.4), rodando **por último** para nunca mascarar uma divergência de
  conteúdo real.

---

## 10. Perna 3 — drills de tamper/restore contra Postgres VIVO (ENTREGUE)

Artefato: `tests/unit/gateway/test_audit_anchor_drills_live_pg.py` (9 provas — 1 controle + 7 drills
de tamper + 1 CLI ponta-a-ponta; G1 acrescentou os drills 6–7, marcador `@pytest.mark.integration`,
**skip-loudly**).

### 10.1 O que estas provas acrescentam à suíte da perna 2

A suíte da perna 2 (`test_audit_anchor_verify.py`) dirige `verify_latest_anchor` por um
`_RowRecordSource` em memória — pina toda a árvore de decisão, mas o caminho linha→registro→recompute
é uma **reconstrução** do que o Postgres faz. A perna 3 roda o **MESMO** `verify_latest_anchor`
contra o `PostgresChainRecordSource` **real**, lendo uma tabela `audit_chain` **real**, semeada pelo
`PostgresAuditSink.emit()` **real**, ancorada pelo `write_anchor` **real** da perna 1, e então
ADULTERADA por SQL cru contra o banco vivo. Cada drill é a ameaça da auditoria §4 tornada real, e
cada um prova que a comparação com a âncora atinge o outcome ALTO correto — com um CONTROLE
(sem tamper) provando MATCH primeiro (um verificador que só alarma não detecta nada).

O mecanismo que torna um edit preguiçoso de conteúdo detectável: o caminho linha→registro
**RECOMPUTA** `record_hash` do conteúdo armazenado (`audit_postgres._row_to_record` →
`AuditRecord.__post_init__`), enquanto `snapshot_from_rows` caminha pelos elos **ARMAZENADOS**. Numa
linha honesta os dois coincidem; numa editada, não.

### 10.2 Os sete drills e o guarda que dispara (observado VIVO)

| # | drill (tamper) | outcome observado (verify job) | qual guarda dispara |
| --- | --- | --- | --- |
| 1 | ALTER meio (edita `decision_basis`, `record_hash` fica velho) | `DIVERGENCE / CHAIN_DISCONTINUITY` | âncora **e** `verify_chain` (hash mismatch) |
| 2 | ALTER cabeça (mesmo edit na linha cauda) | `DIVERGENCE / ROOT_MISMATCH` | âncora **e** `verify_chain` |
| 3 | REMOVE cauda (deleta o registro mais novo) | `DIVERGENCE / RECORD_COUNT_SHORTFALL` (db_n=4<5, `database_root=None`) | **SÓ a âncora** — `verify_chain` diz VALID (a cadeia truncada segue perfeitamente encadeada) |
| 4 | FORK (duas linhas com o mesmo `prev_record_hash`) | Parte 1: `UniqueViolationError(uq_audit_chain_prev_hash)` — o DB recusa o insert. Parte 2 (constraint dropada): `DIVERGENCE / CHAIN_FORK` | Parte 1 = guarda **do DB** (migração 0002); Parte 2 = guarda **do verify job** |
| 5 | RECONSTRUCT (reescrita íntegra e auto-consistente) | `verify_chain` diz **VALID** (enganado — asserido PRIMEIRO), depois âncora `DIVERGENCE / ROOT_MISMATCH` | **SÓ a âncora externa** — o adversário-alvo da §4 |
| 6 | THIN THE LEDGER (G1, Unit B): 3 âncoras encadeadas, a do **MEIO** deletada do store | `ANCHOR_CHAIN_BROKEN / ANCHOR_CHAIN_LINK_MISSING`, **nenhum** veredito sobre o banco | **SÓ o `prev_anchor_root` assinado** — conteúdo honesto, objeto genuinamente ausente, a sonda de corroboração não dispara |
| 7 | SCHEMA DRIFT (G1b): move a linha `<tenant>_alembic_version` DEPOIS de selar a âncora | `SCHEMA_VERSION_MISMATCH` | cross-check da versão **assinada** vs `alembic_version` **vivo** — conteúdo intocado, os dois roots ainda batem |

Cada tamper é pareado com seu CONTROLE (mesmo setup, sem tamper → MATCH) na mesma prova; o drill 5
exibe `verify_chain().valid is True` na falsificação ANTES de a âncora pegá-la (a barra de
não-vacuidade: se o check só-do-DB não fosse genuinamente enganado, pegá-la depois não provaria
nada). A **9ª prova** exercita o **CLI offline da perna 2** ponta-a-ponta (handoff §8): exit 0 +
veredito `MATCH` na última linha do stdout na cadeia honesta, depois exit 10 + veredito `DIVERGENCE`
após deletar a cauda — o contrato §9.4 (veredito = última linha, uma linha; exit code codifica a
classe).

### 10.3 Isolamento live-PG e skip-loudly (por que estas provas não dependem de container ad-hoc)

Mecanismo de skip espelha `test_a2a_edge_live_pg.py`: conecta a um DSN dedicado com timeout de 2s e
`pytest.skip` ALTO se inalcançável — CI e qualquer outro ambiente pulam limpo. O DSN default aponta
uma porta DEDICADA e NÃO-PADRÃO (**5466**), deliberadamente fora de {5432, 5433, 5643} — subir (ou
não) o Postgres descartável desta suíte nunca faz a suíte live-PG de outra atacar um banco meio-pronto.
Cada drill recebe seu PRÓPRIO esquema de tenant recém-migrado (function-scoped, porque cada drill
muta a cadeia destrutivamente), dropado no teardown. Override: `MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL`.

`audit.py` e `audit_postgres.py` permanecem **byte-idênticos** à origin/main em todo o branch
(doutrina aditiva) — os drills EXERCITAM `UNIQUE(prev_record_hash)` + os invariantes de hash-chain,
nunca os modificam.

### 10.4 Transcrição live (verbatim) — CONTROLE (verde) e TAMPER (incidente detectado)

Contra `pgvector/pgvector:pg16` em `localhost:5466` (fora dos comandos de log estruturado):

```text
[ALTER_MIDDLE] CONTROL  -> {"status":"MATCH","reason":null,"anchored_n":5,"db_n":5,"anchor_root":"96d0b0ee3ea9","db_root":"96d0b0ee3ea9"}
[ALTER_MIDDLE] TAMPERED -> {"status":"DIVERGENCE","reason":"CHAIN_DISCONTINUITY","anchored_n":5,"db_n":5,"db_root":null}  | verify_chain.valid=False
[ALTER_HEAD]   CONTROL  -> {"status":"MATCH","reason":null,"anchored_n":5,"db_n":5,"anchor_root":"f36a611ab42a","db_root":"f36a611ab42a"}
[ALTER_HEAD]   TAMPERED -> {"status":"DIVERGENCE","reason":"ROOT_MISMATCH","anchored_n":5,"db_n":5,"anchor_root":"f36a611ab42a","db_root":"8b10304eb77a"}  | verify_chain.valid=False
[REMOVE_TAIL]  CONTROL  -> {"status":"MATCH","reason":null,"anchored_n":5,"db_n":5,"anchor_root":"163d98350e4a","db_root":"163d98350e4a"}
[REMOVE_TAIL]  TAMPERED -> {"status":"DIVERGENCE","reason":"RECORD_COUNT_SHORTFALL","anchored_n":5,"db_n":4,"db_root":null}  | verify_chain.valid=True  (anchor-only catch)
[FORK]         CONTROL  -> {"status":"MATCH","reason":null,"anchored_n":5,"db_n":5,"anchor_root":"86d4133caf29","db_root":"86d4133caf29"}
[FORK]         PART1    -> DB GUARD fired: UniqueViolationError (uq_audit_chain_prev_hash)
[FORK]         PART2    -> {"status":"DIVERGENCE","reason":"CHAIN_FORK","anchored_n":5,"db_n":6,"db_root":null}  | verify_chain.valid=False
[RECONSTRUCT]  CONTROL  -> {"status":"MATCH","reason":null,"anchored_n":5,"db_n":5,"anchor_root":"70d52f37dd09","db_root":"70d52f37dd09"}
[RECONSTRUCT]  TAMPERED -> {"status":"DIVERGENCE","reason":"ROOT_MISMATCH","anchored_n":5,"db_n":5,"anchor_root":"70d52f37dd09","db_root":"63e80893dba9"}  | verify_chain.valid=True  (DB-only fooled, then anchor catches)
```

Suíte comitada, verde contra 5466: `7 passed in 4.89s`. Skip-loudly (DSN → porta morta):
`7 skipped` — cada um com a razão ALTA `COULD NOT VERIFY: Postgres not reachable ...`.

### 10.5 Decisão sobre o polimento tenant-mismatch (achado menor da GK-B)

GK-B notou que uma âncora validamente-assinada-para-tenant-B sob o prefixo do tenant-A é classada
`ANCHOR_UNREADABLE / CHECKPOINT_TENANT_MISMATCH` (`_parse_envelope` recusa antes de ler o banco), o
que subestima uma tentativa de *plantio* de âncora cross-tenant — mas a **saída de segurança está
correta** (negação, nunca MATCH); só o rótulo é cosmético. **Decisão: DEIXAR como está.** Mover para
uma classe de incidente "mais limpa" tocaria o vocabulário FECHADO de `audit_anchor_verify.py`
(`ALL_STATUSES`, `REASON_TO_STATUS`, e as tabelas exit/evento/nível), todos pinados por
igualdade-de-conjunto — um ripple que a perna 3 não deve introduzir num branch escuro cujas
âncoras/verify já estão GK-clareadas. `audit_postgres.py` não seria tocado de qualquer forma; o custo
está no vocabulário da perna 2. Divulgado, não forçado (o brief manda não forçar).

### 10.6 Aberto para o dono (a perna 3 herda, não resolve)

- **KMS/HSM real + WORM real.** Os drills usam os seams FAKE rotulados; a durabilidade e a
  retention-lock que tornam a âncora *evidência* são wiring do dono (§5, §8, ADR-0029 open items).
- **Split IAM sign/verify.** O escritor precisa de `sign`, o job só de `verify` — duas identidades.
- **`prev_anchor_root` — FEITO (G1, DARK).** O encadeamento in-band fechou o problema da "mais
  recente" (§9.2): bump `ANCHOR_FORMAT` v1→v2, elo assinado, `ANCHOR_CHAIN_BROKEN`. Herdado pela
  perna 3 como o drill 6 (THIN THE LEDGER).
- **Cross-check de schema-version — FEITO (G1, DARK).** A comparação do `chain_schema_version` da
  âncora contra o `alembic_version` vivo (§9.6) foi construída (`SCHEMA_VERSION_MISMATCH`) e é
  exercitada VIVA pelo drill 7 (SCHEMA DRIFT).
