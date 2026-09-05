"""Cercas estruturais da migration `0009_drop_pgvector` — a remocao da camada semantica (DU-01-b).

Sem Postgres. A prova VIVA (upgrade/downgrade round-trip contra `postgres:16` E contra
`pgvector/pgvector:pg16`, incluindo a ordem entre tenants) foi executada na entrega e esta citada
no `docs/evidence-ledger.md`; o que se fixa aqui e' o conjunto de coisas que precisam continuar
verdadeiras no DDL para que uma edicao futura que quebre uma delas fique vermelha em CI em vez de
em producao.

Cinco invariantes:

1.  **Integridade da cadeia.** 0009 revisa 0008 e e' pai de exatamente UM filho. A 0009 deixou de
    ser a cabeca quando a `0010_webhook_wamid_dedup` pousou, entao a assercao de CABECA UNICA /
    sem-fork seguiu com a cabeca para `test_migration_0010_webhook_wamid_dedup.py` — exatamente o
    que o docstring desta suite mandava ("a cabeca pertence a quem POR ORA e' a cabeca, entao uma
    futura 0010 muda UM arquivo (este), nao a suite anterior"). O que fica aqui e' a metade que e'
    sobre a PROPRIA 0009: ela revisa a 0008 e nenhuma outra migration reivindica a 0009 como pai,
    o que torna um fork sobre ESTE no impossivel enquanto a cabeca segue adiante.

2.  **O DROP da extensao e' GUARDADO, e as duas formas ingenuas sao PROIBIDAS POR NOME.** A
    extensao `vector` mora em `public` e e' DB-GLOBAL (DL-0017), mas esta migration roda uma vez
    POR SCHEMA DE TENANT. `DROP EXTENSION` puro quebraria a migration do tenant A por causa do
    schema do tenant B; `CASCADE` derrubaria a coluna do tenant B antes de a migration DELE rodar.
    A unica forma correta e' condicionar o DROP a nao restar nenhuma coluna de tipo `vector` no
    banco — e' isso que se afirma aqui, nas duas direcoes (a guarda existe; o CASCADE nao).

3.  **A 0001 parou de exigir pgvector.** Sem isso o primeiro passo de `alembic upgrade head` num
    servidor sem a extensao (a imagem `postgres:16` que o compose passou a usar) morre DENTRO da
    0001, onde migration nenhuma pode consertar. A guarda e' convergente: com pgvector o estado
    final da 0001 e' o mesmo de antes, sem pgvector nada e' criado, e os dois caminhos chegam ao
    mesmo head.

4.  **`downgrade()` e' CONVERGENTE com a 0001 — a MESMA guarda de disponibilidade.** Recriar a
    extensao e a coluna sob `IF EXISTS (pg_available_extensions ...)`. A versao original desta
    suite afirmava o contrario ("sem guarda, para falhar alto"), e estava errada: num servidor sem
    pgvector a guarda da 0001 faz com que a revisao 0008 daquele servidor NAO tenha a coluna
    `embedding`, entao um downgrade sem guarda nao restaura "a coluna que a 0008 tem" — ele morre
    tentando construir algo que a 0008 nunca teve ali, e derruba a unica prova de rollback real do
    repositorio (`tests/unit/platform/integrations/test_amh_inbox_live_pg.py::
    test_upgrade_downgrade_upgrade_roundtrip`) contra a imagem `postgres:16` que este mesmo PR poe
    no compose.

5.  **Nada mais e' tocado.** `upgrade()` e `downgrade()` nomeiam `agent_memory` e a extensao
    `vector`, e nenhuma outra relacao das migrations 0001..0008.

PROVENIENCIA das tabelas em `_PRE_EXISTING_TABLES`: e' a mesma lista que a suite da 0008 usa,
acrescida de `a2a_fact_outbox` (criada pela propria 0008) e de `agent_memory` — que e' a excecao
DELIBERADA, porque a 0009 precisa nomea-la para alterar a coluna.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src/maezo/platform/migrations/versions"
_MIGRATION_PATH = _VERSIONS_DIR / "0009_drop_pgvector.py"
_SOURCE = _MIGRATION_PATH.read_text(encoding="utf-8")
_SOURCE_0001 = (_VERSIONS_DIR / "0001_schema_agents.py").read_text(encoding="utf-8")

_REVISION = "0009"

#: Relacoes criadas por 0001..0008 que a 0009 NAO pode nomear. `agent_memory` fica de fora
#: porque alterar essa tabela e' precisamente o trabalho desta migration.
_PRE_EXISTING_TABLES: tuple[str, ...] = (
    "agent_checkpoints",
    "agent_checkpoint_writes",
    "audit_chain",
    "a2a_idempotency",
    "driver_idempotency",
    "custody_bundles",
    "erasure_log",
    "audit_emit_dedup",
    "amh_inbox",
    "a2a_fact_outbox",
)


def _emitted_sql(source: str) -> str:
    """So o SQL que `op.execute` executa — nunca a prosa do docstring.

    Mesmo helper (e mesma razao) da suite da 0008: o docstring desta migration NOMEIA
    `DROP EXTENSION ... CASCADE` para explicar por que essa forma esta errada, e uma cerca que
    lesse prosa como SQL seria satisfeita apenas apagando a explicacao. Comentarios SQL `--` sao
    removidos pelo mesmo motivo, um nivel abaixo.
    """
    sql = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', source, re.DOTALL))
    sql += "".join(re.findall(r'op\.execute\("(.*?)"\)', source))
    return re.sub(r"--[^\n]*", "", sql)


def _upgrade_sql() -> str:
    body = _SOURCE[_SOURCE.index("def upgrade()") : _SOURCE.index("def downgrade()")]
    return _emitted_sql(body)


def _downgrade_sql() -> str:
    return _emitted_sql(_SOURCE[_SOURCE.index("def downgrade()") :])


# ---------------------------------------------------------------------------
# 0. O extrator nao e' vacuo
# ---------------------------------------------------------------------------


def test_the_prose_stripper_is_not_vacuous() -> None:
    """Controle negativo: o docstring CONTEM as formas que as cercas proibem (ele as nomeia para
    dizer por que estao erradas), e o SQL emitido nao — logo o strip faz trabalho real."""
    assert "CASCADE" in _SOURCE, "o docstring deixou de explicar por que CASCADE esta errado"
    assert "CASCADE" not in _upgrade_sql() + _downgrade_sql()
    assert _upgrade_sql().strip(), "o extrator nao achou SQL nenhum no upgrade()"
    assert _downgrade_sql().strip(), "o extrator nao achou SQL nenhum no downgrade()"


# ---------------------------------------------------------------------------
# 1. Integridade da cadeia
# ---------------------------------------------------------------------------


def test_migration_file_is_named_for_its_revision() -> None:
    assert _MIGRATION_PATH.name == f"{_REVISION}_drop_pgvector.py"


def test_revision_and_down_revision() -> None:
    assert f'revision: str = "{_REVISION}"' in _SOURCE
    assert 'down_revision: str | None = "0008"' in _SOURCE


def test_0009_is_claimed_as_a_parent_by_exactly_one_migration() -> None:
    """A 0009 nao e' mais a cabeca: a cadeia agora e' 0008->0009->0010, e a assercao de cabeca
    unica / sem-fork foi COM a cabeca para `test_migration_0010_webhook_wamid_dedup.py`, como o
    docstring desta suite mandava (uma futura 0010 muda UM arquivo — este).

    O que se afirma aqui e' o que continua sendo sobre a 0009: exatamente UMA migration a
    reivindica como pai. Dois filhos aqui seriam um fork silencioso sobre este no.
    """
    children = []
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        down = re.search(r'^down_revision: str \| None = "([^"]+)"', text, re.MULTILINE)
        if down is not None and down.group(1) == _REVISION:
            children.append(path.name)
    assert children == ["0010_webhook_wamid_dedup.py"], (
        f"esperado 0010 como unico filho da 0009, veio {children}"
    )


# ---------------------------------------------------------------------------
# 2. A coluna sai por tenant; a extensao sai GUARDADA
# ---------------------------------------------------------------------------


def test_upgrade_drops_the_embedding_column_idempotently() -> None:
    """DDL nao qualificada (search_path do tenant, DL-0017) e `IF EXISTS` nas duas pontas: a
    tabela pode nao existir num schema recem-criado, e a coluna pode nunca ter sido criada num
    servidor sem pgvector."""
    sql = " ".join(_upgrade_sql().split())
    assert "ALTER TABLE IF EXISTS agent_memory DROP COLUMN IF EXISTS embedding" in sql


def test_upgrade_never_drops_the_extension_with_cascade() -> None:
    """CASCADE derrubaria a coluna de outro tenant antes de a migration dele rodar — um efeito
    colateral cross-tenant, nao uma migration."""
    assert "CASCADE" not in _upgrade_sql().upper()


def test_the_extension_drop_is_guarded_by_the_absence_of_any_remaining_vector_column() -> None:
    """A guarda que torna a ordem entre tenants irrelevante: o ULTIMO a migrar remove a extensao.

    Sem ela, `DROP EXTENSION` no primeiro tenant falharia com `dependent objects still exist`
    enquanto qualquer outro schema ainda tivesse a coluna.
    """
    sql = " ".join(_upgrade_sql().split())
    assert "DROP EXTENSION vector" in sql, "a extensao nao e' removida em lugar nenhum"
    assert "NOT EXISTS" in sql, "o DROP da extensao nao esta condicionado a coisa nenhuma"
    for probe in ("pg_attribute", "pg_class", "pg_type", "t.typname = 'vector'"):
        assert probe in sql, f"a guarda nao inspeciona {probe} — nao pode saber se restou coluna"
    assert "attisdropped" in sql, (
        "a guarda contaria colunas logicamente removidas (attisdropped) como dependentes vivas"
    )
    # A guarda e' sobre o BANCO inteiro, nao sobre o schema corrente: nenhum filtro por nspname.
    assert "nspname" not in sql, (
        "a guarda filtra por schema — a extensao e' DB-global (DL-0017) e a pergunta tambem tem "
        "de ser do banco inteiro, senao o primeiro tenant remove a extensao de todos"
    )


@pytest.mark.parametrize("table", _PRE_EXISTING_TABLES)
def test_no_relation_from_0001_to_0008_is_named(table: str) -> None:
    """`agent_memory` e' a unica relacao preexistente que esta migration pode nomear."""
    assert table not in _upgrade_sql()
    assert table not in _downgrade_sql()


