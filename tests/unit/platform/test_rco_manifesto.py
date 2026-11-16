"""Leitor do manifesto K1 do rco (`maezo.platform.rco_manifesto`, MZO-2a; contrato-k1.md §11 itens 1-6).

Contra o pacote SINTETICO de `rco_k1_sintetico.py` (nenhum byte real do rco): caminho feliz e cada
recusa fail-closed — digest cru, formato MAJOR/MINOR, `origem.sujo`, campo desconhecido ou ausente,
bytes nao canonicos, prefixo `rco_`, caminho nao plano, anexo adulterado, coluna de saida fora do
`saidas[]` e saida obrigatoria ausente.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from maezo.platform import rco_manifesto as k1
from tests.unit.platform import rco_k1_sintetico as sint


@pytest.fixture
def pacote(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    return tmp_path, sint.montar(tmp_path)


def _ler(raiz: Path) -> k1.ManifestoK1:
    return k1.ler_manifesto((raiz / sint.K1_DIR / "manifesto.json").read_bytes())


def _verificar(raiz: Path) -> list[str]:
    return k1.verificar_pacote(_ler(raiz), k1_dir=raiz / sint.K1_DIR, rules_dir=raiz / sint.RULES_DIR)


def test_constantes_batem_com_o_gate() -> None:
    from scripts.ci import verify_rco_rules_pin as gate

    assert k1.K1_DIR == gate.K1_DIR
    assert k1.K1_MANIFESTO_PATH == gate.K1_MANIFEST_PATH
    assert k1.RULES_DIR == gate.RULES_DIR


def test_caminho_feliz_expoe_o_que_a_auditoria_usa(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    lido = _ler(raiz)
    assert lido.formato_manifesto == "1.0.0"
    assert (lido.repo, lido.commit_sha) == (sint.REPO, sint.COMMIT)
    assert set(lido.decisoes) == {"rco_sint_a", "rco_sint_b"}
    a = lido.decisoes["rco_sint_a"]
    assert a.caminho_consumidor == f"{sint.RULES_DIR}/sint_a.dmn"
    assert a.sha256 == manifesto["decisoes"][0]["sha256"]  # = shaDmn
    assert a.regras["SINT-A-0002"].versao_regra == "1.0.1"
    assert a.regras["SINT-A-0002"].sha256_yaml.startswith("sha256:")
    assert lido.decisoes["rco_sint_b"].saidas[-1] == "acaoSintetica"
    assert {p: x.nome for p, x in lido.anexos.items()} == {
        "vocabulario": "vocabulario.json",
        "entradas": "entradas.json",
        "k6": "k6-filas.json",
    }
    assert lido.exemplos == ("exemplos/rco_sint_a.json",)  # listado, nunca vendorizado
    assert _verificar(raiz) == []


def _mutacao(caminho: list[Any], valor: Any) -> Callable[[dict[str, Any]], None]:
    def aplicar(m: dict[str, Any]) -> None:
        alvo: Any = m
        for chave in caminho[:-1]:
            alvo = alvo[chave]
        if valor is _REMOVER:
            del alvo[caminho[-1]]
        else:
            alvo[caminho[-1]] = valor

    return aplicar


_REMOVER = object()
_HEX_CRU = "a" * 64


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mutacao(["origem", "sujo"], True), "origem.sujo"),
        (_mutacao(["origem", "sujo"], False), "origem.sujo"),  # a chave, presente, ja recusa
        (_mutacao(["formato_manifesto"], "3.0.0"), "formato_manifesto 3.0.0"),
        (_mutacao(["formato_manifesto"], "2.1.0"), "formato_manifesto 2.1.0"),
        (_mutacao(["formato_manifesto"], "2.0.0"), "desconhecidos ['ratificavel_automatico']"),
        (_mutacao(["formato_manifesto"], "1.1.0"), "formato_manifesto 1.1.0"),
        (_mutacao(["formato_manifesto"], "1"), "formato_manifesto"),
        (_mutacao(["campo_novo"], 1), "desconhecidos ['campo_novo']"),
        (_mutacao(["k6"], _REMOVER), "faltam ['k6']"),
        (_mutacao(["decisoes", 0, "extra"], 1), "desconhecidos ['extra']"),
        (_mutacao(["decisoes", 0, "regras", 0, "nova"], 1), "desconhecidos ['nova']"),
        (_mutacao(["decisoes", 0, "regras", 0, "sha256_yaml"], _REMOVER), "faltam ['sha256_yaml']"),
        (_mutacao(["decisoes", 0, "saidas", 0, "novo"], 1), "desconhecidos ['novo']"),
        (_mutacao(["vocabulario", "sha256"], _HEX_CRU), "vocabulario.sha256"),
        (_mutacao(["decisoes", 0, "sha256"], _HEX_CRU), "decisoes[0].sha256"),
        (_mutacao(["decisoes", 0, "regras", 0, "sha256_yaml"], _HEX_CRU), "sha256_yaml"),
        (_mutacao(["decisoes", 0, "regras", 0, "versaoRegra"], "v1"), "versaoRegra"),
        (_mutacao(["decisoes", 0, "regras", 0, "idRegra"], "regra 1"), "idRegra"),
        (_mutacao(["decisoes", 0, "id"], "sint_a"), "'rco_' + id_original"),
        (_mutacao(["prefixo_decisao"], "xyz_"), "prefixo_decisao"),
        (_mutacao(["decisoes", 0, "arquivo"], "dmn/sub/sint_a.dmn"), "sem subpasta"),
        (_mutacao(["decisoes", 0, "caminho_consumidor"], "spec/processes/dmn/rco/sub/sint_a.dmn"), "plano"),
        (_mutacao(["vocabulario", "path"], "../vocabulario.json"), "plano"),
        (_mutacao(["origem", "commit_sha"], "abc"), "commit_sha"),
        (_mutacao(["decisoes"], []), "lista nao vazia"),
    ],
)
def test_forma_invalida_reprova(
    pacote: tuple[Path, dict[str, Any]], mutar: Callable[[dict[str, Any]], None], trecho: str
) -> None:
    raiz, manifesto = pacote
    alterado = copy.deepcopy(manifesto)
    mutar(alterado)
    with pytest.raises(k1.ManifestoK1InvalidoError) as excinfo:
        k1.ler_manifesto(sint.canonico(alterado))
    assert trecho in str(excinfo.value)


def test_decisao_repetida_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    _, manifesto = pacote
    manifesto["decisoes"].append(copy.deepcopy(manifesto["decisoes"][0]))
    with pytest.raises(k1.ManifestoK1InvalidoError, match="repetido"):
        k1.ler_manifesto(sint.canonico(manifesto))


@pytest.mark.parametrize(
    "raw",
    [
        b"{nao e json",
        b'{"a":1,"a":2}',
        "﻿".encode() + b"{}",
    ],
)
def test_json_ilegivel_ou_chave_repetida_reprova(raw: bytes) -> None:
    with pytest.raises(k1.ManifestoK1InvalidoError):
        k1.ler_manifesto(raw)


def test_bytes_nao_canonicos_reprovam(pacote: tuple[Path, dict[str, Any]]) -> None:
    _, manifesto = pacote
    with pytest.raises(k1.ManifestoK1InvalidoError, match="canonicos"):
        k1.ler_manifesto(sint.canonico(manifesto) + b"\n")


@pytest.mark.parametrize("anexo", ["vocabulario.json", "entradas.json", "k6-filas.json"])
def test_anexo_adulterado_ou_ausente_reprova(pacote: tuple[Path, dict[str, Any]], anexo: str) -> None:
    raiz, _ = pacote
    caminho = raiz / sint.K1_DIR / anexo
    caminho.write_bytes(caminho.read_bytes() + b" ")
    assert any(anexo in p and "sha256" in p for p in _verificar(raiz))
    caminho.unlink()
    assert any(anexo in p and "ausente" in p for p in _verificar(raiz))


def test_dmn_adulterado_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote
    alvo = raiz / sint.RULES_DIR / "sint_a.dmn"
    alvo.write_bytes(alvo.read_bytes().replace(b"liberar", b"informar"))
    assert any("sint_a.dmn: sha256 diverge" in p for p in _verificar(raiz))


def _trocar_dmn(raiz: Path, manifesto: dict[str, Any], indice: int, raw: bytes) -> None:
    decisao = manifesto["decisoes"][indice]
    (raiz / decisao["caminho_consumidor"]).write_bytes(raw)
    decisao["sha256"] = sint.sha(raw)
    sint.regravar(raiz, manifesto)


def test_saida_extra_listada_no_saidas_passa_e_nao_listada_reprova(
    pacote: tuple[Path, dict[str, Any]],
) -> None:
    raiz, manifesto = pacote
    assert _verificar(raiz) == []  # rco_sint_b ja tem a extra `acaoSintetica`, listada
    linhas = [
        ['"liberar"', '""', '"SINT-A-0001"', '""', '""', '"x"'],
        ['"informar"', '""', '"SINT-A-0002"', '"cadastro"', '""', '"y"'],
    ]
    _trocar_dmn(
        raiz, manifesto, 0, sint.dmn_bytes("rco_sint_a", [*sint.SAIDAS_OBRIGATORIAS, "colunaNova"], linhas)
    )
    assert any("['colunaNova'] fora do saidas[]" in p for p in _verificar(raiz))


def test_saida_obrigatoria_ausente_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    saidas = [s for s in sint.SAIDAS_OBRIGATORIAS if s != "motivoTab38"]
    linhas = [['"liberar"', '"SINT-A-0001"', '""', '""'], ['"informar"', '"SINT-A-0002"', '"cadastro"', '""']]
    manifesto["decisoes"][0]["saidas"] = [
        s for s in manifesto["decisoes"][0]["saidas"] if s["nome"] != "motivoTab38"
    ]
    _trocar_dmn(raiz, manifesto, 0, sint.dmn_bytes("rco_sint_a", saidas, linhas))
    assert any("faltam as saidas obrigatorias ['motivoTab38']" in p for p in _verificar(raiz))


def test_saidas_do_manifesto_que_a_tabela_nao_tem_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    manifesto["decisoes"][0]["saidas"].append(
        {"nome": "fantasma", "nome_snake": "fantasma", "typeRef": "string"}
    )
    sint.regravar(raiz, manifesto)
    assert any("['fantasma'], ausentes da tabela" in p for p in _verificar(raiz))


def test_id_da_decisao_no_dmn_diferente_do_manifesto_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, manifesto = pacote
    linhas = [
        ['"liberar"', '""', '"SINT-A-0001"', '""', '""'],
        ['"informar"', '""', '"SINT-A-0002"', '"cadastro"', '""'],
    ]
    _trocar_dmn(raiz, manifesto, 0, sint.dmn_bytes("sint_a", sint.SAIDAS_OBRIGATORIAS, linhas))
    assert any("exatamente a decisao 'rco_sint_a'" in p for p in _verificar(raiz))


# ---------------------------------------------------------------------------------------------
# formato_manifesto MAJOR 2 (PROG-RCO-8 no rco; contrato 2.0.0 ainda nao publicado): por regra sai
# `ratificavel_automatico` e entram `ativacao` (conjunto fechado) e `exige_aditivo` (booleano).
# ---------------------------------------------------------------------------------------------


def _para_major2(manifesto: dict[str, Any], ativacao: object = "conferida", aditivo: object = True) -> None:
    manifesto["formato_manifesto"] = "2.0.0"
    for decisao in manifesto["decisoes"]:
        for regra in decisao["regras"]:
            del regra["ratificavel_automatico"]
            regra["ativacao"] = ativacao
            regra["exige_aditivo"] = aditivo


@pytest.mark.parametrize("ativacao", sorted(k1.ATIVACOES))
def test_major2_le_ativacao_e_exige_aditivo(pacote: tuple[Path, dict[str, Any]], ativacao: str) -> None:
    raiz, manifesto = pacote
    _para_major2(manifesto, ativacao)
    sint.regravar(raiz, manifesto)
    regra = _ler(raiz).decisoes["rco_sint_a"].regras["SINT-A-0001"]
    assert (regra.ativacao, regra.exige_aditivo, regra.versao_regra) == (ativacao, True, "1.0.0")
    assert _verificar(raiz) == []


def test_major1_nao_inventa_campos_do_major2(pacote: tuple[Path, dict[str, Any]]) -> None:
    raiz, _ = pacote
    regra = _ler(raiz).decisoes["rco_sint_a"].regras["SINT-A-0001"]
    assert (regra.ativacao, regra.exige_aditivo) == (None, None)


@pytest.mark.parametrize(
    ("ativacao", "aditivo", "trecho"),
    [
        ("automatica", True, "ativacao"),
        ("PLENA", True, "ativacao"),
        (None, True, "ativacao"),
        ("plena", "sim", "exige_aditivo"),
        ("plena", None, "exige_aditivo"),
    ],
)
def test_major2_valor_desconhecido_reprova(
    pacote: tuple[Path, dict[str, Any]], ativacao: object, aditivo: object, trecho: str
) -> None:
    _, manifesto = pacote
    _para_major2(manifesto, ativacao, aditivo)
    with pytest.raises(k1.ManifestoK1InvalidoError, match=trecho):
        k1.ler_manifesto(sint.canonico(manifesto))


def test_major2_com_ratificavel_automatico_reprova(pacote: tuple[Path, dict[str, Any]]) -> None:
    _, manifesto = pacote
    _para_major2(manifesto)
    manifesto["decisoes"][0]["regras"][0]["ratificavel_automatico"] = False
    with pytest.raises(k1.ManifestoK1InvalidoError, match="ratificavel_automatico"):
        k1.ler_manifesto(sint.canonico(manifesto))
