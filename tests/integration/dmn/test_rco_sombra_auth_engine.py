"""Sombra do K1 no portao de criterios do AUTH (DL-0187) no MOTOR REAL: entradas de hoje nunca sao adversas.

PR-2 (`tools.workers.auth_rco`) liga a sombra de `composicao_carencia_cpt` ao AUTH com o `FakeDmnTransport`
nos testes unitarios. Aqui a prova e no CIB Seven, com os DMN REAIS do recorte do K1 2.3.0
(`tests/fixtures/rco/k1-2.3.0-recorte/dmn/`, aed187d4; o sha de cada arquivo e conferido contra o
manifesto do recorte antes de implantar), sem `FakeDmnTransport`:

1. as entradas que a sombra do AUTH monta HOJE (`auth_rco.fatos_carencia_cpt_auth` com variaveis de start
   representativas, pelo montador real e pelas decisoes e pelo dicionario de entradas reais do recorte) sao
   quase todas nulas: so `dataAtendimento` pode vir preenchida, e `caraterAtendimento` e SEMPRE nula (regra 6
   do montador: a semente de start `carater_atendimento`, inclusive `eletivo`, o unico valor que casa as
   linhas `negar`, nunca entra). Avaliadas pelo `CibSevenDmnTransport` e compostas por
   `rco_composicao.compor_async(..., modo="sombra")` com o vocabulario do pacote, NENHUMA linha de nenhum
   membro e adversa (`combinacao.adversas` do `vocabulario.json`, mais o piso `ADVERSAS_PISO`) e o grupo
   escolhe a `revisao_humana` da carencia (fila `cadastro`). Matriz: sem `data_atendimento`, com ela, e a
   semente de carater ausente, `eletivo` e `urgencia`;
2. o caminho inteiro da sombra do AUTH (`auth_rco.avaliar_em_sombra_auth`, que chama
   `rco_sombra.avaliar_composicao_em_sombra` e `rco_sombra.avaliar_em_sombra`) com o transporte real contra
   o motor, com o pin carregado pelo leitor de PRODUCAO (`rco_pin.load_rco_rules_pin`) de um lock em
   `tmp_path`: o desfecho da composicao e `composicao_escolhida`, a vencedora e a carencia, e o
   `composicaoK1` registrado (`modo=sombra`) nao e adverso; nenhum aviso de composicao vira efeito;
3. `rco_geral_elegibilidade`: o recorte NAO traz o DMN dela (so `geral_carencia.dmn` e `geral_cpt.dmn`).
   Nada e fabricado: o pin deste teste nao a contem e a sombra devolve `sem_pacote` para ela, sem chamar o
   motor. A prova dela no motor fica para quando o recorte trouxer o arquivo real.

O esperado vem da tabela do rco (nulo nunca nega nem glosa) e do contrato da sombra; nunca e ajustado ao que
o motor devolve. Sem motor alcancavel o pacote PULA com motivo explicito (`tests/integration/conftest.py`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.adapters.amh.contract import load_contract_pin
from maezo.platform import rco_ativacao as at
from maezo.platform import rco_composicao as cmp
from maezo.platform import rco_provas as rp
from maezo.tools.workers import auth_rco, rco_pin, rco_sombra
from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers._audit_ctx import collect_dmn_versions, collect_rco_composicoes
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform import rco_k1_sintetico as sint

pytestmark = pytest.mark.integration

SPEC_DMN = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"
CARENCIA, CPT = rec.ORDEM
GRUPO = auth_rco.COMPOSICAO_CARENCIA_CPT
ELEGIBILIDADE = auth_rco.DECISAO_ELEGIBILIDADE
TOPICO = "operadora.auth.validate_auto_criteria"
DATA = "2026-11-03"
_AUSENTE = object()
#: Variaveis de start do AUTH (sinteticas, sem dado de caso), sem `data_atendimento` e `carater_atendimento`.
_START_BASE: dict[str, Any] = {
    "tenant_id": "amh",
    "numero_guia_tiss": "GUIA-SINT-PR3-0001",
    "beneficiario_id": "BEN-SINT-PR3-0001",
    "prestador_id": "PREST-SINT-PR3-0001",
    "valor_estimado_brl": 180.0,
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "tipo_procedimento": "eletivo",
    "dias_desde_adesao": 400,
    "cpt_declarada": False,
}


def _adversas() -> frozenset[str]:
    """As decisoes adversas: as do `vocabulario.json` do pacote, nunca menos que o piso do Maezo."""
    vocab = rec.vocabulario()
    assert vocab.adversas, "o vocabulario 2.3.0 declara combinacao.adversas"
    return frozenset(vocab.adversas) | cmp.ADVERSAS_PISO


def _start(data: object, carater: object) -> dict[str, Any]:
    pv = dict(_START_BASE)
    if data is not _AUSENTE:
        pv["data_atendimento"] = data
    if carater is not _AUSENTE:
        pv["carater_atendimento"] = carater
    return pv


def _uniao(montadas: Mapping[str, mt.EntradasMontadas]) -> dict[str, Any]:
    """A mesma uniao de `rco_sombra.avaliar_composicao`: mesmo nome com valor ou tipo diferente e conflito."""
    uniao: dict[str, Any] = {}
    for m in montadas.values():
        for nome, valor in m.variaveis.items():
            if nome in uniao:
                assert type(uniao[nome]) is type(valor) and uniao[nome] == valor, f"conflito em {nome}"
            else:
                uniao[nome] = valor
    return uniao


@pytest.fixture
async def implantadas(engine_client: httpx.AsyncClient) -> dict[str, str]:
    """Implanta os dois DMN REAIS do recorte (sha conferido); devolve `{decisao: id da definicao vigente}`."""
    colisao = sorted(set(rec.ORDEM) & rp.chaves_de_decisao_do_repo(SPEC_DMN))
    assert colisao == [], f"decisoes do recorte colidem com o repo: {colisao}"
    manifesto = rec.manifesto()
    arquivos: dict[str, tuple[str, bytes, str]] = {}
    for decisao in rec.ORDEM:
        caminho = rec.dmn(decisao)
        assert rec.sha256_prefixado(caminho) == manifesto.decisoes[decisao].sha256
        arquivos[caminho.name] = (caminho.name, caminho.read_bytes(), "text/xml")
    resp = await engine_client.post(
        "/deployment/create",
        data={
            "deployment-name": "rco-k1-2.3.0-sombra-auth",
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


# --- 1. as entradas de hoje, no motor, compostas em sombra ------------------------------------------------


@pytest.mark.parametrize(
    "carater", [_AUSENTE, "eletivo", "urgencia"], ids=["sem_carater", "eletivo", "urgencia"]
)
@pytest.mark.parametrize("data", [_AUSENTE, DATA], ids=["sem_data", "com_data"])
async def test_entradas_de_hoje_no_motor_nunca_escolhem_adversa(
    dmn: CibSevenDmnTransport, implantadas: dict[str, str], data: object, carater: object
) -> None:
    pv = _start(data, carater)
    fatos = auth_rco.fatos_carencia_cpt_auth(pv)(load_contract_pin().versao_manifesto_pinada)
    manifesto, dicionario = rec.manifesto(), rec.dicionario()
    montadas = {d: mt.montar_entradas(manifesto.decisoes[d], dicionario, fatos) for d in rec.ORDEM}
    # regra 6: o carater nunca entra; so `dataAtendimento` pode vir preenchida (o resto nao tem produtor)
    for m in montadas.values():
        assert m.variaveis["caraterAtendimento"] is None
    preenchidas = {n for m in montadas.values() for n, v in m.variaveis.items() if v is not None}
    assert preenchidas == (set() if data is _AUSENTE else {"dataAtendimento"})
    entradas = _uniao(montadas)

    vocab = rec.vocabulario()

    async def avaliador(decisao: str, valores: Mapping[str, Any]) -> tuple[list[dict[str, Any]], DmnVersion]:
        linhas, versao = await dmn.evaluate(decisao, dict(valores))
        return [dict(linha) for linha in linhas], versao

    resultado = await cmp.compor_async(
        rec.composicao(),
        avaliador,
        entradas,
        modo=cmp.MODO_SOMBRA,
        ordem_restricao=vocab.ordem_restricao,
        sinais_auditoria=vocab.sinais_auditoria,
        adversas=vocab.adversas,
        adversa_exige_preenchidos=vocab.adversa_exige_preenchidos,
    )

    adversas = _adversas()
    assert {d: v.id for d, v in resultado.versoes.items()} == implantadas
    assert set(resultado.veredictos) == set(rec.ORDEM)
    for decisao, linhas in resultado.veredictos.items():
        assert linhas, f"{decisao}: o motor nao devolveu linha"
        assert not [ln for ln in linhas if ln.get("decisao") in adversas], (decisao, linhas)
    # o grupo escolhe a revisao humana da carencia (nulo -> revisao_humana, fila cadastro), nunca adversa
    assert resultado.desfecho == cmp.DESFECHO_ESCOLHIDO, resultado.motivo
    assert resultado.decisao_vencedora == CARENCIA
    assert len(resultado.linhas) == 1
    vencedora = resultado.linhas[0]
    assert vencedora["decisao"] == "revisao_humana" and vencedora["decisao"] not in adversas, vencedora
    assert vencedora["fila"] == "cadastro", vencedora


# --- 2 e 3. o caminho inteiro da sombra do AUTH contra o motor ----------------------------------------------


def _montar_pacote_real(raiz: Path) -> None:
    """Pacote pinado so com as DUAS decisoes de que o recorte traz o DMN real (bytes conferidos pelo sha).

    Molde: `_montar_pacote` de `tests/unit/tools/workers/test_auth_rco_sombra_efeito.py`, SEM a elegibilidade
    (la ela entra com um DMN sintetico, que aqui nao vai ao motor). Itens do pacote que nao sao do grupo
    (prova do motor, exemplo) sao os do pacote sintetico (`rco_k1_sintetico`)."""
    manifesto = sint.montar(raiz)
    rco = raiz / sint.RULES_DIR
    k1 = raiz / sint.K1_DIR
    for arquivo in rco.glob("*.dmn"):
        arquivo.unlink()
    real = json.loads((rec.RECORTE / "manifesto.json").read_bytes())
    decisoes = []
    for d in real["decisoes"]:
        if d["id"] not in rec.ORDEM:
            continue
        raw = (rec.RECORTE / d["arquivo"]).read_bytes()
        assert sint.sha(raw) == d["sha256"]  # os bytes reais, sem alteracao
        (rco / f"{d['id_original']}.dmn").write_bytes(raw)
        decisoes.append(d)
    manifesto["decisoes"] = sorted(decisoes, key=lambda d: d["id"])
    manifesto["formato_manifesto"] = "2.3.0"
    manifesto["composicoes"] = [c for c in real["composicoes"] if c["nome"] == GRUPO]
    for nome in ("vocabulario.json", "k6-filas.json"):
        (k1 / nome).write_bytes((rec.RECORTE / nome).read_bytes())
    manifesto["vocabulario"] = real["vocabulario"]
    manifesto["k6"] = real["k6"]
    raw_entradas = (rec.RECORTE / "entradas-recorte.json").read_bytes()
    dic = json.loads(raw_entradas)
    (k1 / "entradas.json").write_bytes(raw_entradas)
    manifesto["dicionario_entradas"] = {
        "path": "entradas.json",
        "sha256": dic["sha256_conteudo"],
        "sha256_arquivo": sint.sha(raw_entradas),
        "versao": dic["versao"],
    }
    manifesto["exemplos_sinteticos"][0]["decisao"] = CARENCIA
    lock = sint.lock(raiz)
    lock["artifacts"] = [
        {"path": d["caminho_consumidor"], "sha256": d["sha256"]} for d in manifesto["decisoes"]
    ]
    sint.gravar_lock(raiz, lock)
    sint.regravar(raiz, manifesto)


@pytest.fixture
def pin_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[rco_pin.RcoRulesPin]:
    _montar_pacote_real(tmp_path)
    pin = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    at.limpar_cache_estados()
    yield pin
    at.limpar_cache_estados()


def test_recorte_nao_traz_o_dmn_da_elegibilidade() -> None:
    """Item 3: a elegibilidade esta no manifesto real, sem o DMN dela no recorte; nada e fabricado."""
    entrada = rec.manifesto().decisoes[ELEGIBILIDADE]
    assert not (rec.RECORTE / entrada.arquivo).exists()
    presentes = sorted(p.name for p in (rec.RECORTE / "dmn").glob("*.dmn"))
    assert presentes == ["geral_carencia.dmn", "geral_cpt.dmn"]
    assert cmp.composicao_de(rec.manifesto(), ELEGIBILIDADE) is None  # avaliada sozinha, nao no grupo


@pytest.mark.parametrize("data", [_AUSENTE, DATA], ids=["sem_data", "com_data"])
async def test_sombra_do_auth_inteira_no_motor_nao_e_adversa(
    dmn: CibSevenDmnTransport,
    implantadas: dict[str, str],
    pin_real: rco_pin.RcoRulesPin,
    monkeypatch: pytest.MonkeyPatch,
    data: object,
) -> None:
    assert set(pin_real.tables) == set(rec.ORDEM) and ELEGIBILIDADE not in pin_real.tables
    for decisao in rec.ORDEM:  # o pin atesta os mesmos bytes que foram implantados
        pinada = pin_real.tables[decisao].decisao
        assert pinada is not None and pinada.sha256 == rec.manifesto().decisoes[decisao].sha256
        # aquece o cache de versao do transporte (o orcamento da sombra cobre so a avaliacao)
        await dmn.evaluate(decisao, {})
    desfechos: list[str] = []
    monkeypatch.setattr(rco_sombra, "record_rco_sombra", desfechos.append)

    with collect_dmn_versions() as versoes, collect_rco_composicoes() as composicoes:
        # sincrono como no harness (fora do laco: `evaluate_sync` abre o seu), com o orcamento de producao
        await asyncio.to_thread(auth_rco.avaliar_em_sombra_auth, dmn, _start(data, "eletivo"), topico=TOPICO)

    assert desfechos == [rco_sombra.COMPOSICAO_ESCOLHIDA, rco_sombra.SEM_PACOTE]
    adversas = _adversas()
    assert set(versoes) == set(rec.ORDEM)  # a elegibilidade nao foi ao motor
    for membro in rec.ORDEM:
        entrada = versoes[membro]
        assert entrada["modo"] == "sombra"
        assert entrada["composicaoK1"] == {
            "nome": GRUPO,
            "modo": "sombra",
            "desfecho": cmp.DESFECHO_ESCOLHIDO,
            "decisaoVencedora": CARENCIA,
            "motivo": None,
            "peloPadrao": False,
        }
        assert "sinaisAuditoria" not in entrada  # sem sinal: nada para avisar
    # a vencedora registrada e a linha nao adversa da carencia; nenhum aviso de efeito
    assert versoes[CARENCIA].get("idRegra"), versoes[CARENCIA]
    registradas = list(composicoes.values())
    assert [(r.modo, r.decisao_vencedora) for r in registradas] == [("sombra", CARENCIA)]
    assert all(r.modo != cmp.MODO_EFEITO for r in registradas)
    assert "revisao_humana" not in adversas
