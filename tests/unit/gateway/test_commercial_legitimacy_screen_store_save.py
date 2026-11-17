"""OP21 substrato — o contrato de `save`: reenvio idempotente, chave tomada é `revision_taken`.

Defeito corrigido: `PostgresLegitimacyScreenStore.save` fazia `INSERT … ON CONFLICT DO
NOTHING` e devolvia a re-leitura da chave — numa corrida de revalidação (outro escritor grava
a revisão N+1 entre a leitura da cabeça e o INSERT) o chamador recebia a linha do OUTRO
escritor como se fosse a dele (201 com critérios/estado/autor alheios) e a revalidação dele se
perdia em silêncio. Agora a re-leitura é COMPARADA ao pedido, na mesma transação:

- reenvio com o mesmo conteúdo e autor devolve a linha existente (idempotente), inclusive
  quando só os carimbos de instante (`created_at`/`updated_at`) diferem;
- conteúdo OU autor diferente na mesma `(screen_ref, screen_revision)` é `revision_taken`;
- a violação da UNIQUE da oportunidade só é `contract_mismatch` quando a chave de instância
  está livre — se a chave foi tomada (interleaving da corrida), a classificação é pela
  comparação.

Sem banco: um engine dublê com a semântica da 0030 (PK de instância árbitro do ON CONFLICT +
UNIQUE da oportunidade). A corrida em PostgreSQL REAL está em
`tests/unit/platform/test_legitimacy_screens_migration_live_pg.py`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from maezo.gateway.commercial_legitimacy_screen import (
    _INSERT_SQL,
    _SELECT_ONE_SQL,
    LegitimacyCriterion,
    LegitimacyEvidenceRef,
    LegitimacyScreenRecord,
    LegitimacyScreenStoreError,
    PostgresLegitimacyScreenStore,
    _criteria_json,
    same_screen_revision_content,
)

_NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)


def _record(**overrides: object) -> LegitimacyScreenRecord:
    fields: dict[str, object] = {
        "tenant": "tenant-op21",
        "screen_ref": "screen-" + "a" * 16,
        "screen_revision": 2,
        "legal_entity_ref": "pj-contratante-1",
        "opportunity_ref": "opportunity-1",
        "contract_kind": "empresarial",
        "contracting_form": "direta",
        "criteria": (
            LegitimacyCriterion(
                criterion_id="vinculo_empresarial",
                result="proven",
                norm_anchor="RN 557/2022, art. 5º",
                evidence_refs=(
                    LegitimacyEvidenceRef(
                        kind="operadora", locator="custodia/2026/vinculo.pdf", document_sha256="b" * 64
                    ),
                ),
            ),
        ),
        "status": "valid",
        "would_block": False,
        "art39_signaled": False,
        "reconfirmation_due_at": datetime(2027, 10, 26, 12, 0, 0, tzinfo=UTC),
        "created_by": "staff-op21",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    return LegitimacyScreenRecord(**fields)  # type: ignore[arg-type]


def _row(record: LegitimacyScreenRecord) -> dict[str, Any]:
    """A linha como o dialect devolve: jsonb como lista, instantes como `datetime`."""
    row = record.model_dump(exclude={"schema_version"})
    row["criteria"] = json.loads(_criteria_json(record))
    return row


class _Table:
    """`portal_legitimacy_screens` em memória: PK árbitro + UNIQUE da oportunidade."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, int], dict[str, Any]] = {}
        #: Hook da corrida: roda ANTES do INSERT do chamador (o outro escritor chega antes).
        self.before_insert: Callable[[], None] | None = None
        #: Interleaving em que a UNIQUE da oportunidade dispara antes do árbitro do ON CONFLICT.
        self.non_arbiter_first = False

    def put(self, record: LegitimacyScreenRecord) -> None:
        self.rows[(record.tenant, record.screen_ref, record.screen_revision)] = _row(record)

    def insert(self, params: dict[str, Any]) -> None:
        if self.before_insert is not None:
            hook, self.before_insert = self.before_insert, None
            hook()
        key = (params["tenant"], params["screen_ref"], params["screen_revision"])
        binding_taken = any(
            (row["tenant"], row["opportunity_ref"], row["screen_revision"])
            == (params["tenant"], params["opportunity_ref"], params["screen_revision"])
            and row["screen_ref"] != params["screen_ref"]
            for row in self.rows.values()
        )
        if key in self.rows:
            if self.non_arbiter_first:
                raise IntegrityError("INSERT", {}, Exception("unique opportunity"))
            return  # ON CONFLICT (pk) DO NOTHING
        if binding_taken:
            raise IntegrityError("INSERT", {}, Exception("unique opportunity"))
        row = dict(params)
        row["operation"] = "legitimacy_screen"
        row["criteria"] = json.loads(params["criteria"])
        self.rows[key] = row


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class _Conn:
    def __init__(self, table: _Table) -> None:
        self.table = table
        self.savepoints = 0

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        self.savepoints += 1
        yield

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        if statement is _INSERT_SQL:
            self.table.insert(params)
            return _Result(None)
        assert statement is _SELECT_ONE_SQL
        key = (params["tenant"], params["screen_ref"], params["screen_revision"])
        return _Result(self.table.rows.get(key))


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, table: _Table) -> None:
        self.table = table
        self.connections: list[_Conn] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_Conn]:
        conn = _Conn(self.table)
        self.connections.append(conn)
        yield conn


