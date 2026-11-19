"""Chancela de regras (ADR-0080/DL-0194 PROPOSTOS): JSON canônico, extratores e hash por regra.

Os vetores em `vetores/` são FIXOS: se um digest abaixo mudar, mudou o significado de toda aprovação
já transcrita. Mudar um vetor é mudar o formato `maezo-regra-hash.v1` — exige versão nova.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from maezo.portal.engine.profile import canonicalize as canonicalize_do_portal
from maezo.regras.canonico import INTEIRO_SEGURO_MAX, CanonicoError, canonicalizar, sha256_canonico
from maezo.regras.catalogo.extratores import FonteError, extrair, fonte_sha256
from maezo.regras.catalogo.hash import SCHEMA_HASH, material_do_hash, regra_sha256
from maezo.regras.catalogo.schema import Regra

_VETORES = Path(__file__).parent / "vetores"

VETORES_DE_FONTE: dict[str, tuple[dict[str, Any], str]] = {
    "dmn_linha": (
        {"arquivo": "tabela.dmn", "regra": "r1"},
        "c1e5b149df583fd4d03997d4d0ee983e7ba7ac95fd559a6a810f29f71f820446",
    ),
    "bpmn_elemento": (
        {"arquivo": "processo.bpmn", "elemento": "timer_sla"},
        "134222359b5e1b40263edda89903449f2417aa6bf7b11ccfe247e4c6a8c8fa44",
    ),
    "parametro_politica": (
        {"arquivo": "politica.yaml", "caminho": ["politica", "reembolso", "params"]},
        "8d7e5dba3866ca1b6d5b8efc5896460166c820a10a7d8e9ab84f58ea520dcc21",
    ),
    "python_constante": (
        {"arquivo": "constantes.py", "nome": "LIMITE"},
        "3bb5deaf3bab5af66348f211ffb5973e5b1f0795f0c21754550ca44ff9f84ae8",
    ),
    "clausula_documento": (
        {"arquivo": "documento.md", "secao": "## 1. Primeira"},
        "894b917bb4dd32e64dc826a6c2bd80215f1daecbd9289531e1f71ee5268c777d",
    ),
    "texto_beneficiario": (
        {"arquivo": "politica.yaml", "caminho": ["mensagens", "aviso"]},
        "89d9a25937d223596d8b7c71803bb913c7f9b91c180e6befebbf3c5827905b7e",
    ),
}

REGRA_VETOR = Regra(
    id="TESTE.VETOR.UM",
    alias=None,
    estado_declarado="legado_pendente",
    tipo="parametro_politica",
    fonte=({"arquivo": "politica.yaml", "caminho": ["politica", "reembolso", "params", "teto_brl"]},),
    sha256="",
    aprovam=("financas", "dono"),
    ciencia=("po",),
    titulo="Titulo do vetor",
    regra="Regra do vetor de teste.",
    exemplo="Exemplo com a Ana.",
    orientacao="",
    o_que_e="Aprovar o vetor de teste.",
    o_que_nao_e="Nao liga nada em lugar nenhum.",
    norma=None,
    validade="P1Y",
    editavel=(),
    historico_de_mudanca=(),
    aprovacoes=(),
    liberacoes=(),
    comprovacoes=(),
)
HASH_REGRA_VETOR = "02a64211c17a4eaa0d6970f32ae63dd86bfbcafc5cf4a0c43680bdb7b72631d1"


# --- JSON canonico -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    [
        None,
        True,
        "ção",
        ["b", "a"],
        {"b": "1", "a": {"z": None, "é": [True, False]}},
        {"\U0001f600": "x", "￿": "y"},  # ordem por unidade UTF-16, nao por code point
    ],
)
def test_canonico_igual_ao_perfil_do_portal_sem_numeros(valor: Any) -> None:
    """Paridade: para todo valor sem numero, os bytes sao os do perfil RFC 8785 do portal."""
    assert canonicalizar(valor) == canonicalize_do_portal(valor)


def test_canonico_aceita_inteiro_seguro_e_recusa_float_e_inteiro_grande() -> None:
    assert canonicalizar({"teto": 300, "n": -1}) == b'{"n":-1,"teto":300}'
    assert canonicalizar(INTEIRO_SEGURO_MAX) == str(INTEIRO_SEGURO_MAX).encode()
    with pytest.raises(CanonicoError):
        canonicalizar(INTEIRO_SEGURO_MAX + 1)
    with pytest.raises(CanonicoError):
        canonicalizar(0.6)
    with pytest.raises(CanonicoError):
        canonicalizar("\ud800")
    with pytest.raises(CanonicoError):
        canonicalizar({1: "chave nao texto"})


# --- vetores fixos por tipo de fonte --------------------------------------------------------------


@pytest.mark.parametrize("tipo", sorted(VETORES_DE_FONTE))
def test_vetor_fixo_de_cada_tipo_de_fonte(tipo: str) -> None:
    fonte, esperado = VETORES_DE_FONTE[tipo]
    assert fonte_sha256(_VETORES, tipo, fonte) == esperado


def test_vetor_fixo_da_regra_inteira() -> None:
    assert regra_sha256(_VETORES, REGRA_VETOR) == HASH_REGRA_VETOR


@pytest.fixture
def copia(tmp_path: Path) -> Path:
    destino = tmp_path / "v"
    shutil.copytree(_VETORES, destino)
    return destino


@pytest.mark.parametrize("tipo", sorted(VETORES_DE_FONTE))
def test_crlf_nao_muda_nenhum_digest(copia: Path, tipo: str) -> None:
    """Checkout Windows (`core.autocrlf=true`) e CI Linux concordam (RNF-19, como a DL-0148)."""
    for arquivo in copia.iterdir():
        arquivo.write_bytes(arquivo.read_bytes().replace(b"\n", b"\r\n"))
    fonte, esperado = VETORES_DE_FONTE[tipo]
    assert fonte_sha256(copia, tipo, fonte) == esperado


def test_regra_dmn_muda_quando_a_coluna_muda_e_nao_quando_outra_linha_muda(copia: Path) -> None:
    dmn = copia / "tabela.dmn"
    original = dmn.read_text(encoding="utf-8")
    fonte, esperado = VETORES_DE_FONTE["dmn_linha"]
    dmn.write_text(original.replace('"HUMANO"', '"PESSOA"'), encoding="utf-8", newline="\n")
    assert fonte_sha256(copia, "dmn_linha", fonte) == esperado, "outra linha nao muda r1"
    dmn.write_text(original.replace('label="valor"', 'label="valor pedido"'), encoding="utf-8", newline="\n")
    assert fonte_sha256(copia, "dmn_linha", fonte) != esperado, "coluna mudou: r1 revalida"


def test_c14n_ignora_reindentacao_do_xml(copia: Path) -> None:
    dmn = copia / "tabela.dmn"
    dmn.write_text(
        dmn.read_text(encoding="utf-8").replace('<rule id="r1">', '<rule id="r1">\n   '), encoding="utf-8"
    )
    assert (
        fonte_sha256(copia, "dmn_linha", VETORES_DE_FONTE["dmn_linha"][0]) == VETORES_DE_FONTE["dmn_linha"][1]
    )


def test_clausula_vai_ate_o_proximo_cabecalho_de_nivel_menor_ou_igual() -> None:
    trecho = extrair(_VETORES, "clausula_documento", {"arquivo": "documento.md", "secao": "## 1. Primeira"})
    assert b"### 1.1 Sub" in trecho and b"## 2. Segunda" not in trecho
    assert b"## nao e cabecalho" in trecho, "cabecalho dentro de bloco de codigo nao corta"


def test_localizador_entra_no_digest() -> None:
    """Apontar para outra chave com o MESMO valor muda o hash (a aprovacao de um zero nao vale para outro)."""
    a = fonte_sha256(_VETORES, "python_constante", {"arquivo": "constantes.py", "nome": "LIMITE"})
    b = sha256_canonico(
        {
            "tipo": "python_constante",
            "localizador": {"arquivo": "constantes.py", "nome": "OUTRO"},
            "conteudo_sha256": a,
        }
    )
    assert a != b


@pytest.mark.parametrize(
    ("tipo", "fonte", "trecho"),
    [
        ("dmn_linha", {"arquivo": "tabela.dmn", "regra": "r9"}, "encontrada 0"),
        ("bpmn_elemento", {"arquivo": "processo.bpmn", "elemento": "nada"}, "encontrado 0"),
        ("parametro_politica", {"arquivo": "politica.yaml", "caminho": ["politica", "x"]}, "nao resolve"),
        ("python_constante", {"arquivo": "constantes.py", "nome": "NADA"}, "atribuida 0"),
        ("clausula_documento", {"arquivo": "documento.md", "secao": "## 9. Nada"}, "encontrado 0"),
        ("clausula_documento", {"arquivo": "documento.md", "secao": "Primeira"}, "linha de cabecalho"),
        ("texto_beneficiario", {"arquivo": "politica.yaml", "caminho": ["politica"]}, "nao e' texto"),
        ("parametro_politica", {"arquivo": "../fora.yaml", "caminho": ["a"]}, "sem '..'"),
        ("parametro_politica", {"arquivo": "nao-existe.yaml", "caminho": ["a"]}, "nao existe"),
    ],
)
def test_fonte_irresolvivel_falha_fechada(tipo: str, fonte: dict[str, Any], trecho: str) -> None:
    with pytest.raises(FonteError, match=trecho):
        fonte_sha256(_VETORES, tipo, fonte)


def test_xml_com_dtd_e_recusado(copia: Path) -> None:
    (copia / "mal.dmn").write_text(
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', encoding="utf-8"
    )
    with pytest.raises(FonteError, match="XML recusado"):
        fonte_sha256(copia, "dmn_linha", {"arquivo": "mal.dmn", "regra": "r1"})


def test_float_em_politica_e_recusado(copia: Path) -> None:
    (copia / "f.yaml").write_text("a: 0.6\n", encoding="utf-8")
    with pytest.raises(FonteError, match="fora do JSON canonico"):
        fonte_sha256(copia, "parametro_politica", {"arquivo": "f.yaml", "caminho": ["a"]})


# --- o que entra e o que nao entra no hash ---------------------------------------------------------


@pytest.mark.parametrize(
    "mudanca",
    [
        {"titulo": "Outro titulo"},
        {"regra": "Outra regra do vetor."},
        {"exemplo": "Outro exemplo com o Joao."},
        {"orientacao": "Agora com orientacao."},
        {"o_que_e": "Aprovar outra coisa."},
        {"o_que_nao_e": "Nao faz outra coisa."},
        {"aprovam": ("financas",)},
        {"ciencia": ()},
        {"editavel": ({"campo": "x"},)},
    ],
)
def test_campo_que_entra_no_hash_muda_o_hash(mudanca: dict[str, Any]) -> None:
    assert regra_sha256(_VETORES, replace(REGRA_VETOR, **mudanca)) != HASH_REGRA_VETOR


@pytest.mark.parametrize(
    "mudanca",
    [
        {"aprovacoes": ({"papel": "financas"},)},
        {"liberacoes": ({"liberador": "x"},)},
        {"comprovacoes": ({"x": 1},)},
        {"estado_declarado": "ativa"},
        {"historico_de_mudanca": ({"pr": 1},)},
        {"alias": "TST-01"},
        {"aprovam": ("dono", "financas")},  # ordem nao importa: conjunto ordenado no hash
    ],
)
def test_campo_fora_do_hash_nao_muda_o_hash(mudanca: dict[str, Any]) -> None:
    """Transcrever aprovacao nao pode mudar o hash que ela aprova (laço)."""
    assert regra_sha256(_VETORES, replace(REGRA_VETOR, **mudanca)) == HASH_REGRA_VETOR


def test_material_do_hash_tem_o_schema_versionado() -> None:
    material = material_do_hash(REGRA_VETOR, ["0" * 64])
    assert material["schema"] == SCHEMA_HASH == "maezo-regra-hash.v1"
    assert material["norma_sha256"] is None
