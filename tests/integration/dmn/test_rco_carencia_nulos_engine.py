"""MZO-2b — a tabela de carencia do rco com as entradas do K3 NULAS, no MOTOR REAL (nunca por mock).

O montador de entradas (`maezo.tools.workers.rco_montador`) manda nulos `grupoCarencia`,
`carenciaContratualDias` e `saldoCarenciaDias` enquanto o contrato AMH pinado for anterior ao v1.16
(K3 nao publicado). Este arquivo prova, avaliando no CIB Seven a tabela PUBLICADA pelo pacote K1
(`tests/fixtures/rco/k1-1.1.0-recorte/geral_carencia.dmn`, `rco_geral_carencia`, o arquivo do pacote
gerado do rco no commit fe1dc8975c0a486da0699f0fd6a0279e4b70f9af), que essas entradas nulas levam a
`revisao_humana` (fila `cadastro`) e NUNCA a `negar`, para todo carater e data, e que a mesma tabela nega
quando o K3 traz saldo positivo (o nulo nao e um acaso da tabela: sao o pin e a lacuna do saldo que
seguram a negativa).

A tabela e FIXTURE DE TESTE, nunca de `spec/` (o pin do rco segue `sem_pacote`). Sem motor
alcancavel, o pacote inteiro PULA com motivo explicito (`tests/integration/conftest.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from tests.unit.tools.workers import rco_recorte

pytestmark = pytest.mark.integration

_FIXTURE: Path = rco_recorte.CARENCIA_DMN
_KEY = "rco_geral_carencia"
_BYTES = _FIXTURE.read_bytes()


@pytest.fixture
async def carencia_deployed(engine_client: httpx.AsyncClient) -> str:
    resp = await engine_client.post(
        "/deployment/create",
        data={
            "deployment-name": "mzo-2b-teste-rco-carencia",
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
        },
        files={_FIXTURE.name: (_FIXTURE.name, _BYTES, "text/xml")},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")
    definition = await engine_client.get(f"/decision-definition/key/{_KEY}")
    definition.raise_for_status()
    return str(definition.json()["id"])


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


def _entradas(
    data: str | None, carater: str | None, *, versao_k3: tuple[int, int], k3: mt.FatosK3V116
) -> dict[str, object]:
    decisao = rco_recorte.decisoes()[_KEY]
    # A variavel de start `carater_atendimento` e ignorada pelo montador; o carater vem so da guia.
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": data, "carater_atendimento": "eletivo"},
        versao_k3_pinada=versao_k3,
        k3=k3,
        carater_atendimento_guia=carater,
    )
    return dict(mt.montar_entradas(decisao, rco_recorte.dicionario(), fatos).variaveis)


_K3_NEGARIA = mt.FatosK3V116(data_atendimento="2026-12-01", grupo="demais", dias=180, saldo_dias=40)


@pytest.mark.parametrize("carater", ["eletivo", "urgencia", None])
@pytest.mark.parametrize("data", ["2026-12-01", None])
async def test_k3_nulo_pelo_pin_vai_a_revisao_humana_nunca_negar(
    dmn: CibSevenDmnTransport, carencia_deployed: str, carater: str | None, data: str | None
) -> None:
    entradas = _entradas(data, carater, versao_k3=(1, 15), k3=_K3_NEGARIA)
    assert entradas["grupoCarencia"] is None and entradas["saldoCarenciaDias"] is None
    rows, version = await dmn.evaluate(_KEY, entradas)
    assert version.id == carencia_deployed
    assert len(rows) == 1, rows
    assert rows[0]["decisao"] == "revisao_humana", rows
    assert rows[0]["fila"] == "cadastro", rows


async def test_mesma_tabela_nega_com_saldo_k3_positivo_o_pin_e_que_segura(
    dmn: CibSevenDmnTransport, carencia_deployed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mt, "lacuna_saldo_fechada", lambda: True)  # so para provar o efeito do gate
    entradas = _entradas("2026-12-01", "eletivo", versao_k3=(1, 16), k3=_K3_NEGARIA)
    rows, _version = await dmn.evaluate(_KEY, entradas)
    assert [r["decisao"] for r in rows] == ["negar"], rows
    assert rows[0]["classeAdverso"] == "administrativo", rows