# ---------------------------------------------------------------------------
# 3. A 0001 parou de exigir pgvector
# ---------------------------------------------------------------------------


def test_migration_0001_no_longer_hard_requires_the_vector_extension() -> None:
    """A guarda convergente da 0001. Sem ela a imagem `postgres:16` do compose nao migra:
    `FeatureNotSupportedError: extension "vector" is not available` DENTRO da 0001, antes de a
    0009 existir para o runner."""
    sql = " ".join(_emitted_sql(_SOURCE_0001).split())
    assert "pg_available_extensions" in sql, (
        "0001 voltou a criar a extensao incondicionalmente — `alembic upgrade head` deixa de "
        "funcionar em qualquer servidor sem pgvector, e nenhuma migration posterior pode salvar"
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public" in sql, (
        "a 0001 deixou de criar a extensao mesmo onde ela existe — bancos com pgvector deixariam "
        "de ter o estado historico que a 0009 remove"
    )
    assert "ADD COLUMN IF NOT EXISTS embedding" in sql, (
        "a coluna `embedding` sumiu da 0001 — a 0009 passaria a dropar algo que nunca existiu e "
        "o registro historico do schema estaria reescrito, nao emendado"
    )


def test_migration_0001_does_not_declare_the_vector_column_inline() -> None:
    """Controle da guarda acima: se a coluna voltar para o corpo da tabela, um tipo de extensao
    ausente derruba a criacao inteira e a guarda do ADD COLUMN vira decorativa."""
    body = _SOURCE_0001[_SOURCE_0001.index("agent_memory (") :]
    body = body[: body.index('"""')]
    assert "vector(1536)" not in body


