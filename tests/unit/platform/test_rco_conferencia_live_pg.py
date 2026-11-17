"""DL-0182 (unidade 5d-1) AO VIVO: a janela da `conferida` e o escritor de desfecho sobre a 0035 real.

Cadeia alembic REAL num schema de tenant descartavel (molde `test_rco_decisao_regra_live_pg.py`). O indicador
`efeito_k1_habilitado` e simulado so na avaliacao (`habilitado=True`); nada de config ou ambiente muda.
Provas:

- so conferencias CONFIRMADAS contam: 29 `sim` mantem a conferencia, o 30o fecha a janela da `glosar`;
  decisoes em sombra e de outra regra/ativacao nunca contam; `sem_desfecho` e ignorado;
- `negar` da mesma regra continua em conferencia depois das 30;
- um `nao` zera a janela (e alerta); depois sao precisas 30 novas confirmacoes;
- versao nova comeca do zero;
- o escritor so insere (append-only), recusa decisao que nao foi conferencia e e idempotente, inclusive
  com re-tentativas simultaneas.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
import structlog

from maezo.platform import rco_ativacao as at
from maezo.platform.rco_manifesto import ParametrosAtivacao, RegraK1
from maezo.platform.rco_registro import (
    EVENTO_CONFERENCIA_REJEITADA,
    ContextoDecisao,
    DesfechoRecusadoError,
    PostgresRegistroDecisoesRco,
    RegistroRcoIndisponivelError,
    avaliar_regra,
    chave_idempotencia,
)
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.platform.test_rco_decisao_regra_live_pg import (
    _drop,
    _linhas,
    _postgres_reachable,
    _schema_novo,
)

pytestmark = pytest.mark.integration

JANELA = 30
PARAMETROS = ParametrosAtivacao(3, 7, 50, 5, 30, JANELA)
AGORA = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _skip_if_postgres_unreachable() -> None:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL / "
            "MAEZO_PG_HOST_PORT) — 5d-1 rco conferencia live tests SKIPPED (visible, not silent). "
            "Start it with `docker compose --profile core up -d postgres` and re-run this file."
        )


def _avaliacao(
    decisao: str = "glosar",
    *,
    versao: str = "1.0.0",
    ativacao: str = "conferida",
    habilitado: bool = True,
    id_regra: str = "REGRA-C",
) -> Any:
    av = avaliar_regra(
        decision_key="rco_x",
        sha_dmn="sha256:" + "a" * 64,
        regra=RegraK1(id_regra, versao, "b" * 64, ativacao, False),
        parametros=PARAMETROS,
        row={
            "decisao": decisao,
            "classeAdverso": "administrativo",
            "motivoTab38": "1801",
            "fila": "cadastro",
        },
        estados=at.EstadosManuais({(id_regra, versao): "ativa_l2"}),
        habilitado=habilitado,
    )
    assert av is not None
    return av


class _Caso:
    def __init__(self, dsn: str, tenant: str) -> None:
        self.dsn = dsn
        self.tenant = tenant
        self.store = PostgresRegistroDecisoesRco(dsn=dsn, tenant=tenant)
        self._n = 0

    async def decidir(self, av: Any) -> tuple[int, str]:
        """Grava uma decisao nova e devolve (id, acao) da linha."""
        self._n += 1
        tarefa = f"t{self._n}"
        ctx = ContextoDecisao(
            tenant=self.tenant,
            chave_idempotencia=chave_idempotencia(self.tenant, tarefa, "rco_x"),
            chave_negocio=f"AUTH-{tarefa}",
        )
        assert await self.store.registrar(av, ctx) is True
        linha = (
            await _linhas(
                self.dsn,
                self.tenant,
                "SELECT id, acao FROM rco_decisao_regra WHERE chave_idempotencia = $1",
                ctx.chave_idempotencia,
            )
        )[0]
        return int(linha["id"]), str(linha["acao"])

    async def confirmar_n(self, n: int, av: Any) -> None:
        for _ in range(n):
            id_decisao, acao = await self.decidir(av)
            assert acao == "conferencia"
            r = await self.store.registrar_desfecho(id_decisao, "confirmar", desfecho_em=AGORA)
            assert r.gravado


async def test_janela_conta_so_confirmadas_e_zera_na_rejeicao_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        caso = _Caso(dsn, tenant)
        glosa = _avaliacao("glosar")

        # Sombra (indicador desligado) da MESMA regra: 40 decisoes que nao sao conferencia nem contam.
        sombra_ids = [(await caso.decidir(_avaliacao("glosar", habilitado=False)))[0] for _ in range(40)]
        with pytest.raises(DesfechoRecusadoError, match="acao_nao_e_conferencia"):
            await caso.store.registrar_desfecho(sombra_ids[0], "confirmar", desfecho_em=AGORA)
        assert await caso.store.contar_conferencias_confirmadas("REGRA-C", "1.0.0") == 0

        # 29 confirmadas + 10 conferencias `sem_desfecho`: a janela continua aberta.
        await caso.confirmar_n(29, glosa)
        for _ in range(10):
            id_decisao, acao = await caso.decidir(glosa)
            assert acao == "conferencia"
            assert (await caso.store.registrar_desfecho(id_decisao, "sem_desfecho", desfecho_em=None)).gravado
        assert await caso.store.contar_conferencias_confirmadas("REGRA-C", "1.0.0") == 29
        _, acao = await caso.decidir(glosa)
        assert acao == "conferencia"

        # Outra ativacao/regra com desfechos nao contam para REGRA-C.
        outra = _avaliacao("glosar", id_regra="REGRA-D")
        id_outra, _ = await caso.decidir(outra)
        await caso.store.registrar_desfecho(id_outra, "confirmar", desfecho_em=AGORA)
        assert await caso.store.contar_conferencias_confirmadas("REGRA-C", "1.0.0") == 29

        # A 30a confirmacao fecha a janela da `glosar`; `negar` da mesma regra continua em conferencia.
        await caso.confirmar_n(1, glosa)
        assert await caso.store.contar_conferencias_confirmadas("REGRA-C", "1.0.0") == 30
        id_efeito, acao = await caso.decidir(glosa)
        assert acao == "efeito_com_amostragem"
        with pytest.raises(DesfechoRecusadoError, match="acao_nao_e_conferencia"):
            await caso.store.registrar_desfecho(id_efeito, "confirmar", desfecho_em=AGORA)
        id_negar, acao = await caso.decidir(_avaliacao("negar"))
        assert acao == "conferencia"

        # Um `nao` zera a janela e alerta; a proxima `glosar` volta a conferir.
        with structlog.testing.capture_logs() as logs:
            r = await caso.store.registrar_desfecho(id_negar, "rejeitar", desfecho_em=AGORA)
        assert r.gravado and r.janela_reiniciada
        alertas = [e for e in logs if e["event"] == EVENTO_CONFERENCIA_REJEITADA]
        assert len(alertas) == 1 and alertas[0]["id_regra"] == "REGRA-C"
        assert await caso.store.contar_conferencias_confirmadas("REGRA-C", "1.0.0") == 0
        await caso.confirmar_n(29, glosa)
        _, acao = await caso.decidir(glosa)
        assert acao == "conferencia"
        await caso.confirmar_n(1, glosa)
        _, acao = await caso.decidir(glosa)
        assert acao == "efeito_com_amostragem"

        # Versao nova comeca do zero.
        _, acao = await caso.decidir(_avaliacao("glosar", versao="1.1.0"))
        assert acao == "conferencia"
        assert await caso.store.contar_conferencias_confirmadas("REGRA-C", "1.1.0") == 0

        # Plena nunca entra na contagem nem aceita desfecho de conferencia.
        id_plena, acao = await caso.decidir(_avaliacao("glosar", ativacao="plena", id_regra="REGRA-P"))
        assert acao == "efeito_com_amostragem"
        with pytest.raises(DesfechoRecusadoError, match="acao_nao_e_conferencia"):
            await caso.store.registrar_desfecho(id_plena, "rejeitar", desfecho_em=AGORA)
    finally:
        await _drop(dsn, tenant)


async def test_escritor_de_desfecho_append_only_e_idempotente_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        caso = _Caso(dsn, tenant)
        id_decisao, acao = await caso.decidir(_avaliacao("glosar"))
        assert acao == "conferencia"

        # re-tentativas simultaneas do mesmo clique: uma linha so
        resultados = await asyncio.gather(
            *(caso.store.registrar_desfecho(id_decisao, "confirmar", desfecho_em=AGORA) for _ in range(6))
        )
        assert [r.gravado for r in resultados].count(True) == 1
        assert (
            await caso.store.registrar_desfecho(id_decisao, "confirmar", desfecho_em=AGORA)
        ).gravado is False

        # conferencia decidida nao volta a ser indecisa; correcao `sim` -> `nao` vira LINHA NOVA
        with pytest.raises(DesfechoRecusadoError, match="conferencia_ja_decidida"):
            await caso.store.registrar_desfecho(id_decisao, "sem_desfecho", desfecho_em=None)
        primeira = await _linhas(
            dsn, tenant, "SELECT id, concordou, desfecho_em FROM rco_decisao_regra_desfecho"
        )
        assert (await caso.store.registrar_desfecho(id_decisao, "rejeitar", desfecho_em=AGORA)).gravado
        todas = await _linhas(
            dsn, tenant, "SELECT id, concordou, desfecho_em FROM rco_decisao_regra_desfecho ORDER BY id"
        )
        assert [r["concordou"] for r in todas] == ["sim", "nao"]
        assert dict(todas[0]) == dict(primeira[0])  # a linha anterior nao muda (append-only)
        assert todas[0]["desfecho_em"] == AGORA

        # recusas sem escrever nada
        with pytest.raises(DesfechoRecusadoError, match="decisao_inexistente"):
            await caso.store.registrar_desfecho(10**9, "confirmar", desfecho_em=AGORA)
        outro = PostgresRegistroDecisoesRco(dsn=dsn, tenant="outro_tenant")
        with pytest.raises((RegistroRcoIndisponivelError, DesfechoRecusadoError)):  # outro schema: nunca a ve
            await outro.registrar_desfecho(id_decisao, "confirmar", desfecho_em=AGORA)
        assert len(await _linhas(dsn, tenant, "SELECT id FROM rco_decisao_regra_desfecho")) == 2

        # o banco continua recusando UPDATE/DELETE direto (0035): o escritor nunca precisaria deles
        raw = await asyncpg.connect(dsn)
        try:
            await raw.execute(f'SET search_path TO "{tenant}", public')
            with pytest.raises(asyncpg.RaiseError, match="append-only"):
                await raw.execute("UPDATE rco_decisao_regra_desfecho SET concordou = 'sim'")
        finally:
            await raw.close()
    finally:
        await _drop(dsn, tenant)
