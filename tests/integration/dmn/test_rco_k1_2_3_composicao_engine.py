"""Composicao `composicao_carencia_cpt` (`mais_restritiva`) do K1 2.3.0 REAL do rco no MOTOR REAL (DL-0183).

As duas decisoes do recorte (`tests/fixtures/rco/k1-2.3.0-recorte/dmn/`, os arquivos do pacote gerado do rco
no commit aed187d4bada72e365381b48c6e96921d34c8815, ids `rco_`) sao implantadas no CIB Seven e avaliadas pelo
caminho de producao (`CibSevenDmnTransport.evaluate`) dentro de `rco_composicao.compor_async`, com a
`ordem_restricao` e o vocabulario de `sinalAuditoria` do `vocabulario.json` do pacote:
- cada caso do GRUPO do rco (`validacao/composicao/regras-gerais/composicao_carencia_cpt.json`): o grupo
  decide (`escolhido`) e da a saida que o rco declara (`decisao`, `decisao_de`, `idRegra`, `motivoTab38`,
  `fila`, `sinais`), com a versao de cada membro avaliado;
- cada caso do rco POR DECISAO (`validacao/casos/regras-gerais/geral_{carencia,cpt}.json`), avaliado dentro do
  grupo: a linha da propria decisao, no veredicto do grupo, satisfaz o `esperado` do rco, e o grupo decide.
O `esperado` e o do rco e NUNCA e ajustado ao que o motor devolve: divergencia e escalada. Sem motor
alcancavel o arquivo PULA com motivo explicito (`tests/integration/conftest.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.platform import rco_composicao
from maezo.platform import rco_provas as rp
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform.rco_k1_2_2_recorte import diferencas

pytestmark = pytest.mark.integration

SPEC_DMN = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"


@pytest.fixture
async def implantadas(engine_client: httpx.AsyncClient) -> dict[str, str]:
    """Implanta os dois DMN do recorte; devolve `{id da decisao: id da definicao vigente por chave}`."""
    colisao = sorted(set(rec.ORDEM) & rp.chaves_de_decisao_do_repo(SPEC_DMN))
    assert colisao == [], f"decisoes do recorte colidem com o repo: {colisao}"
    arquivos = {rec.dmn(d).name: (rec.dmn(d).name, rec.dmn(d).read_bytes(), "text/xml") for d in rec.ORDEM}
    resp = await engine_client.post(
        "/deployment/create",
        data={
            "deployment-name": "rco-k1-2.3.0-composicao-carencia-cpt",
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true",
        },
        files=arquivos,
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")
    definicoes: dict[str, str] = {}
    for decisao in rec.ORDEM:
        definicao = await engine_client.get(f"/decision-definition/key/{decisao}")
        definicao.raise_for_status()
        definicoes[decisao] = str(definicao.json()["id"])
    return definicoes


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


async def _compor(
    dmn: CibSevenDmnTransport, entradas: Mapping[str, Any]
) -> rco_composicao.ResultadoComposicao:
    vocab = rec.vocabulario()

    async def avaliador(decisao: str, valores: Mapping[str, Any]) -> tuple[list[dict[str, Any]], DmnVersion]:
        linhas, versao = await dmn.evaluate(decisao, dict(valores))
        return [dict(linha) for linha in linhas], versao

    return await rco_composicao.compor_async(
        rec.composicao(),
        avaliador,
        entradas,
        ordem_restricao=vocab.ordem_restricao,
        sinais_auditoria=vocab.sinais_auditoria,
        modo="efeito",
    )


async def test_casos_do_grupo_do_rco_no_motor(dmn: CibSevenDmnTransport, implantadas: dict[str, str]) -> None:
    casos = rec.casos_do_grupo()
    assert len(casos) == 9
    divergencias: list[str] = []
    for caso in casos:
        resultado = await _compor(dmn, caso.entradas)
        onde = f"{caso.arquivo}#{caso.id}"
        if resultado.desfecho != rco_composicao.DESFECHO_ESCOLHIDO:
            divergencias.append(f"{onde}: revisao_humana ({resultado.motivo})")
            continue
        if {d: v.id for d, v in resultado.versoes.items()} != implantadas:
            divergencias.append(f"{onde}: versoes avaliadas {dict(resultado.versoes)} != implantadas")
        if set(resultado.veredictos) != set(rec.ORDEM):
            divergencias.append(f"{onde}: veredictos {sorted(resultado.veredictos)}")
        if dif := rec.diferencas_do_grupo(
            caso.esperado, resultado.decisao_vencedora, resultado.linhas[0], resultado.sinais
        ):
            divergencias.append(f"{onde}: {dif}")
    assert divergencias == []


async def test_casos_por_decisao_do_rco_dentro_do_grupo(
    dmn: CibSevenDmnTransport, implantadas: dict[str, str]
) -> None:
    todos = rec.casos()
    assert {d: sum(c.decisao == d for c in todos) for d in rec.ORDEM} == {
        "rco_geral_carencia": 51,
        "rco_geral_cpt": 22,
    }
    divergencias: list[str] = []
    for caso in todos:
        resultado = await _compor(dmn, caso.entradas)
        onde = f"{caso.arquivo}#{caso.id} ({caso.decisao})"
        if resultado.desfecho != rco_composicao.DESFECHO_ESCOLHIDO:
            divergencias.append(f"{onde}: revisao_humana ({resultado.motivo})")
            continue
        propria = resultado.veredictos.get(caso.decisao, ())
        if len(propria) != 1:
            divergencias.append(f"{onde}: a decisao do caso devolveu {len(propria)} linhas")
        elif dif := diferencas(caso.esperado, propria[0]):
            divergencias.append(f"{onde}: decisao do caso {dif}")
    assert divergencias == []
