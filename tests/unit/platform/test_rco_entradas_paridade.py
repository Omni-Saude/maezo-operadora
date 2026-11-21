"""Paridade do leitor do dicionario K1 com o PUBLICADOR do rco (DL-0184).

O leitor (`maezo.platform.rco_entradas`) confere o valor de todo termo `parametro` e a
`referencia_por_tipo_guia` do dicionario 1.5.0 com as condicoes COPIADAS de `scripts/k1_entradas.py` do rco
no commit aed187d4 (`erros_parametro` e `erros_referencia_guia`): chave com `None` ou `""` e ausente,
`arquivo#chave` por `str(ref).partition("#")` e elemento do XSD por `"#" in v`, sem expressao regular.

A prova roda o MESMO conjunto de exemplos pelo leitor do Maezo (o caminho inteiro,
`ler_dicionario_entradas` sobre o recorte real do pacote) e pela funcao do proprio rco, carregada da fonte
no commit (copia byte a byte em `tests/fixtures/rco/fonte-aed187d4/k1_entradas.py.txt`, conferida pelo id
de blob do git, `git rev-parse aed187d4:scripts/k1_entradas.py`). O arquivo e o MESMO blob no commit
b8444f52 (dicionario 1.6.0, o do primeiro pin real; `COMMITS_RCO_COM_ESTE_BLOB`): a copia vale para os dois e
nao ha segunda pasta de fonte. Sem rede e sem dependencia nova: o
modulo do rco so importa a biblioteca padrao e `yaml`. Cada exemplo traz tambem o veredicto esperado, com a
linha de `validacao/teste_k1_entradas.py` do rco que o estabelece quando ha uma.

Divergencias CONHECIDAS, todas com o Maezo mais estrito (recusa o que o rco aceita), ficam listadas a parte
(`DIVERGENCIAS_MAIS_ESTRITAS`): recusar o pacote e o lado seguro. A unica no sentido contrario e a
existencia do arquivo de uma `referencia` (linha 153 do rco), que so o rco confere, contra a arvore dele: o
pacote nao leva o arquivo, e o montador recusa parametro publicado por `referencia` em formula que ele
calcula (`conferir_mapeamento`).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from maezo.platform import rco_entradas as ent
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "rco"
FONTE_RCO = FIXTURES / "fonte-aed187d4" / "k1_entradas.py.txt"
#: `git rev-parse aed187d4:scripts/k1_entradas.py` no rco (Omni-Saude/regras-contratos-operadora).
BLOB_RCO = "54d03c50d943aa37ce7c93441078ed340e3e2afd"
#: Commits do rco em que `git rev-parse <commit>:scripts/k1_entradas.py` da `BLOB_RCO` (conferido no rco,
#: 21/11/2026): aed187d4 (dicionario 1.5.0) e b8444f52 (1.6.0). O publicador nao mudou entre eles.
COMMITS_RCO_COM_ESTE_BLOB = (
    "aed187d4bada72e365381b48c6e96921d34c8815",
    "b8444f52e33eda556b5d2dab7837b046d5bd769e",
)
RECORTE_1_6_0 = FIXTURES / "entradas-1.6.0-recorte" / "entradas-1.6.0-recorte.json"
PRAZO = "prazoComprovacaoUsoVencido"
RECORTE = FIXTURES / "k1-2.3.0-recorte" / "entradas-recorte.json"
URGENCIA = "urgenciaAposVinteQuatroHorasCobertura"
PARAMETRO = "horasMinimasUrgencia"
#: Arquivos que as referencias aceitas citam: criados na raiz temporaria do rco (o rco confere que existem).
ARQUIVOS_CITADOS = ("regras/schema/regra.schema.json", "mk/pacote.mk", "a b")


def _blob_git(dados: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(dados) + dados, usedforsecurity=False).hexdigest()


@pytest.fixture(scope="module")
def rco() -> ModuleType:
    """O `scripts/k1_entradas.py` do rco em aed187d4, importado do arquivo (nunca reescrito)."""
    assert _blob_git(FONTE_RCO.read_bytes()) == BLOB_RCO
    nome = "rco_k1_entradas_aed187d4"
    loader = SourceFileLoader(nome, str(FONTE_RCO))
    spec = importlib.util.spec_from_file_location(nome, FONTE_RCO, loader=loader)
    assert spec is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    try:
        loader.exec_module(modulo)
    finally:
        sys.modules.pop(nome, None)
    return modulo


@pytest.fixture
def raiz_rco(tmp_path: Path) -> Path:
    for relativo in ARQUIVOS_CITADOS:
        (tmp_path / relativo).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relativo).write_text("", encoding="utf-8")
    return tmp_path


def _dados() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(RECORTE.read_text(encoding="utf-8"))
    return dados


def _formula(dados: dict[str, Any]) -> dict[str, Any]:
    formula: dict[str, Any] = next(e for e in dados["entradas"] if e["nome_dmn"] == URGENCIA)["formula"]
    return formula


def _maezo_aceita(dados: dict[str, Any]) -> bool:
    try:
        ent.ler_dicionario_entradas(json.dumps(dados, ensure_ascii=False).encode("utf-8"))
    except ManifestoK1InvalidoError:
        return False
    return True


# --- termo `parametro` ------------------------------------------------------------------------------
#: (campos de valor do termo, aceito?, de onde vem o veredicto no rco)
EXEMPLOS_PARAMETRO: list[tuple[dict[str, Any], bool, str]] = [
    ({"valor": 24, "unidade": "horas"}, True, "teste_k1_entradas.py:189"),
    ({"referencia": "regras/schema/regra.schema.json#properties"}, True, "teste_k1_entradas.py:190"),
    (
        {"referencia": ["regras/schema/regra.schema.json#a", "mk/pacote.mk#b"]},
        True,
        "teste_k1_entradas.py:191",
    ),
    ({"valor_por_contrato": "prazo do contrato vigente"}, True, "teste_k1_entradas.py:192"),
    ({"lacuna": "L-x-01"}, True, "teste_k1_entradas.py:193"),
    ({"valor": 0, "unidade": "dias"}, True, "k1_entradas.py:135 (0 nao e None nem vazio)"),
    ({"valor": 24, "unidade": "horas", "lacuna": None}, True, "k1_entradas.py:135 (None e ausente)"),
    ({"valor": 24, "unidade": "horas", "lacuna": ""}, True, "k1_entradas.py:135 (vazio e ausente)"),
    ({"lacuna": "L-x", "unidade": None, "valor": None}, True, "k1_entradas.py:135 e 145"),
    ({"referencia": []}, True, "k1_entradas.py:148-149 (lista vazia: o laco nao confere nada)"),
    ({"referencia": "a b#c"}, True, "k1_entradas.py:150-151 (espaco no arquivo e aceito)"),
    ({}, False, "teste_k1_entradas.py:194"),
    ({"valor": 24}, False, "teste_k1_entradas.py:195"),
    ({"valor": 24, "unidade": "semanas"}, False, "teste_k1_entradas.py:196"),
    ({"valor": "24", "unidade": "horas"}, False, "teste_k1_entradas.py:197"),
    ({"referencia": "mk/pacote.mk"}, False, "teste_k1_entradas.py:199"),
    ({"valor": 24, "unidade": "horas", "lacuna": "L-x"}, False, "teste_k1_entradas.py:200"),
    ({"valor": True, "unidade": "horas"}, False, "k1_entradas.py:142 (booleano nao e numero)"),
    ({"valor": None, "unidade": None}, False, "k1_entradas.py:135-136 (nenhum presente)"),
    ({"valor_por_contrato": ""}, False, "k1_entradas.py:135-136 (vazio e ausente)"),
    ({"lacuna": "L-x", "unidade": ""}, False, "k1_entradas.py:145 (`unidade` fora do valor so None)"),
    ({"referencia": "mk/pacote.mk#"}, False, "k1_entradas.py:151 (chave vazia)"),
    ({"referencia": "#chave"}, False, "k1_entradas.py:151 (arquivo vazio)"),
    ({"referencia": ["mk/pacote.mk#a", 5]}, False, "k1_entradas.py:150 (`str(5)` sem `#`)"),
]


def _com_parametro(campos: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """O recorte com o termo `horasMinimasUrgencia` trocado pelos campos do exemplo, e o termo trocado."""
    dados = _dados()
    termo = next(t for t in _formula(dados)["termos"] if t["nome"] == PARAMETRO)
    for chave in (*ent.ORDEM_CAMPOS_VALOR_PARAMETRO, "unidade"):
        termo.pop(chave, None)
    termo.update(copy.deepcopy(campos))
    return dados, termo


@pytest.mark.parametrize(("campos", "aceito", "origem"), EXEMPLOS_PARAMETRO)
def test_parametro_tem_o_veredicto_do_publicador(
    rco: ModuleType, raiz_rco: Path, campos: dict[str, Any], aceito: bool, origem: str
) -> None:
    dados, termo = _com_parametro(campos)
    assert (rco.erros_parametro("x", termo, raiz_rco) == []) is aceito, origem
    assert _maezo_aceita(dados) is aceito, origem


# --- `referencia_por_tipo_guia` -----------------------------------------------------------------------
def _mudar_item(indice: int, **campos: Any) -> Any:
    def aplicar(refs: list[dict[str, Any]]) -> None:
        refs[indice].update(campos)

    return aplicar


def _tirar(indice: int, chave: str) -> Any:
    def aplicar(refs: list[dict[str, Any]]) -> None:
        refs[indice].pop(chave)

    return aplicar


def _repetir_guia(refs: list[dict[str, Any]]) -> None:
    refs[1]["guia"] = refs[0]["guia"]


def _repetir_guia_com_espaco(refs: list[dict[str, Any]]) -> None:
    refs[1]["guia"] = f" {refs[0]['guia']} "


def _esvaziar(refs: list[dict[str, Any]]) -> None:
    refs.clear()


def _nada(refs: list[dict[str, Any]]) -> None:
    return None


#: (mudanca na lista publicada da urgencia, aceito?, de onde vem o veredicto no rco). Item 5: o de data nula.
EXEMPLOS_REFERENCIA: list[tuple[Any, bool, str]] = [
    (_nada, True, "teste_k1_entradas.py:210 (bem formada); a lista real do pacote"),
    (_mudar_item(0, lacuna=None), True, "k1_entradas.py:183 (lacuna None com data: sem exigencia)"),
    (_mudar_item(0, lacuna=""), True, "k1_entradas.py:183 (lacuna vazia com data: sem exigencia)"),
    (_mudar_item(0, data="a #b"), True, "k1_entradas.py:181 (so exige `#`)"),
    (_mudar_item(0, hora="tissX.xsd#y"), True, "k1_entradas.py:181"),
    (_tirar(5, "lacuna"), False, "teste_k1_entradas.py:216"),
    (_mudar_item(5, lacuna=None), False, "k1_entradas.py:183 (None e ausente)"),
    (_mudar_item(5, lacuna=""), False, "k1_entradas.py:183 (vazio e ausente)"),
    (_mudar_item(5, lacuna="   "), False, "k1_entradas.py:183 (`.strip()`)"),
    (_mudar_item(0, extra=1), False, "teste_k1_entradas.py:218"),
    (_tirar(0, "hora"), False, "k1_entradas.py:172 (falta chave)"),
    (_mudar_item(0, data="dataSolicitacao"), False, "teste_k1_entradas.py:220"),
    (_mudar_item(0, hora=5), False, "k1_entradas.py:181 (nao e texto)"),
    (_repetir_guia, False, "teste_k1_entradas.py:222"),
    (_repetir_guia_com_espaco, False, "k1_entradas.py:175-176 (repetida depois do `.strip()`)"),
    (_mudar_item(1, guia=""), False, "k1_entradas.py:175-176 (guia vazia)"),
    (_esvaziar, False, "k1_entradas.py:167-168 (lista vazia)"),
]


@pytest.mark.parametrize(("mudar", "aceito", "origem"), EXEMPLOS_REFERENCIA)
def test_referencia_por_tipo_guia_tem_o_veredicto_do_publicador(
    rco: ModuleType, mudar: Any, aceito: bool, origem: str
) -> None:
    dados = _dados()
    formula = _formula(dados)
    mudar(formula["referencia_por_tipo_guia"])
    assert (rco.erros_referencia_guia("x", formula) == []) is aceito, origem
    assert _maezo_aceita(dados) is aceito, origem


# --- divergencias conhecidas (o Maezo recusa o que o rco aceita) -------------------------------------
DIVERGENCIAS_MAIS_ESTRITAS: list[tuple[str, Any]] = [
    ("lacuna so com espacos", {"lacuna": "   "}),
    ("lacuna que nao e texto", {"lacuna": 5}),
    ("valor_por_contrato que nao e texto", {"valor_por_contrato": 7}),
]


@pytest.mark.parametrize(("rotulo", "campos"), DIVERGENCIAS_MAIS_ESTRITAS)
def test_divergencias_conhecidas_sao_so_mais_estritas(
    rco: ModuleType, raiz_rco: Path, rotulo: str, campos: dict[str, Any]
) -> None:
    dados, termo = _com_parametro(campos)
    assert rco.erros_parametro("x", termo, raiz_rco) == [], rotulo
    assert not _maezo_aceita(dados), rotulo


def test_guia_ou_lacuna_que_nao_e_texto_e_so_mais_estrito(rco: ModuleType) -> None:
    for mudar in (_mudar_item(0, guia=5), _mudar_item(0, lacuna=5)):
        dados = _dados()
        formula = _formula(dados)
        mudar(formula["referencia_por_tipo_guia"])
        assert rco.erros_referencia_guia("x", formula) == []
        assert not _maezo_aceita(dados)


def test_arquivo_da_referencia_so_o_rco_confere(rco: ModuleType, raiz_rco: Path) -> None:
    # Unica divergencia no sentido contrario: o arquivo citado vive na arvore do rco, que o pacote nao leva.
    dados, termo = _com_parametro({"referencia": "nao/existe.yaml#x"})
    assert any("arquivo inexistente" in e for e in rco.erros_parametro("x", termo, raiz_rco))
    assert _maezo_aceita(dados)


def test_as_condicoes_copiadas_sao_as_do_rco() -> None:
    fonte = FONTE_RCO.read_text(encoding="utf-8")
    assert 'presentes = [k for k in CAMPOS_PARAMETRO if t.get(k) not in (None, "")]' in fonte
    assert 'arq, _, chave = str(ref).partition("#")' in fonte
    assert "if not arq or not chave:" in fonte
    assert 'if v is not None and (not isinstance(v, str) or "#" not in v):' in fonte
    assert 'g = str(r["guia"] or "").strip()' in fonte
    assert ent.ORDEM_CAMPOS_VALOR_PARAMETRO == ("valor", "referencia", "valor_por_contrato", "lacuna")
    assert 'CAMPOS_PARAMETRO = ("valor", "referencia", "valor_por_contrato", "lacuna")' in fonte


# --- dicionario 1.6.0 (rco b8444f52): `referencia` em termo de cadastro ---------------------------------
def _prazo_1_6_0() -> tuple[dict[str, Any], dict[str, Any]]:
    dados: dict[str, Any] = json.loads(RECORTE_1_6_0.read_text(encoding="utf-8"))
    entrada = next(e for e in dados["entradas"] if e["nome_dmn"] == PRAZO)
    return dados, entrada


def _rco_aceita_formula(rco: ModuleType, entrada: dict[str, Any]) -> bool:
    """`erros_formulas` do publicador sobre a entrada, na forma do dicionario de dados dele (`derivacao`)."""
    campos = {PRAZO: {"origem": entrada["origem"], "derivacao": copy.deepcopy(entrada["formula"])}}
    erros: list[str] = rco.erros_formulas(campos, {"derivadas_sem_formula_legado": []})
    return erros == []


def test_o_1_6_0_publicado_e_aceito_pelos_dois(rco: ModuleType) -> None:
    dados, entrada = _prazo_1_6_0()
    assert _rco_aceita_formula(rco, entrada)
    assert _maezo_aceita(dados)


@pytest.mark.parametrize("referencia", ["sem-chave", "#chave", [], 5])
def test_referencia_malformada_em_termo_de_cadastro_e_so_mais_estrita(
    rco: ModuleType, referencia: Any
) -> None:
    """O publicador nao confere `referencia` fora do termo `parametro` (`erros_formulas`, linhas 259-264): o
    Maezo a confere como `arquivo#chave` e so recusa a mais, o lado seguro."""
    dados, entrada = _prazo_1_6_0()
    termo = next(t for t in entrada["formula"]["termos"] if t["nome"] == "autorizacaoExtemporanea")
    termo["referencia"] = referencia
    assert _rco_aceita_formula(rco, entrada)
    assert not _maezo_aceita(dados)


def test_a_fonte_copiada_vale_para_o_commit_do_dicionario_1_6_0() -> None:
    """A copia (blob `BLOB_RCO`) cobre o commit do recorte 1.6.0: e o mesmo publicador nos dois commits."""
    assert _blob_git(FONTE_RCO.read_bytes()) == BLOB_RCO
    origem = json.loads((RECORTE_1_6_0.parent / "ORIGEM.json").read_text(encoding="utf-8"))
    assert origem["versoes"]["1.6.0"]["commit"] in COMMITS_RCO_COM_ESTE_BLOB
    assert COMMITS_RCO_COM_ESTE_BLOB[0].startswith("aed187d4") and FONTE_RCO.parent.name == "fonte-aed187d4"
