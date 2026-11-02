"""Pacote PAGTO PR-E — a forma da linha, a chave e o protocolo do store (sem banco).

O banco real (triggers, CHECKs, UNIQUE, locks, as tres consultas) e' provado ao vivo em
`test_pagto_obrigacao_ledger_live_pg.py`. Aqui: espelhos dos CHECKs recusando antes da rede, a
chave deterministica, a ordem dos locks, a falha fechada e o erro de programacao que sobe.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.pagto_obrigacao import ledger as lg
from maezo.platform.pagto_obrigacao.ledger import (
    EntradaObrigacao,
    PostgresRazaoObrigacoesPagamento,
    RazaoIndisponivelError,
    RazaoObrigacoesPagamento,
    chave_obrigacao,
)

_MIGRACAO = (
    Path(__file__).resolve().parents[3]
    / "src/maezo/platform/migrations/versions/0033_pagto_obrigacao_ledger.py"
)


def _entrada(**campos: Any) -> EntradaObrigacao:
    base: dict[str, Any] = {
        "tenant": "amh",
        "evento": "REGISTRADA",
        "origem": "contas",
        "chave_obrigacao": chave_obrigacao("amh", "contas", ("L1", "p1", "-")),
        "ordem_pagamento_id": "ORDEM-1",
        "prestador_id": "p1",
        "valor_cents": 100,
        "numero_lote_tiss": "L1",
        "competencia": "2026-06",
        "origem_instancia_id": "pi-1",
    }
    base.update(campos)
    return EntradaObrigacao(**base)


def test_chave_deterministica_por_tenant_origem_e_componentes() -> None:
    a = chave_obrigacao("amh", "contas", ("L1", "p1", "G1"))
    assert a == chave_obrigacao("amh", "contas", ("L1", "p1", "G1"))
    assert re.fullmatch(r"OBRIG-contas-[0-9a-f]{64}", a)
    assert a != chave_obrigacao("outro-tenant", "contas", ("L1", "p1", "G1"))
    assert a != chave_obrigacao("amh", "recurso", ("L1", "p1", "G1")).replace("recurso", "contas")
    # Sem ambiguidade de separador: ("a-b","c") != ("a","b-c").
    assert chave_obrigacao("amh", "outro", ("a-b", "c")) != chave_obrigacao("amh", "outro", ("a", "b-c"))


@pytest.mark.parametrize(
    ("tenant", "origem", "componentes"),
    [("", "contas", ("x",)), ("amh", "fraude", ("x",)), ("amh", "contas", ()), ("amh", "contas", ("x", ""))],
)
def test_chave_sem_identidade_e_recusada(tenant: str, origem: str, componentes: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        chave_obrigacao(tenant, origem, componentes)


def test_entrada_valida() -> None:
    assert _entrada().valor_cents == 100


@pytest.mark.parametrize(
    "campos",
    [
        {"tenant": "a b"},
        {"tenant": "a/b"},
        {"evento": "CANCELADA"},
        {"origem": "fraude"},
        {"chave_obrigacao": "OBRIG-contas-xyz"},
        {"chave_obrigacao": chave_obrigacao("amh", "recurso", ("g",))},  # prefixo != origem
        {"ordem_pagamento_id": ""},
        {"ordem_pagamento_id": "a b"},
        {"prestador_id": "p 1"},
        {"valor_cents": 0},
        {"valor_cents": True},
        {"valor_cents": 2**63},
        {"numero_lote_tiss": ""},
        {"competencia": "2026-13"},
        {"origem_instancia_id": "pi 1"},
        {"versao_detector": "V1"},
    ],
)
def test_entrada_fora_dos_checks_da_0033_e_recusada_antes_da_rede(campos: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _entrada(**campos)


def test_espelhos_batem_com_a_migracao() -> None:
    ddl = _MIGRACAO.read_text(encoding="utf-8")
    assert "evento IN ('REGISTRADA', 'LIBERADA')" in ddl
    assert "origem IN ('contas', 'recurso', 'outro')" in ddl
    assert "UNIQUE (tenant, chave_obrigacao, ordem_pagamento_id, evento)" in ddl
    assert "BEFORE UPDATE OR DELETE" in ddl and "BEFORE TRUNCATE" in ddl
    assert "REVOKE ALL ON TABLE pagto_obrigacao_ledger FROM PUBLIC" in ddl
    assert {"REGISTRADA", "LIBERADA"} == lg.EVENTOS
    assert {"contas", "recurso", "outro"} == lg.ORIGENS


# --- store sobre uma conexao falsa ------------------------------------------------------------


class _Conn:
    def __init__(
        self,
        *,
        nivel1: list[dict[str, Any]] | None = None,
        soma: int = 0,
        similar: dict[str, Any] | None = None,
        gravado: int | None = 1,
        erro: Exception | None = None,
    ) -> None:
        self.chamadas: list[tuple[str, tuple[Any, ...]]] = []
        self._nivel1 = nivel1 or []
        self._soma = soma
        self._similar = similar
        self._gravado = gravado
        self._erro = erro
        self.fechada = False

    async def execute(self, sql: str, *args: Any) -> None:
        self.chamadas.append((sql, args))

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.chamadas.append((sql, args))
        if self._erro is not None:
            raise self._erro
        return self._nivel1

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.chamadas.append((sql, args))
        if self._erro is not None:
            raise self._erro
        return self._gravado if sql.startswith("INSERT") else self._soma

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.chamadas.append((sql, args))
        return self._similar

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield

    async def close(self) -> None:
        self.fechada = True


def _store(conn: _Conn) -> PostgresRazaoObrigacoesPagamento:
    async def _conectar() -> _Conn:
        return conn

    return PostgresRazaoObrigacoesPagamento(dsn="postgresql://x@h/db", tenant="amh", conectar=_conectar)


def test_store_cumpre_a_porta() -> None:
    assert isinstance(_store(_Conn()), RazaoObrigacoesPagamento)


def test_registrar_e_ler_trava_prestador_depois_obrigacao_le_e_so_entao_grava() -> None:
    conn = _Conn()
    asyncio.run(_store(conn).registrar_e_ler(_entrada()))
    sqls = [sql for sql, _ in conn.chamadas]
    assert sqls[0].startswith("SET search_path")
    locks = [args[0] for sql, args in conn.chamadas if "pg_advisory_xact_lock" in sql]
    assert locks == [
        "pagto-prestador|amh|p1",
        f"pagto-obrigacao|amh|{_entrada().chave_obrigacao}",
    ]
    assert sqls[-1].startswith("INSERT INTO pagto_obrigacao_ledger")
    assert "ON CONFLICT (tenant, chave_obrigacao, ordem_pagamento_id, evento) DO NOTHING" in sqls[-1]
    assert conn.fechada


@pytest.mark.parametrize(
    ("linhas", "situacao", "ordem"),
    [
        ([], "SEM_OUTRA_ORDEM", ""),
        ([{"ordem_pagamento_id": "O2", "evento": "REGISTRADA"}], "REGISTRADA_OUTRA_ORDEM", "O2"),
        (
            [
                {"ordem_pagamento_id": "O2", "evento": "REGISTRADA"},
                {"ordem_pagamento_id": "O3", "evento": "LIBERADA"},
            ],
            "LIBERADA_OUTRA_ORDEM",
            "O3",
        ),
    ],
)
def test_nivel1_a_mais_grave_vence(linhas: list[dict[str, Any]], situacao: str, ordem: str) -> None:
    indicios = asyncio.run(_store(_Conn(nivel1=linhas)).registrar_e_ler(_entrada()))
    assert (indicios.nivel1_situacao, indicios.nivel1_ordem_id) == (situacao, ordem)


def test_niveis_2_e_3_e_gravou() -> None:
    conn = _Conn(soma=700, similar={"ordem_pagamento_id": "OS", "idade_dias": 4}, gravado=None)
    indicios = asyncio.run(_store(conn).registrar_e_ler(_entrada()))
    assert indicios.nivel2_soma_outras_cents == 700
    assert (indicios.nivel3_avaliado, indicios.nivel3_similar_idade_dias, indicios.nivel3_ordem_id) == (
        True,
        4,
        "OS",
    )
    assert indicios.gravou is False  # re-entrega da mesma ordem: ja estava


def test_sem_instancia_e_sem_competencia_nao_consulta_niveis_2_e_3() -> None:
    conn = _Conn()
    indicios = asyncio.run(_store(conn).registrar_e_ler(_entrada(origem_instancia_id=None, competencia=None)))
    assert indicios.nivel2_soma_outras_cents is None
    assert indicios.nivel3_avaliado is False and indicios.nivel3_similar_idade_dias is None
    assert not any("SUM(valor_cents)" in sql or "idade_dias" in sql for sql, _ in conn.chamadas)


def test_falha_de_banco_vira_razao_indisponivel_e_a_conexao_fecha() -> None:
    conn = _Conn(erro=OSError("conexao caiu"))
    with pytest.raises(RazaoIndisponivelError):
        asyncio.run(_store(conn).registrar_e_ler(_entrada()))
    assert conn.fechada


def test_conexao_que_nao_abre_vira_razao_indisponivel() -> None:
    async def _nao_abre() -> Any:
        raise ConnectionRefusedError

    store = PostgresRazaoObrigacoesPagamento(dsn="postgresql://x@h/db", tenant="amh", conectar=_nao_abre)
    with pytest.raises(RazaoIndisponivelError):
        asyncio.run(store.registrar_e_ler(_entrada()))
    with pytest.raises(RazaoIndisponivelError):
        asyncio.run(store.registrar_liberacao(_entrada(evento="LIBERADA")))


def test_erro_de_programacao_nao_e_embrulhado() -> None:
    with pytest.raises(TypeError):
        asyncio.run(_store(_Conn(erro=TypeError("bug"))).registrar_e_ler(_entrada()))


def test_tenant_e_evento_errados_sao_recusados_antes_da_rede() -> None:
    conn = _Conn()
    store = _store(conn)
    outro = chave_obrigacao("x", "contas", ("L1", "p1", "-"))
    with pytest.raises(ValueError):
        asyncio.run(store.registrar_e_ler(_entrada(tenant="x", chave_obrigacao=outro)))
    with pytest.raises(ValueError):
        asyncio.run(store.registrar_e_ler(_entrada(evento="LIBERADA")))
    with pytest.raises(ValueError):
        asyncio.run(store.registrar_liberacao(_entrada()))
    assert conn.chamadas == []


def test_registrar_liberacao_so_insere() -> None:
    conn = _Conn(gravado=None)
    assert asyncio.run(_store(conn).registrar_liberacao(_entrada(evento="LIBERADA"))) is False
    sqls = [sql for sql, _ in conn.chamadas]
    assert len(sqls) == 2 and sqls[1].startswith("INSERT")
    assert not any(re.search(r"\b(UPDATE|DELETE|TRUNCATE)\b", sql) for sql in sqls)


def test_store_nao_tem_sql_de_reescrita() -> None:
    fonte = Path(lg.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(UPDATE|DELETE FROM|TRUNCATE)\b", fonte.split("_COLUNAS: Final")[1])
