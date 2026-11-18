"""Leitor do dicionario de entradas do K1 (`maezo.platform.rco_entradas`, MZO-2b; contrato-k1.md §1/§5).

Contra o RECORTE REAL `tests/fixtures/rco/k1-1.1.0-recorte/entradas.json` (as entradas dos dominios
`regras-gerais` e `diarias-taxas`, recortadas do `entradas.json` gerado do rco no commit
fe1dc8975c0a486da0699f0fd6a0279e4b70f9af, dicionario 1.1.0; `sha256_conteudo` e o do dicionario
COMPLETO; procedencia em `decisoes.json`) e cada recusa fail-closed.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from maezo.platform import rco_entradas as ent
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError

RECORTE = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "k1-1.1.0-recorte"


def _dados() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((RECORTE / "entradas.json").read_text(encoding="utf-8"))
    return dados


def _ler(dados: dict[str, Any]) -> ent.DicionarioEntradasK1:
    return ent.ler_dicionario_entradas(json.dumps(dados, ensure_ascii=False).encode("utf-8"))


def _indice(dados: dict[str, Any], nome: str) -> int:
    return next(i for i, e in enumerate(dados["entradas"]) if e["nome_dmn"] == nome)


def test_recorte_real_le_e_expoe_o_que_o_montador_usa() -> None:
    dic = ent.ler_dicionario_entradas((RECORTE / "entradas.json").read_bytes())
    assert dic.versao == "1.1.0"
    assert dic.sha256_conteudo.startswith("sha256:")
    data = dic.entradas["dataAtendimento"]
    assert (data.origem, data.tipo, data.variavel_maezo) == ("tiss", "string", "data_atendimento")
    assert data.dominio.tipo == "padrao" and data.dominio.verificavel
    carater = dic.entradas["caraterAtendimento"]
    assert carater.variavel_maezo == "carater_atendimento"
    assert carater.dominio.valores == ("1", "2")
    idade = dic.entradas["idadeBeneficiario"]
    assert idade.variavel_maezo == "idade_anos"  # nome_maezo declarado pelo rco
    assert (idade.dominio.minimo, idade.dominio.maximo) == (0, 130)
    prazo = dic.entradas["diasAlemDoPrazoContratual"]
    assert prazo.origem == "derivado" and prazo.formula is not None
    assert [(t.nome, t.origem) for t in prazo.formula.termos] == [
        ("diasAteApresentacao", "derivado"),
        ("prazoApresentacaoContratualDias", "cadastro"),
    ]
    assert dic.entradas["familiaDiariaTaxa"].dominio.verificavel is False  # lista truncada (73 valores)
    assert dic.entradas["grupoCarencia"].classe_lago == "derivavel"
    assert dic.entradas["autorizacaoExtemporanea"].classe_lago is None  # lacuna do lago


@pytest.mark.parametrize(
    ("texto", "tipo", "verificavel"),
    [
        ("{true, false}", "lista", True),
        ("{a, b, c, d, e, f, … (73 valores)}", "lista", False),
        ("faixa 0..300", "faixa", True),
        ("faixa -36500..36500", "faixa", True),
        ("faixa 0..None", "faixa", True),
        ("faixa 0.5..1.5", "faixa", True),
        ("padrão ^[0-9]{4}$", "padrao", True),
        ("tabela TUSS 22 (procedimentos e eventos em saúde)", "tabela", False),
    ],
)
def test_resumo_de_dominio(texto: str, tipo: str, verificavel: bool) -> None:
    dominio = ent.ler_dominio(texto)
    assert dominio is not None
    assert (dominio.tipo, dominio.verificavel) == (tipo, verificavel)


@pytest.mark.parametrize("texto", ["LACUNA", "faixa 3..1", "{}", "padrão [", "faixa a..b"])
def test_resumo_de_dominio_desconhecido(texto: str) -> None:
    assert ent.ler_dominio(texto) is None


def _mutar(caminho: list[Any], valor: Any) -> Any:
    def aplicar(dados: dict[str, Any]) -> None:
        alvo: Any = dados
        for chave in caminho[:-1]:
            alvo = (
                alvo[_indice(dados, chave)]
                if isinstance(chave, str) and isinstance(alvo, list)
                else alvo[chave]
            )
        if valor is _REMOVER:
            del alvo[caminho[-1]]
        else:
            alvo[caminho[-1]] = valor

    return aplicar


_REMOVER = object()


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mutar(["versao"], "1.3.0"), "versao '1.3.0'"),
        (_mutar(["versao"], "1.1"), "versao"),
        (_mutar(["sha256_conteudo"], "a" * 64), "sha256_conteudo"),
        (_mutar(["novo"], 1), "desconhecidos ['novo']"),
        (_mutar(["entradas", "grupoCarencia", "fallback_nulo"], "negar"), "fallback_nulo"),
        (_mutar(["entradas", "grupoCarencia", "origem"], "lago"), "origem"),
        (_mutar(["entradas", "grupoCarencia", "tipo"], "date"), "tipo"),
        (_mutar(["entradas", "grupoCarencia", "dominio"], "LACUNA"), "dominio"),
        (_mutar(["entradas", "grupoCarencia", "extra"], 1), "desconhecidos ['extra']"),
        (_mutar(["entradas", "grupoCarencia", "tabelas"], _REMOVER), "faltam ['tabelas']"),
        (_mutar(["entradas", "grupoCarencia", "nome_dmn"], "grupo carencia"), "nome_dmn"),
        (
            _mutar(
                ["entradas", "grupoCarencia", "formula"],
                {"formula": "x", "termos": [{"nome": "a", "origem": "tiss", "fonte": "f"}]},
            ),
            "so entrada de origem derivado",
        ),
        (
            _mutar(["entradas", "diasAlemDoPrazoContratual", "formula", "termos", 1, "origem"], "lago"),
            "termos[1].origem",
        ),
        (_mutar(["entradas", "diasAlemDoPrazoContratual", "formula", "termos"], []), "lista nao vazia"),
        (_mutar(["entradas", "grupoCarencia", "derivacao_lago", "classe_lago"], ""), "classe_lago"),
        (_mutar(["entradas"], []), "lista nao vazia"),
    ],
)
def test_forma_invalida_reprova(mutar: Any, trecho: str) -> None:
    dados = copy.deepcopy(_dados())
    mutar(dados)
    with pytest.raises(ManifestoK1InvalidoError) as excinfo:
        _ler(dados)
    assert trecho in str(excinfo.value)


def test_nome_repetido_reprova() -> None:
    dados = _dados()
    dados["entradas"].append(copy.deepcopy(dados["entradas"][0]))
    with pytest.raises(ManifestoK1InvalidoError, match="repetido"):
        _ler(dados)


@pytest.mark.parametrize("raw", [b'{"versao": "1.1.0", "versao": "1.1.0"}', b'{"x": NaN}', b"\xff"])
def test_json_estrito(raw: bytes) -> None:
    with pytest.raises(ManifestoK1InvalidoError, match="JSON ilegivel"):
        ent.ler_dicionario_entradas(raw)


# --- carregar_dicionario_entradas: anexo cruzado com o manifesto -------------------------------------


def _manifesto(k1_dir: Path, raw: bytes, **mudancas: Any) -> Any:
    from maezo.platform import rco_manifesto as k1
    from tests.unit.tools.workers import rco_recorte

    dic = ent.ler_dicionario_entradas(raw)
    (k1_dir / "entradas.json").write_bytes(raw)
    decisoes = {
        did: dataclasses.replace(
            d,
            entradas_declaradas=tuple(
                (n, dic.entradas[n].origem, dic.entradas[n].nome_maezo) for n in d.entradas
            ),
        )
        for did, d in rco_recorte.decisoes().items()
    }
    campos: dict[str, Any] = {
        "formato_manifesto": "1.0.0",
        "versao_pacote": "3.0.0",
        "repo": "Omni-Saude/regras-contratos-operadora",
        "commit_sha": "fe1dc8975c0a486da0699f0fd6a0279e4b70f9af",
        "decisoes": MappingProxyType(decisoes),
        "anexos": MappingProxyType(
            {"entradas": k1.AnexoK1("entradas", "entradas.json", k1.sha256_prefixado(raw))}
        ),
        "exemplos": (),
        "dicionario_entradas_versao": dic.versao,
        "dicionario_entradas_sha256": dic.sha256_conteudo,
    }
    campos.update(mudancas)
    return k1.ManifestoK1(**campos)


def test_carregar_cruza_versao_hash_e_entradas_das_decisoes(tmp_path: Path) -> None:
    raw = (RECORTE / "entradas.json").read_bytes()
    dic = ent.carregar_dicionario_entradas(_manifesto(tmp_path, raw), k1_dir=tmp_path)
    assert dic.versao == "1.1.0"


@pytest.mark.parametrize(
    ("mudancas", "trecho"),
    [
        ({"dicionario_entradas_versao": "1.0.0"}, "dicionario_entradas.versao"),
        ({"dicionario_entradas_sha256": "sha256:" + "0" * 64}, "sha256_conteudo"),
    ],
)
def test_carregar_recusa_versao_ou_hash_divergente(
    tmp_path: Path, mudancas: dict[str, Any], trecho: str
) -> None:
    raw = (RECORTE / "entradas.json").read_bytes()
    with pytest.raises(ManifestoK1InvalidoError, match=trecho):
        ent.carregar_dicionario_entradas(_manifesto(tmp_path, raw, **mudancas), k1_dir=tmp_path)


def test_carregar_recusa_origem_ou_nome_maezo_divergente_na_decisao(tmp_path: Path) -> None:
    raw = (RECORTE / "entradas.json").read_bytes()
    manifesto = _manifesto(tmp_path, raw)
    carencia = manifesto.decisoes["rco_geral_carencia"]
    errada = dataclasses.replace(
        carencia, entradas_declaradas=(("caraterAtendimento", "cadastro", "carater_atendimento"),)
    )
    alterado = dataclasses.replace(
        manifesto, decisoes=MappingProxyType({**manifesto.decisoes, carencia.id: errada})
    )
    with pytest.raises(ManifestoK1InvalidoError, match="origem/nome_maezo"):
        ent.carregar_dicionario_entradas(alterado, k1_dir=tmp_path)


def test_carregar_recusa_anexo_adulterado(tmp_path: Path) -> None:
    raw = (RECORTE / "entradas.json").read_bytes()
    manifesto = _manifesto(tmp_path, raw)
    (tmp_path / "entradas.json").write_bytes(raw + b" ")
    with pytest.raises(ManifestoK1InvalidoError, match="sha256 diverge"):
        ent.carregar_dicionario_entradas(manifesto, k1_dir=tmp_path)


# --- dicionario 1.5.0 (rco aed187d4): valor do parametro e referencia por tipo de guia ---------------

RECORTE_2_3 = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "k1-2.3.0-recorte"
URGENCIA = "urgenciaAposVinteQuatroHorasCobertura"


def _dados_15() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((RECORTE_2_3 / "entradas-recorte.json").read_text(encoding="utf-8"))
    assert dados["versao"] == "1.5.0"
    return dados


def _formula(dados: dict[str, Any], nome: str) -> dict[str, Any]:
    formula: dict[str, Any] = dados["entradas"][_indice(dados, nome)]["formula"]
    return formula


def _termo(dados: dict[str, Any], nome: str, termo: str) -> dict[str, Any]:
    return next(t for t in _formula(dados, nome)["termos"] if t["nome"] == termo)


def test_dicionario_15_le_o_valor_de_cada_parametro_e_a_referencia_da_guia() -> None:
    dic = _ler(_dados_15())
    termos = {t.nome: t for e in dic.entradas.values() if e.formula is not None for t in e.formula.termos}
    assert (termos["horasMinimasUrgencia"].valor, termos["horasMinimasUrgencia"].unidade) == (24, "horas")
    assert (termos["janelaDias"].valor, termos["janelaDias"].unidade) == (30, "dias")
    assert termos["procedimentoCirurgico"].referencia == ("fontes/derivados/tuss-22-202609.csv#codigo",)
    assert len(termos["tabelaClassificacao"].referencia) == 2
    assert termos["diariaLeitoAltaTecnologia"].lacuna == "L-prog-rco-04"
    assert all(t.valor is None and not t.referencia for t in termos.values() if t.origem != "parametro")
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None and len(formula.referencia_por_tipo_guia) == 6
    sem_data = [r for r in formula.referencia_por_tipo_guia if r.data is None]
    assert len(sem_data) == 1 and sem_data[0].lacuna


def test_dicionario_14_recusa_os_campos_da_15() -> None:
    dados = _dados_15()
    dados["versao"] = "1.4.0"
    with pytest.raises(ManifestoK1InvalidoError, match="desconhecidos"):
        _ler(dados)


def _mudar_termo(termo: str, **campos: Any) -> Any:
    def aplicar(dados: dict[str, Any]) -> None:
        nome = "reinternacaoMesmaCausa" if termo == "janelaDias" else URGENCIA
        alvo = _termo(dados, nome, termo)
        for chave, valor in campos.items():
            if valor is _FORA:
                alvo.pop(chave, None)
            else:
                alvo[chave] = valor

    return aplicar


def _mudar_referencia(indice: int, **campos: Any) -> Any:
    def aplicar(dados: dict[str, Any]) -> None:
        alvo = _formula(dados, URGENCIA)["referencia_por_tipo_guia"][indice]
        for chave, valor in campos.items():
            if valor is _FORA:
                alvo.pop(chave, None)
            else:
                alvo[chave] = valor

    return aplicar


_FORA = object()


@pytest.mark.parametrize(
    ("mutar", "trecho"),
    [
        (_mudar_termo("horasMinimasUrgencia", valor=_FORA, unidade=_FORA), "exatamente um de"),
        (_mudar_termo("horasMinimasUrgencia", lacuna="L-x"), "exatamente um de"),
        (_mudar_termo("horasMinimasUrgencia", unidade=_FORA), "com `unidade` em"),
        (_mudar_termo("janelaDias", valor=_FORA, lacuna="L-x"), "`unidade` so"),
        (_mudar_termo("horasMinimasUrgencia", valor=True), "tem de ser numero"),
        (_mudar_termo("horasMinimasUrgencia", valor="24"), "tem de ser numero"),
        (_mudar_termo("horasMinimasUrgencia", unidade="semanas"), "tem de ser numero"),
        (
            _mudar_termo("horasMinimasUrgencia", valor=_FORA, unidade=_FORA, referencia="sem-chave"),
            "referencia",
        ),
        # chave com "" e AUSENTE, como no publicador (DL-0184): nenhum campo de valor presente
        (
            _mudar_termo("horasMinimasUrgencia", valor=_FORA, unidade=_FORA, valor_por_contrato=""),
            "exatamente um de",
        ),
        (_mudar_termo("horasMinimasUrgencia", valor=None, unidade=None), "exatamente um de"),
        (_mudar_termo("horasMinimasUrgencia", valor=_FORA, unidade=None, lacuna="  "), "texto"),
        (_mudar_termo("horasMinimasUrgencia", valor=_FORA, unidade="", lacuna="L-x"), "`unidade` so"),
        (_mudar_termo("horasMinimasUrgencia", extra=1), "desconhecidos"),
        (_mudar_termo("dataInicioCobertura", valor=1, unidade="dias"), "desconhecidos"),
        (_mudar_referencia(0, extra=1), "desconhecidos"),
        (_mudar_referencia(0, hora=_FORA), "faltam ['hora']"),
        (_mudar_referencia(0, data="dataSolicitacao"), "elemento do XSD"),
        (_mudar_referencia(5, lacuna=_FORA), "data nula exige"),
        (_mudar_referencia(1, guia="solicitação de SP/SADT"), "repetida"),
        (_mudar_referencia(1, guia=""), "texto nao vazio"),
    ],
)
def test_forma_invalida_da_15_reprova(mutar: Any, trecho: str) -> None:
    dados = _dados_15()
    mutar(dados)
    with pytest.raises(ManifestoK1InvalidoError) as erro:
        _ler(dados)
    assert any(trecho in p for p in erro.value.problemas), erro.value.problemas


def test_chave_com_none_ou_vazio_e_ausente_como_no_publicador() -> None:
    # `scripts/k1_entradas.py::erros_parametro` (rco aed187d4, linha 135) conta so
    # `t.get(k) not in (None, "")`;
    # `unidade` fora do `valor` so pode ser `None` (linha 145); lacuna `None` com data e ausente (linha 183).
    dados = _dados_15()
    alvo = _termo(dados, URGENCIA, "horasMinimasUrgencia")
    alvo.update(lacuna=None, referencia="", valor_por_contrato=None)
    lacuna = _termo(dados, "procedimentoAlcancadoCpt", "diariaLeitoAltaTecnologia")
    lacuna.update(valor=None, unidade=None)
    _formula(dados, URGENCIA)["referencia_por_tipo_guia"][0]["lacuna"] = None
    dic = _ler(dados)
    termos = {t.nome: t for e in dic.entradas.values() if e.formula is not None for t in e.formula.termos}
    assert (termos["horasMinimasUrgencia"].valor, termos["horasMinimasUrgencia"].unidade) == (24, "horas")
    assert termos["diariaLeitoAltaTecnologia"].lacuna == "L-prog-rco-04"
    formula = dic.entradas[URGENCIA].formula
    assert formula is not None and formula.referencia_por_tipo_guia[0].lacuna is None


def test_referencia_vazia_e_aceita_como_no_publicador_e_fica_sem_valor() -> None:
    # O rco aceita `referencia: []` (o laco de `erros_parametro` nao confere item nenhum): o termo fica sem
    # valor, e o montador le o parametro nulo (nulo nunca nega).
    dados = _dados_15()
    alvo = _termo(dados, URGENCIA, "horasMinimasUrgencia")
    alvo.pop("valor")
    alvo.pop("unidade")
    alvo["referencia"] = []
    formula = _ler(dados).entradas[URGENCIA].formula
    assert formula is not None
    termo = next(t for t in formula.termos if t.origem == "parametro")
    assert (termo.valor, termo.referencia, termo.lacuna, termo.valor_por_contrato) == (None, (), None, None)


def test_referencia_por_tipo_guia_vazia_reprova() -> None:
    dados = _dados_15()
    _formula(dados, URGENCIA)["referencia_por_tipo_guia"] = []
    with pytest.raises(ManifestoK1InvalidoError, match="lista nao vazia"):
        _ler(dados)
