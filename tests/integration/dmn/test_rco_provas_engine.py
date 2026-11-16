"""Provas do pacote K1 no motor REAL (contrato-k1.md §11 item 12): a fixture `teste-unario-booleano`
do rco (copia vendorizada, commit 323fec5c) avaliada no CIB Seven.

O `esperado` e o do rco, observado la no feel-engine 1.19.3; aqui ele NUNCA e ajustado ao que o motor
devolve: divergencia significa que as tabelas do rco se comportam diferente no motor do Maezo e e
escalada, nao corrigida no teste. Prova tambem que um `esperado` invertido e recusado.

`test_provas_do_pacote_pinado_casam_no_motor` le o PIN de bordo (`config/integrations/rco/rules.lock.json`):
com pacote fixado, roda TODAS as `provas[]` dele no CIB Seven e falha em qualquer divergencia (decisao da
ROOT, 16/11/2026: pin real so com provas executadas com sucesso). Este arquivo roda no shard `restante`
da lane `integration`, que todo PR que toca `config/integrations/rco/**` dispara (filtro de caminhos da
lane: so `docs/**`, `*.md`, `src/maezo/portal/web/**` e `.github/**` pulam). Sem pacote (`sem_pacote`) o
teste afirma esse estado explicitamente: nunca passa vazio sem motivo.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from maezo.platform import rco_manifesto as k1
from maezo.platform import rco_provas as rp

pytestmark = pytest.mark.integration

K1_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "rco"
REPO = Path(__file__).resolve().parents[3]
SPEC_DMN = REPO / "spec" / "processes" / "dmn"
PIN = REPO / "config" / "integrations" / "rco" / "rules.lock.json"
PIN_K1_DIR = REPO / k1.K1_DIR
ID = "teste-unario-booleano"
CASOS = f"provas/{ID}/validacao/casos"


def _prova(k1_dir: Path, *, rehash: bool = False) -> k1.ProvaK1:
    """Prova da copia vendorizada; `rehash` recalcula os sha256 (so para a copia deliberadamente alterada)."""
    origem = json.loads((k1_dir / "provas" / ID / "ORIGEM.json").read_text(encoding="utf-8"))
    arquivos = tuple(
        k1.ArquivoProvaK1(
            f"provas/{ID}/{rel}",
            k1.sha256_prefixado((k1_dir / "provas" / ID / rel).read_bytes()) if rehash else sha,
        )
        for rel, sha in origem["arquivos"].items()
    )
    return k1.ProvaK1(ID, "tabela-verdade de teste unario booleano", arquivos, CASOS)


async def test_todos_os_casos_da_fixture_casam_no_motor(engine_client: httpx.AsyncClient) -> None:
    prova = _prova(K1_DIR)
    definicoes = await rp.implantar_prova(
        engine_client, prova, k1_dir=K1_DIR, chaves_do_repo=rp.chaves_de_decisao_do_repo(SPEC_DMN)
    )
    avaliador = rp.AvaliadorCibSeven(engine_client, definicoes)
    divergencias = await rp.executar_provas([prova], k1_dir=K1_DIR, avaliador=avaliador)
    assert [d.render() for d in divergencias] == []


async def test_esperado_invertido_e_recusado(engine_client: httpx.AsyncClient, tmp_path: Path) -> None:
    # Copia para tmp_path e inverte o esperado de cel01/C01T (casa -> nao_casa); o motor segue dizendo casa.
    origem = K1_DIR / "provas" / ID
    destino = tmp_path / "provas" / ID
    for fonte in origem.rglob("*"):
        if fonte.is_file():
            alvo = destino / fonte.relative_to(origem)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(fonte.read_bytes())
    cel01 = destino / "validacao" / "casos" / "cel01.json"
    dados = json.loads(cel01.read_text(encoding="utf-8"))
    assert dados["casos"][0]["esperado"]["resultado"] == "casa"
    dados["casos"][0]["esperado"]["resultado"] = "nao_casa"
    cel01.write_text(json.dumps(dados), encoding="utf-8")

    prova = _prova(tmp_path, rehash=True)  # o manifesto falso acompanha a copia alterada
    definicoes = await rp.implantar_prova(
        engine_client, prova, k1_dir=tmp_path, chaves_do_repo=rp.chaves_de_decisao_do_repo(SPEC_DMN)
    )
    avaliador = rp.AvaliadorCibSeven(engine_client, definicoes)
    divergencias = await rp.executar_provas([prova], k1_dir=tmp_path, avaliador=avaliador)
    assert [(d.caso.rsplit("#", 1)[-1], d.esperado, d.obtido) for d in divergencias] == [
        ("C01T", "nao_casa", "casa")
    ]


async def test_provas_do_pacote_pinado_casam_no_motor(engine_client: httpx.AsyncClient) -> None:
    resultado = await rp.executar_provas_do_pin(
        PIN,
        k1_dir=PIN_K1_DIR,
        motor=rp.MotorCibSeven(engine_client),
        chaves_do_repo=rp.chaves_de_decisao_do_repo(SPEC_DMN),
    )
    if resultado.estado == rp.ESTADO_SEM_PACOTE:
        # Sem pacote nao ha prova a rodar: o pin tem de DIZER isso (nada de pasta k1/ nem artefatos).
        lock = json.loads(PIN.read_text(encoding="utf-8"))
        assert (lock["estado"], lock["manifest_pin"], lock["artifacts"]) == ("sem_pacote", None, [])
        assert (resultado.provas, resultado.casos, resultado.divergencias) == ((), 0, ())
        return
    assert resultado.estado == rp.ESTADO_FIXADO
    assert resultado.provas, "pacote fixado sem provas executadas"
    assert resultado.casos > 0, "pacote fixado sem casos avaliados no motor"
    assert [d.render() for d in resultado.divergencias] == []
