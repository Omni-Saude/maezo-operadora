"""Cercas estruturais da migration `0009_drop_pgvector` — a remocao da camada semantica (DU-01-b).

Sem Postgres. A prova VIVA (upgrade/downgrade round-trip contra `postgres:16` E contra
`pgvector/pgvector:pg16`, incluindo a ordem entre tenants) foi executada na entrega e esta citada
no `docs/evidence-ledger.md`; o que se fixa aqui e' o conjunto de coisas que precisam continuar
verdadeiras no DDL para que uma edicao futura que quebre uma delas fique vermelha em CI em vez de
em producao.

Cinco invariantes:

1.  **Integridade da cadeia.** 0009 revisa 0008 e e' a UNICA cabeca; nenhum revision id e'
    reivindicado duas vezes e nenhuma revisao e' pai de dois filhos. (Assercao herdada de
    `test_migration_0008_a2a_fact_outbox.py`, por instrucao do docstring dela: a cabeca pertence
    a quem POR ORA e' a cabeca.)

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

4.  **`downgrade()` e' honesto e NAO tem guarda de disponibilidade.** Recriar a extensao e a
    coluna, sem `IF EXISTS (pg_available_extensions)`: num servidor sem pgvector ele FALHA ALTO em
    vez de devolver um schema "0008" sem a coluna que a 0008 tem.

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


def test_0009_is_the_unique_head_of_a_linear_chain() -> None:
    """Sem fork: cada revisao reivindicada uma vez, exatamente uma nao referenciada como pai.

    Herdada de `test_migration_0008_a2a_fact_outbox.py` quando a 0009 pousou — a cabeca pertence
    a quem POR ORA e' a cabeca, entao uma futura 0010 muda UM arquivo (este), nao a suite anterior.
    """
    revisions: dict[str, str | None] = {}
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert rev is not None, f"{path.name} declares no revision"
        assert down is not None, f"{path.name} declares no down_revision"
        assert rev.group(1) not in revisions, f"duplicate revision id {rev.group(1)}"
        revisions[rev.group(1)] = down.group(1)

    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {_REVISION}, f"expected {_REVISION} to be the sole head, got {heads}"
    assert len(parents) == len(revisions) - 1, "a revision is claimed as parent by two children"


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


# ---------------------------------------------------------------------------
# 4. `downgrade()` e' honesto
# ---------------------------------------------------------------------------


def test_downgrade_recreates_both_the_extension_and_the_column() -> None:
    sql = " ".join(_downgrade_sql().split())
    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public" in sql
    assert "ADD COLUMN IF NOT EXISTS embedding public.vector(1536)" in sql


def test_downgrade_has_no_availability_guard_and_therefore_fails_loudly() -> None:
    """Deliberadamente SEM `pg_available_extensions`: num servidor sem pgvector o downgrade tem de
    quebrar, nao devolver um "0008" sem a coluna que a 0008 tem."""
    assert "pg_available_extensions" not in _downgrade_sql(), (
        "downgrade() ganhou uma guarda de disponibilidade — passaria a alegar uma reversibilidade "
        "que nao entrega (ver o docstring da migration)"
    )


def test_the_migration_records_that_downgrade_restores_no_values() -> None:
    """A coluna volta VAZIA. A afirmacao tem de estar escrita, porque um leitor que assuma o
    contrario acha que ha um caminho de restauracao de dados aqui — e nao ha."""
    assert "volta vazia" in _SOURCE or "VAZIA" in _SOURCE