def _store() -> tuple[PostgresLegitimacyScreenStore, _Table, _Engine]:
    table = _Table()
    engine = _Engine(table)
    return PostgresLegitimacyScreenStore(engine), table, engine  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------
# A comparação de reenvio — o que conta e o que não conta.
# ----------------------------------------------------------------------------------


def test_comparacao_ignora_so_os_carimbos_de_instante() -> None:
    base = _record()
    later = _NOW + timedelta(seconds=7)
    assert same_screen_revision_content(base, _record(created_at=later, updated_at=later))
    # Mesmo instante em outro fuso (o banco devolve UTC) não fabrica diferença.
    brt = timezone(timedelta(hours=-3))
    assert same_screen_revision_content(
        base, _record(reconfirmation_due_at=datetime(2027, 10, 26, 9, 0, 0, tzinfo=brt))
    )


@pytest.mark.parametrize(
    "change",
    [
        {"created_by": "staff-outro"},  # autor diferente = outra verificação
        {"status": "pending"},
        {"would_block": True},
        {"opportunity_ref": "opportunity-2"},
        {"legal_entity_ref": "pj-outra"},
        {"contracting_form": "administradora"},
        {"reconfirmation_due_at": None},
        {"art39_signaled": True, "art39_anchor": "RN 557/2022, art. 39"},
        {
            "criteria": (
                LegitimacyCriterion(
                    criterion_id="vinculo_empresarial", result="pending", norm_anchor="RN 557/2022, art. 5º"
                ),
            )
        },
    ],
)
def test_comparacao_conta_todo_campo_de_conteudo_e_o_autor(change: dict[str, object]) -> None:
    assert not same_screen_revision_content(_record(), _record(**change))


# ----------------------------------------------------------------------------------
# `PostgresLegitimacyScreenStore.save` sobre o engine dublê.
# ----------------------------------------------------------------------------------


async def test_primeira_gravacao_devolve_a_propria_linha_dentro_de_um_savepoint() -> None:
    store, table, engine = _store()
    record = _record()
    assert await store.save(record) == record
    assert len(table.rows) == 1
    assert engine.connections[-1].savepoints == 1


async def test_reenvio_identico_e_idempotente_e_devolve_a_linha_existente() -> None:
    store, table, _ = _store()
    first = await store.save(_record())
    later = _NOW + timedelta(minutes=3)
    again = await store.save(_record(created_at=later, updated_at=later))  # só carimbos mudam
    assert again == first
    assert again.created_at == _NOW  # a linha EXISTENTE, não o pedido
    assert len(table.rows) == 1


async def test_conteudo_diferente_na_mesma_revisao_e_revision_taken() -> None:
    store, table, _ = _store()
    winner = _record(
        status="pending",
        criteria=(
            LegitimacyCriterion(
                criterion_id="vinculo_empresarial", result="pending", norm_anchor="RN 557/2022, art. 5º"
            ),
        ),
    )
    await store.save(winner)
    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await store.save(_record())
    assert exc.value.code == "revision_taken"
    stored = table.rows[("tenant-op21", "screen-" + "a" * 16, 2)]
    assert stored["status"] == "pending"  # a linha do vencedor permanece intacta


async def test_so_o_autor_diferente_e_revision_taken() -> None:
    store, _, _ = _store()
    await store.save(_record(created_by="staff-a"))
    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await store.save(_record(created_by="staff-b"))
    assert exc.value.code == "revision_taken"


async def test_corrida_o_outro_escritor_chega_entre_a_cabeca_e_o_insert() -> None:
    """O defeito original: antes, o chamador recebia a linha do outro escritor como sucesso."""
    store, table, _ = _store()
    other = _record(
        created_by="staff-outro",
        status="invalid",
        criteria=(
            LegitimacyCriterion(
                criterion_id="vinculo_empresarial", result="not_proven", norm_anchor="RN 557/2022, art. 5º"
            ),
        ),
    )
    table.before_insert = lambda: table.put(other)
    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await store.save(_record())
    assert exc.value.code == "revision_taken"
    assert table.rows[("tenant-op21", "screen-" + "a" * 16, 2)]["created_by"] == "staff-outro"


async def test_unique_da_oportunidade_com_a_chave_tomada_e_classificada_pela_comparacao() -> None:
    """Interleaving em que a UNIQUE não-árbitro dispara primeiro: a chave de instância foi
    TOMADA (não é segunda instância) — conteúdo diferente é `revision_taken`, o mesmo é reenvio."""
    store, table, _ = _store()
    table.put(_record(created_by="staff-outro"))
    table.non_arbiter_first = True
    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await store.save(_record())
    assert exc.value.code == "revision_taken"
    identical = await store.save(_record(created_by="staff-outro"))
    assert identical.created_by == "staff-outro"


async def test_segunda_instancia_da_oportunidade_continua_contract_mismatch() -> None:
    store, _, _ = _store()
    await store.save(_record())
    with pytest.raises(LegitimacyScreenStoreError) as exc:
        await store.save(_record(screen_ref="screen-" + "d" * 16))
    assert exc.value.code == "contract_mismatch"
