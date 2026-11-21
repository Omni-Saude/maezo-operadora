"""Composicao `pares_mutuamente_exclusivos_ortop` do pacote K1 2.2.0 REAL do rco no MOTOR REAL (DL-0173).

As tres decisoes do recorte (`tests/fixtures/rco/k1-2.2.0-recorte/dmn/`, os arquivos do pacote gerado do
rco no commit a19783753b5b7ef839bc6ec5a565699097265177, ids `rco_`) sao implantadas no CIB Seven e
avaliadas pelo caminho de producao (`CibSevenDmnTransport.evaluate`) dentro de
`rco_composicao.compor_async`, para TODO caso-teste do rco das tres decisoes. Para cada caso:
- o grupo decide (`escolhido`; nunca `revisao_humana`: o rco prova CMP06 e CMP08 no
  `make composicao-dmn`);
- a linha da propria decisao do caso, avaliada dentro do grupo, satisfaz o `esperado` do rco (a
  comparacao do runner do rco: `rco_k1_2_2_recorte.iguais`);
- o grupo da a saida que o rco declara (`rco_k1_2_2_recorte.expectativa`): a decisao vencedora e a linha
  dela; onde o rco nao declara a saida do grupo, a decisao do caso nao vence.
O `esperado` e o do rco e NUNCA e ajustado ao que o motor devolve: divergencia significa que o motor ou a
composicao do Maezo se comportam diferente do rco, e e escalada. Sem motor alcancavel o arquivo PULA com
motivo explicito (`tests/integration/conftest.py`).
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
from tests.unit.platform import rco_k1_2_2_recorte as rec

pytestmark = pytest.mark.integration

SPEC_DMN = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"


@pytest.fixture
async def implantadas(engine_client: httpx.AsyncClient) -> dict[str, str]:
    """Implanta os tres DMN do recorte; devolve `{id da decisao: id da definicao vigente por chave}`."""
    # Desde o primeiro pin real do K1 (#1367, DL-0198) as decisoes do recorte tambem estao pinadas em
    # `spec/processes/dmn/rco/`. Colidir com o repo so e aceito quando o arquivo do recorte e BYTE A BYTE o
    # pinado: implantar de novo nao muda tabela nenhuma do motor; conteudo diferente na mesma chave reprova.
    colisao = sorted(set(rec.ORDEM) & rp.chaves_de_decisao_do_repo(SPEC_DMN))
    divergentes = [
        d for d in colisao if (SPEC_DMN / "rco" / rec.dmn(d).name).read_bytes() != rec.dmn(d).read_bytes()
    ]
    assert divergentes == [], f"decisoes do recorte colidem com o repo com outro conteudo: {divergentes}"
    arquivos = {rec.dmn(d).name: (rec.dmn(d).name, rec.dmn(d).read_bytes(), "text/xml") for d in rec.ORDEM}
    resp = await engine_client.post(
        "/deployment/create",
        data={
            "deployment-name": "rco-k1-2.2.0-composicao-ortop",
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


async def test_casos_do_rco_pela_composicao_no_motor(
    dmn: CibSevenDmnTransport, implantadas: dict[str, str]
) -> None:
    grupo = rec.composicao()
    todos = rec.casos()
    assert len(todos) == 70
    divergencias: list[str] = []
    for caso in todos:
        linhas_por_decisao: dict[str, list[dict[str, Any]]] = {}

        async def avaliador(
            decisao: str,
            entradas: Mapping[str, Any],
            _gravadas: dict[str, list[dict[str, Any]]] = linhas_por_decisao,
        ) -> tuple[list[dict[str, Any]], DmnVersion]:
            linhas, versao = await dmn.evaluate(decisao, dict(entradas))
            _gravadas[decisao] = [dict(linha) for linha in linhas]
            return linhas, versao

        resultado = await rco_composicao.compor_async(grupo, avaliador, caso.entradas, modo="efeito")
        onde = f"{caso.arquivo}#{caso.id} ({caso.decisao})"
        if resultado.desfecho != rco_composicao.DESFECHO_ESCOLHIDO:
            divergencias.append(f"{onde}: revisao_humana ({resultado.motivo})")
            continue
        if {d: v.id for d, v in resultado.versoes.items()} != implantadas:
            divergencias.append(f"{onde}: versoes avaliadas {dict(resultado.versoes)} != implantadas")
        propria = linhas_por_decisao[caso.decisao]
        if len(propria) != 1:
            divergencias.append(f"{onde}: a decisao do caso devolveu {len(propria)} linhas")
        elif dif := rec.diferencas(caso.esperado, propria[0]):
            divergencias.append(f"{onde}: decisao do caso {dif}")
        exp = rec.expectativa(caso, todos, grupo)
        if exp.vencedora is None:
            if resultado.decisao_vencedora == exp.nao_vence:
                divergencias.append(f"{onde}: {exp.nao_vence} venceu sem idRegra")
            continue
        assert exp.esperado is not None
        if resultado.decisao_vencedora != exp.vencedora:
            divergencias.append(
                f"{onde}: venceu {resultado.decisao_vencedora}, o rco declara {exp.vencedora} ({exp.fonte})"
            )
        elif dif := rec.diferencas(exp.esperado, resultado.linhas[0]):
            divergencias.append(f"{onde}: saida do grupo {dif} ({exp.fonte})")
    assert divergencias == []