def test_the_compose_stack_no_longer_pulls_the_pgvector_image() -> None:
    """A outra metade da decisao R-005: a imagem do compose. Fica AQUI e nao numa suite de deploy
    porque so faz sentido junto da guarda da 0001 — trocar a imagem sem a guarda quebra a lane de
    integracao inteira, e a guarda sem a troca nao entrega a decisao."""
    compose = (Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(encoding="utf-8")
    images = [line.split("image:", 1)[1].strip() for line in compose.splitlines() if "image:" in line]
    assert not [image for image in images if image.startswith("pgvector/")], images


#: Os DOIS arquivos de entrada do repositorio que descrevem a stack corrente em tempo PRESENTE.
#: Escopo deliberadamente fechado nestes dois: `docs/decisions-log.md` (DL-0017), `docs/archive/**`,
#: `PLANS.md` e as ADRs falam de pgvector como REGISTRO HISTORICO e continuam corretos.
_PRESENT_TENSE_STACK_DOCS: tuple[str, ...] = ("PROJECT.md", "README.md")


@pytest.mark.parametrize("doc", _PRESENT_TENSE_STACK_DOCS)
def test_the_entry_point_docs_do_not_advertise_pgvector_as_current_stack(doc: str) -> None:
    """Achado F3 do gatekeeper R1. `make dev-stack` e' `docker compose --profile core up -d`, cuja
    imagem de postgres a decisao R-005 trocou — mas `PROJECT.md:3/:54` e `README.md:74` continuavam
    anunciando "PostgreSQL+pgvector"/"postgres+pgvector" no PRESENTE.

    Nao e' cosmetico: `docs/architecture/overview.md:89` repete a linha de stack CITANDO
    `PROJECT.md` como fonte, entao corrigir uma e nao a outra deixava o proprio ramo com dois
    documentos em contradicao, o derivado certo e a fonte errada.
    """
    text = (Path(__file__).resolve().parents[3] / doc).read_text(encoding="utf-8")
    offenders = [
        f"{doc}:{number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        if "pgvector" in line
    ]
    assert not offenders, offenders


def test_the_stack_line_of_the_two_entry_docs_still_names_postgres() -> None:
    """Controle de nao-vacuidade do teste acima: apagar a mencao a banco nenhum tambem o deixaria
    verde. O que a decisao R-005 remove e' `pgvector`, nao o PostgreSQL."""
    for doc in _PRESENT_TENSE_STACK_DOCS:
        text = (Path(__file__).resolve().parents[3] / doc).read_text(encoding="utf-8")
        assert "ostgres" in text, f"{doc} deixou de nomear o PostgreSQL"


# ---------------------------------------------------------------------------
# 4. `downgrade()` e' CONVERGENTE com a guarda da 0001
# ---------------------------------------------------------------------------

#: O predicado de disponibilidade, escrito UMA vez aqui: 0001 e 0009-downgrade tem de fazer a
#: MESMA pergunta, senao "convergente" e' so uma palavra no docstring.
_AVAILABILITY_PREDICATE = "EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')"


def _downgrade_guarded_block() -> str:
    """O trecho do SQL do `downgrade()` que roda DENTRO da guarda de disponibilidade.

    Sem este recorte, uma guarda que envolvesse apenas o `CREATE EXTENSION` e deixasse o
    `ADD COLUMN ... public.vector(1536)` de fora satisfaria um `in`-simples e continuaria
    quebrando em `postgres:16` — que e' exatamente o defeito que esta secao existe para impedir.
    """
    sql = " ".join(_downgrade_sql().split())
    assert _AVAILABILITY_PREDICATE in sql, sql
    start = sql.index(_AVAILABILITY_PREDICATE)
    end = sql.index("END IF;", start)
    return sql[start:end]


def test_downgrade_recreates_both_the_extension_and_the_column() -> None:
    sql = " ".join(_downgrade_sql().split())
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public" in sql
    assert "ADD COLUMN IF NOT EXISTS embedding public.vector(1536)" in sql


def test_downgrade_carries_the_same_availability_guard_as_0001() -> None:
    """A cerca do defeito F1. Um `downgrade()` sem guarda deixa VERMELHA a unica prova de rollback
    real do repositorio (`test_amh_inbox_live_pg.py::test_upgrade_downgrade_upgrade_roundtrip`)
    contra a imagem `postgres:16` que este mesmo PR poe no compose — medido: `1 failed, 21 passed`
    sem a guarda, `22 passed` com ela.

    A guarda nao esconde reversibilidade nenhuma: num servidor sem pgvector a 0001 tambem nao cria
    a coluna, logo a 0008 DAQUELE servidor nao a tem e o downgrade guardado devolve exatamente a
    0008 dele.
    """
    assert _AVAILABILITY_PREDICATE in " ".join(_downgrade_sql().split()), (
        "downgrade() perdeu a guarda de disponibilidade — volta a morrer com "
        '`extension "vector" is not available` em qualquer servidor sem pgvector, inclusive na '
        "imagem `postgres:16` do compose (ver o docstring da migration)"
    )


def test_the_downgrade_guard_covers_the_column_and_not_only_the_extension() -> None:
    """Meia-guarda tambem quebra: `public.vector(1536)` nao resolve onde a extensao nao existe."""
    guarded = _downgrade_guarded_block()
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public" in guarded
    assert "ADD COLUMN IF NOT EXISTS embedding public.vector(1536)" in guarded


def test_the_downgrade_column_is_created_through_execute_not_parsed_inline() -> None:
    """Controle da guarda acima: o corpo de um bloco `DO` e' parseado INTEIRO antes de rodar, entao
    um `ALTER TABLE ... public.vector(1536)` literal dentro do `DO` falharia no parse mesmo com a
    guarda em volta. So `EXECUTE '<sql>'` adia a resolucao do tipo. Mesma razao da 0001."""
    guarded = _downgrade_guarded_block()
    column_stmt = guarded[guarded.index("ADD COLUMN IF NOT EXISTS embedding") :]
    prefix = guarded[: guarded.index("ADD COLUMN IF NOT EXISTS embedding")]
    assert "EXECUTE '" in prefix, column_stmt


def test_the_0001_and_0009_guards_ask_the_same_question() -> None:
    """Convergencia literal: se a 0001 mudar de predicado e a 0009 nao (ou vice-versa), os dois
    caminhos deixam de encontrar-se e esta cerca fica vermelha antes de o roundtrip quebrar."""
    sql_0001 = " ".join(_emitted_sql(_SOURCE_0001).split())
    assert _AVAILABILITY_PREDICATE in sql_0001, (
        "a 0001 deixou de usar o predicado de disponibilidade canonico — 0009.downgrade() ficou "
        "convergente com uma pergunta que ninguem mais faz"
    )


def test_the_migration_records_that_downgrade_restores_no_values() -> None:
    """A coluna volta VAZIA. A afirmacao tem de estar escrita, porque um leitor que assuma o
    contrario acha que ha um caminho de restauracao de dados aqui — e nao ha."""
    assert "volta vazia" in _SOURCE or "VAZIA" in _SOURCE
