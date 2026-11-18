"""Montador de entradas das decisoes K1 do rco (`maezo.tools.workers.rco_montador`, MZO-2b).

Contra o recorte REAL do K1 (`rco_recorte.py`): o dicionario de entradas 1.1.0 e as decisoes dos
dominios `regras-gerais` e `diarias-taxas`. Prova as regras duras da coordenacao:

- dado ausente, fora do tipo ou fora do dominio entra NULO, nunca inventado nem convertido por palpite;
- derivada sem formula estruturada entra nula; a unica com formula (`diasAlemDoPrazoContratual`) segue
  a formula publicada e o montador recusa se ela mudar;
- carencia com grupo e saldo (K3 v1.16) e nula enquanto o pin AMH for anterior a 1.16, e a tabela de
  carencia, com essas entradas nulas, NAO tem linha `negar` que possa casar (leitura da tabela; o
  motor real esta em `tests/integration/dmn/test_rco_carencia_nulos_engine.py`);
- cadastro e fora da TISS nunca vem das variaveis de processo (sementes do start).
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from maezo.platform.rco_entradas import DicionarioEntradasK1, FormulaK1, TermoFormulaK1
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte
from tests.unit.tools.workers import rco_recorte

PIN_SEM_K3 = (1, 15)
PIN_COM_K3 = (1, 16)
DATA = "2026-12-01"


@pytest.fixture(scope="module")
def dic() -> DicionarioEntradasK1:
    return rco_recorte.dicionario()


@pytest.fixture(scope="module")
def decisoes() -> dict[str, Any]:
    return rco_recorte.decisoes()


def _guia(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "data_atendimento": DATA,
        "carater_atendimento": "eletivo",
        "codigo_procedimento_tuss": "31309127",
    }
    base.update(extra)
    return base


def _k3(**extra: Any) -> mt.FatosK3V116:
    base: dict[str, Any] = {
        "data_atendimento": DATA,
        "grupo": "demais",
        "dias": 180,
        "saldo_dias": 40,
        "situacao_cadastral": "ativo",
        "tem_cpt_vigente": False,
    }
    base.update(extra)
    return mt.FatosK3V116(**base)


# --- cobertura do mapeamento ----------------------------------------------------------------------


def test_mapeamento_cobre_exatamente_as_entradas_dos_dominios(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    # O mapeamento cobre o pacote MAIS NOVO suportado (K1 2.3.0, dicionario 1.5.0; DL-0183): nem entrada
    # sem fonte, nem mapeamento morto. As decisoes do recorte 1.1.0 continuam cobertas (sem entrada a menos).
    novas = rco_k1_2_3_recorte.manifesto().decisoes.values()
    for dominio, mapa in mt.MAPEAMENTOS.items():
        do_dominio = [d for d in decisoes.values() if d.dominio == dominio]
        assert do_dominio, dominio
        declaradas = {nome for d in do_dominio for nome in d.entradas}
        assert declaradas <= set(mapa), dominio
        assert set(mapa) == {nome for d in novas if d.dominio == dominio for nome in d.entradas}, dominio
        for d in do_dominio:
            assert mt.conferir_mapeamento(dic, dominio, d.entradas) == [], d.id


def test_mapeamento_segue_a_origem_e_a_formula_do_dicionario(dic: DicionarioEntradasK1) -> None:
    dic_14 = rco_k1_2_3_recorte.dicionario()
    for mapa in mt.MAPEAMENTOS.values():
        for nome, m in mapa.items():
            if nome not in dic.entradas:  # entrada do dicionario 1.3.0/1.4.0 (DL-0183)
                assert m.origem == dic_14.entradas[nome].origem, nome
                continue
            entrada = dic.entradas[nome]
            assert m.origem == entrada.origem, nome
            if entrada.origem == "derivado":
                # 1.1.0: so `diasAlemDoPrazoContratual` tem formula; as que a 1.2.0 publica (implementadas
                # ou pendentes) sao nulas por desenho neste dicionario anterior a elas.
                efetiva = mt._fonte_efetiva(m, entrada)
                esperado = mt.Fonte.FORMULA if entrada.formula is not None else mt.Fonte.NULO_POR_DESENHO
                assert efetiva is esperado, nome
                assert m.fonte in (mt.Fonte.FORMULA, mt.Fonte.FORMULA_PENDENTE, mt.Fonte.NULO_POR_DESENHO)
                if m.fonte is mt.Fonte.FORMULA:
                    assert nome in mt.FORMULAS, nome
                if m.fonte is mt.Fonte.FORMULA_PENDENTE:
                    assert nome in mt.FORMULAS_PENDENTES and nome not in mt.FORMULAS, nome
            if m.fonte in (mt.Fonte.GUIA, mt.Fonte.CADASTRO, mt.Fonte.FORA_TISS):
                # a chave na fonte e o nome Maezo do PROPRIO dicionario (`nome_maezo`, senao `nome_snake`)
                assert m.campo is None, nome
            if m.fonte is mt.Fonte.K3_V116:
                assert m.campo in {f.name for f in dataclasses.fields(mt.FatosK3V116)}, nome
            if m.fonte is mt.Fonte.GUIA_EXPLICITA:
                assert (nome, m.campo) == ("caraterAtendimento", "carater_atendimento_guia")


def test_k3_so_para_as_entradas_que_o_lago_deriva_no_v116(dic: DicionarioEntradasK1) -> None:
    k3 = {n for mapa in mt.MAPEAMENTOS.values() for n, m in mapa.items() if m.fonte is mt.Fonte.K3_V116}
    assert k3 == {
        "grupoCarencia",
        "carenciaContratualDias",
        "saldoCarenciaDias",
        "situacaoCadastral",
        "cptVigente",
    }
    for nome in k3:
        assert dic.entradas[nome].origem == "cadastro"
        assert dic.entradas[nome].classe_lago in {"ja_existe", "derivavel", "campo_novo"}


# --- carencia: K3 atras do pin ---------------------------------------------------------------------

_K3_CARENCIA = ("grupoCarencia", "carenciaContratualDias", "saldoCarenciaDias")


def test_carencia_nula_enquanto_o_pin_amh_e_anterior_a_v116(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    fatos = mt.FatosDoCaso(
        guia=_guia(), versao_k3_pinada=PIN_SEM_K3, k3=_k3(), carater_atendimento_guia="1"
    )  # fatos K3 presentes
    montadas = mt.montar_entradas(decisoes["rco_geral_carencia"], dic, fatos)
    assert dict(montadas.variaveis) == {
        "dataAtendimento": DATA,
        "caraterAtendimento": "1",
        "grupoCarencia": None,
        "carenciaContratualDias": None,
        "saldoCarenciaDias": None,
    }
    assert {n: montadas.nulos[n] for n in _K3_CARENCIA} == dict.fromkeys(_K3_CARENCIA, "k3_v1_16_nao_pinado")
    assert montadas.fontes["grupoCarencia"] == "k3_v1_16"
    assert montadas.versao_dicionario == "1.1.0"


def test_carencia_k3_nula_com_pin_v116_enquanto_a_lacuna_do_saldo_esta_aberta(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    assert mt.lacuna_saldo_fechada() is False  # configuracao de bordo: lacuna aberta
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_COM_K3, k3=_k3())
    montadas = mt.montar_entradas(decisoes["rco_geral_carencia"], dic, fatos)
    assert {n: montadas.nulos[n] for n in _K3_CARENCIA} == dict.fromkeys(
        _K3_CARENCIA, "k3_lacuna_saldo_isencoes_aberta"
    )
    eleg = mt.montar_entradas(decisoes["rco_geral_elegibilidade"], dic, fatos)
    assert eleg.variaveis["situacaoCadastral"] == "ativo"  # a lacuna e so da carencia


def _lacunas(tmp_path: Path, lacuna: object) -> Path:
    caminho = tmp_path / "k3-lacunas.json"
    caminho.write_text(json.dumps({mt.LACUNA_SALDO: lacuna}), encoding="utf-8")
    return caminho


_FECHADA = {"fechada": True, "fonte": "x", "decidido_por": "negocio (fulano)", "decidido_em": "2026-12-01"}


def _fechar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    caminho = _lacunas(tmp_path, _FECHADA)
    monkeypatch.setattr(mt, "lacuna_saldo_fechada", lambda: mt.ler_lacuna_saldo_fechada(caminho))


def test_configuracao_de_bordo_da_lacuna_esta_aberta_e_com_fonte() -> None:
    raiz = Path(__file__).resolve().parents[4]
    dados = json.loads((raiz / mt.K3_LACUNAS_RELATIVO).read_text(encoding="utf-8"))[mt.LACUNA_SALDO]
    assert dados["fechada"] is False
    assert (dados["decidido_por"], dados["decidido_em"]) == (None, None)
    assert "k1-entradas-origem.yaml:237-240" in dados["fonte"]


@pytest.mark.parametrize(
    ("conteudo", "fechada"),
    [
        (_FECHADA, True),
        ({**_FECHADA, "decidido_por": None}, False),  # fechada sem procedencia: recusada (aberta)
        ({**_FECHADA, "decidido_em": " "}, False),
        ({**_FECHADA, "fechada": "true"}, False),
        ({"fechada": True}, False),
        ("fechada", False),
    ],
)
def test_lacuna_so_fecha_com_procedencia(tmp_path: Path, conteudo: object, fechada: bool) -> None:
    assert mt.ler_lacuna_saldo_fechada(_lacunas(tmp_path, conteudo)) is fechada


def test_lacuna_ausente_ou_malformada_conta_como_aberta(tmp_path: Path) -> None:
    assert mt.ler_lacuna_saldo_fechada(tmp_path / "nao-existe.json") is False
    ruim = tmp_path / "ruim.json"
    ruim.write_text('{"saldo_isencoes_portabilidade": {"fechada": true,', encoding="utf-8")
    assert mt.ler_lacuna_saldo_fechada(ruim) is False


def test_lacuna_fechada_sem_procedencia_mantem_k3_nulo(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    caminho = _lacunas(tmp_path, {**_FECHADA, "decidido_por": None, "decidido_em": None})
    monkeypatch.setattr(mt, "lacuna_saldo_fechada", lambda: mt.ler_lacuna_saldo_fechada(caminho))
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_COM_K3, k3=_k3())
    montadas = mt.montar_entradas(decisoes["rco_geral_carencia"], dic, fatos)
    assert all(montadas.nulos[n] == "k3_lacuna_saldo_isencoes_aberta" for n in _K3_CARENCIA)


def test_carencia_com_k3_pinado_e_lacuna_fechada_le_o_item_do_procedimento(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fechar(monkeypatch, tmp_path)
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_COM_K3, k3=_k3(), carater_atendimento_guia="1")
    montadas = mt.montar_entradas(decisoes["rco_geral_carencia"], dic, fatos)
    assert [montadas.variaveis[n] for n in _K3_CARENCIA] == ["demais", 180, 40]
    assert montadas.nulos == {}


@pytest.mark.parametrize(
    ("k3", "motivo"),
    [
        (_k3(grupo="nao_classificado"), "fora_do_dominio"),  # sem curadoria: nunca vira grupo
        (_k3(data_atendimento="2026-11-16"), "k3_fora_da_data_do_atendimento"),  # avaliado "hoje"
        (_k3(data_atendimento=None), "k3_fora_da_data_do_atendimento"),
        (_k3(grupo=None), "ausente_na_fonte"),
    ],
)
def test_grupo_k3_fora_do_contrato_entra_nulo(
    dic: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    k3: mt.FatosK3V116,
    motivo: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fechar(monkeypatch, tmp_path)
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_COM_K3, k3=k3)
    montadas = mt.montar_entradas(decisoes["rco_geral_carencia"], dic, fatos)
    assert montadas.variaveis["grupoCarencia"] is None
    assert montadas.nulos["grupoCarencia"] == motivo


def test_k3_sem_data_do_atendimento_na_guia_entra_nulo(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    guia = _guia()
    del guia["data_atendimento"]
    guia["data_solicitacao_iso"] = DATA  # instante do Maezo: nunca vira a data do atendimento
    fatos = mt.FatosDoCaso(guia=guia, versao_k3_pinada=PIN_COM_K3, k3=_k3())
    montadas = mt.montar_entradas(decisoes["rco_geral_carencia"], dic, fatos)
    assert montadas.variaveis["dataAtendimento"] is None
    assert all(montadas.variaveis[n] is None for n in _K3_CARENCIA)
    assert montadas.nulos["saldoCarenciaDias"] == "k3_fora_da_data_do_atendimento"


def _celulas_negar(dmn: Path) -> tuple[list[str], list[list[str]]]:
    ns = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
    tabela = ET.parse(dmn).getroot().find(".//d:decisionTable", ns)
    assert tabela is not None
    entradas = [
        (i.findtext("d:inputExpression/d:text", namespaces=ns) or "").strip()
        for i in tabela.findall("d:input", ns)
    ]
    saidas = [o.get("name") for o in tabela.findall("d:output", ns)]
    negar: list[list[str]] = []
    for regra in tabela.findall("d:rule", ns):
        celulas = [
            (c.findtext("d:text", namespaces=ns) or "").strip() for c in regra.findall("d:inputEntry", ns)
        ]
        saida = dict(
            zip(
                saidas,
                [
                    (c.findtext("d:text", namespaces=ns) or "").strip()
                    for c in regra.findall("d:outputEntry", ns)
                ],
                strict=True,
            )
        )
        if saida["decisao"] == '"negar"':
            negar.append(celulas)
    return entradas, negar


def test_carencia_com_k3_nulo_nao_tem_linha_negar_que_case(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    """A tabela PUBLICADA (`rco_geral_carencia`): toda linha `negar` testa grupo, prazo e saldo com um
    literal, faixa fechada ou comparacao (lista PERMITIDA `_nunca_casa_nulo`). Com o K3 nulo pelo pin,
    nenhuma linha `negar` pode casar: o motor cai em `revisao_humana` (provado no teste de integracao)."""
    entradas, negar = _celulas_negar(rco_recorte.CARENCIA_DMN)
    assert entradas == list(decisoes["rco_geral_carencia"].entradas)
    assert negar, "a tabela nega com dado presente; o teste precisa de linhas negar para valer"
    montadas = mt.montar_entradas(
        decisoes["rco_geral_carencia"],
        dic,
        # pior caso para `negar`: carater eletivo ("1") presente, K3 trazido mas segurado pelo pin
        mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3, k3=_k3(), carater_atendimento_guia="1"),
    )
    nulas = [i for i, nome in enumerate(entradas) if montadas.variaveis[nome] is None]
    assert {entradas[i] for i in nulas} == set(_K3_CARENCIA)
    for celulas in negar:
        for i in nulas:
            assert _nunca_casa_nulo(celulas[i]), (entradas[i], celulas[i])


# Lista PERMITIDA de testes unarios que nunca casam nulo em FEEL: literal entre aspas, numero,
# comparacao com literal ou numero, faixa fechada `[a..b]`, ou lista separada por virgula desses.
# Qualquer outra coisa reprova — inclusive "" (curinga do Camunda), "-", `null`, `not(...)`, `?`.
_NUM = r"-?[0-9]+(?:\.[0-9]+)?"
_LIT = r'"[^"\\]*"'
_ITEM_SEGURO = re.compile(
    rf"^(?:{_LIT}|{_NUM}|(?:<|<=|>|>=)\s*(?:{_LIT}|{_NUM})|\[\s*{_NUM}\s*\.\.\s*{_NUM}\s*\])$"
)


def _nunca_casa_nulo(celula: str) -> bool:
    itens = [i.strip() for i in celula.split(",")]
    return bool(celula.strip()) and all(_ITEM_SEGURO.fullmatch(i) and i != "null" for i in itens)


@pytest.mark.parametrize(
    ("celula", "seguro"),
    [
        ('"parto_a_termo"', True),
        ("[0..300]", True),
        ("> 0", True),
        ('>= "2026-11-03"', True),
        ("1, 2", True),
        ("", False),
        ("-", False),
        ("null", False),
        ("< 0, > 300, null", False),
        ('not("1")', False),
        ('not ("1")', False),
        ("? > 0", False),
        ("x is defined", False),
        ("]0..300]", False),
    ],
)
def test_lista_permitida_de_celulas(celula: str, seguro: bool) -> None:
    assert _nunca_casa_nulo(celula) is seguro


# --- guia: conversao declarada, nunca palpite -------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado", "motivo"),
    [
        ("eletivo", "1", None),
        ("urgencia", "2", None),
        ("emergencia", "2", None),
        ("1", "1", None),
        ("2", "2", None),
        (" urgencia", None, "fora_do_dominio"),  # casamento exato, como o adaptador do rco
        ("Urgencia", None, "fora_do_dominio"),
        ("programada", None, "fora_do_dominio"),
        (2, None, "tipo_invalido"),
        (None, None, "ausente_na_fonte"),
        ("", None, "ausente_na_fonte"),
    ],
)
def test_carater_pela_tabela_23_declarada_pelo_rco(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], valor: object, esperado: object, motivo: str | None
) -> None:
    fatos = mt.FatosDoCaso(
        guia=_guia(),
        versao_k3_pinada=PIN_SEM_K3,
        carater_atendimento_guia=valor,  # type: ignore[arg-type]
    )
    montadas = mt.montar_entradas(decisoes["rco_geral_elegibilidade"], dic, fatos)
    assert montadas.variaveis["caraterAtendimento"] == esperado
    assert montadas.nulos.get("caraterAtendimento") == motivo
    assert montadas.fontes["caraterAtendimento"] == "guia_explicita"


@pytest.mark.parametrize("semente", ["eletivo", "1", "urgencia"])
def test_carater_da_variavel_de_start_e_ignorado(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], semente: str
) -> None:
    """O start de hoje preenche `carater_atendimento="eletivo"` quando falta (agents/rafael). Sem o
    carater da guia, a entrada e NULA — nunca `1`, que e o que deixa casar as linhas `negar`."""
    fatos = mt.FatosDoCaso(guia=_guia(carater_atendimento=semente), versao_k3_pinada=PIN_SEM_K3)
    for decisao in ("rco_geral_carencia", "rco_geral_elegibilidade"):
        montadas = mt.montar_entradas(decisoes[decisao], dic, fatos)
        assert montadas.variaveis["caraterAtendimento"] is None
        assert montadas.nulos["caraterAtendimento"] == "ausente_na_fonte"


def test_tabela_23_vem_do_arquivo_versionado_com_a_fonte_do_rco() -> None:
    raiz = Path(__file__).resolve().parents[4]
    dados = json.loads((raiz / mt.CARATER_TUSS23_RELATIVO).read_text(encoding="utf-8"))
    assert dados["fonte"] == {
        "repo": "Omni-Saude/regras-contratos-operadora",
        "commit_sha": "fe1dc8975c0a486da0699f0fd6a0279e4b70f9af",
        "arquivo": "docs/dicionario-dados.yaml",
        "linhas": "303-314",
        "campo": "caraterAtendimento.valores_maezo",
    }
    assert dados["valores_maezo"] == {"eletivo": "1", "urgencia": "2", "emergencia": "2"}
    assert dados["aceitos_sem_conversao"] == ["1", "2"]
    assert dict(mt.carater_tuss23()) == {
        "eletivo": "1",
        "urgencia": "2",
        "emergencia": "2",
        "1": "1",
        "2": "2",
    }


def test_decisao_sem_entradas_recusa(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    vazia = dataclasses.replace(decisoes["rco_geral_carencia"], entradas=())
    with pytest.raises(mt.MontadorEntradasError, match="sem entradas"):
        mt.montar_entradas(vazia, dic, mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3))


@pytest.mark.parametrize("data", ["2026-13-01", "01/12/2026", "2026-12-01T10:00:00-03:00", 20261201])
def test_data_fora_do_padrao_do_dicionario_entra_nula(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], data: object
) -> None:
    fatos = mt.FatosDoCaso(guia=_guia(data_atendimento=data), versao_k3_pinada=PIN_SEM_K3)
    montadas = mt.montar_entradas(decisoes["rco_geral_elegibilidade"], dic, fatos)
    assert montadas.variaveis["dataAtendimento"] is None


# --- cadastro e fora da TISS: nunca das variaveis de processo ---------------------------------------


def test_semente_do_start_nunca_vira_cadastro_nem_k3(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    sementes = _guia(
        situacao_cadastral="ativo",
        idade_anos=40,
        deficiencia_cadastro=False,
        criterio_revisao_atendido=True,
        familia_diaria_taxa="tema_diaria_global",
    )
    fatos = mt.FatosDoCaso(guia=sementes, versao_k3_pinada=PIN_COM_K3)
    eleg = mt.montar_entradas(decisoes["rco_geral_elegibilidade"], dic, fatos)
    assert eleg.variaveis["situacaoCadastral"] is None
    cobertura = mt.montar_entradas(decisoes["rco_diaria_cobertura"], dic, fatos)
    assert {
        n: cobertura.variaveis[n]
        for n in ("idadeBeneficiario", "deficienciaCadastro", "criterioRevisaoAtendido", "familiaDiariaTaxa")
    } == {
        "idadeBeneficiario": None,
        "deficienciaCadastro": None,
        "criterioRevisaoAtendido": None,
        "familiaDiariaTaxa": None,
    }
    assert cobertura.nulos["familiaDiariaTaxa"] == "derivada_sem_formula"


@pytest.mark.parametrize(
    ("idade", "esperado", "motivo"),
    [
        (40, 40, None),
        (0, 0, None),
        (131, None, "fora_do_dominio"),
        (-1, None, "fora_do_dominio"),
        (True, None, "tipo_invalido"),
        (40.0, None, "tipo_invalido"),
    ],
)
def test_cadastro_entregue_pelo_worker_valida_tipo_e_faixa(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], idade: object, esperado: object, motivo: str | None
) -> None:
    fatos = mt.FatosDoCaso(
        guia=_guia(),
        versao_k3_pinada=PIN_SEM_K3,
        cadastro={"idade_anos": idade, "deficiencia_cadastro": False},
        fora_tiss={"criterio_revisao_atendido": True},
    )
    montadas = mt.montar_entradas(decisoes["rco_diaria_cobertura"], dic, fatos)
    assert montadas.variaveis["idadeBeneficiario"] == esperado
    assert montadas.nulos.get("idadeBeneficiario") == motivo
    assert montadas.variaveis["deficienciaCadastro"] is False
    assert montadas.variaveis["criterioRevisaoAtendido"] is True
    assert montadas.fontes["criterioRevisaoAtendido"] == "fora_tiss"


def test_toda_decisao_dos_dominios_monta_toda_entrada(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3)
    for decisao in decisoes.values():
        montadas = mt.montar_entradas(decisao, dic, fatos)
        assert tuple(montadas.variaveis) == decisao.entradas
        assert set(montadas.nulos) == {n for n, v in montadas.variaveis.items() if v is None}


# --- formula publicada: diasAlemDoPrazoContratual ---------------------------------------------------


@pytest.mark.parametrize(
    ("dias", "prazo", "esperado", "motivo"),
    [
        (40, 30, 10, None),
        (20, 30, -10, None),
        (40, 0, 40, None),
        (40, -1, None, mt.MotivoNulo.PRAZO_CONTRATUAL_NEGATIVO),
        (None, 30, None, mt.MotivoNulo.TERMO_AUSENTE),
        (40, None, None, mt.MotivoNulo.TERMO_AUSENTE),
        (True, 30, None, mt.MotivoNulo.TERMO_AUSENTE),
        (40.0, 30, None, mt.MotivoNulo.TERMO_AUSENTE),
    ],
)
def test_formula_dias_alem_do_prazo_contratual(
    dias: object, prazo: object, esperado: object, motivo: object
) -> None:
    calcular = mt.FORMULAS["diasAlemDoPrazoContratual"].calcular
    assert calcular({"diasAteApresentacao": dias, "prazoApresentacaoContratualDias": prazo}) == (
        esperado,
        motivo,
    )


def test_dias_alem_do_prazo_nulo_enquanto_dias_ate_apresentacao_nao_tem_formula(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    """O prazo do contrato pode chegar do cadastro, mas `diasAteApresentacao` (o outro termo) e derivada
    SEM formula estruturada no dicionario 1.1.0: entra nula, e a formula manda nulo. Uma semente do
    termo na guia nao muda isso."""
    fatos = mt.FatosDoCaso(
        guia=_guia(dias_ate_apresentacao=45),
        versao_k3_pinada=PIN_SEM_K3,
        cadastro={"prazo_apresentacao_contratual_dias": 30},
    )
    montadas = mt.montar_entradas(decisoes["rco_geral_prazos_conta"], dic, fatos)
    assert montadas.variaveis["diasAteApresentacao"] is None
    assert montadas.nulos["diasAteApresentacao"] == "derivada_sem_formula"
    assert montadas.variaveis["diasAlemDoPrazoContratual"] is None
    assert montadas.nulos["diasAlemDoPrazoContratual"] == "termo_da_formula_ausente"
    assert montadas.fontes["diasAlemDoPrazoContratual"] == "formula"


def _com_entrada(dic: DicionarioEntradasK1, nome: str, **mudancas: Any) -> DicionarioEntradasK1:
    entradas = dict(dic.entradas)
    entradas[nome] = dataclasses.replace(entradas[nome], **mudancas)
    return dataclasses.replace(dic, entradas=entradas)


def test_formula_publicada_diferente_da_implementada_recusa(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    atual = dic.entradas["diasAlemDoPrazoContratual"].formula
    assert atual is not None
    outra = _com_entrada(
        dic,
        "diasAlemDoPrazoContratual",
        formula=dataclasses.replace(atual, formula="diasAteApresentacao - 30"),
    )
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3)
    with pytest.raises(mt.MontadorEntradasError, match="diverge da implementada"):
        mt.montar_entradas(decisoes["rco_geral_prazos_conta"], outra, fatos)


def test_formula_publicada_para_entrada_que_o_montador_manda_nula_recusa(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Derivada mapeada como nula por desenho (sem formula) e o dicionario publica uma: recusa. Hoje
    nenhuma derivada dos dois dominios esta assim (as 11 tem formula na 1.2.0); o mapa e do teste."""
    mapas = dict(mt.MAPEAMENTOS)
    mapas["diarias-taxas"] = MappingProxyType(
        {**mt.MAPEAMENTOS["diarias-taxas"], "familiaDiariaTaxa": mt._SEM_FORMULA}
    )
    monkeypatch.setattr(mt, "MAPEAMENTOS", MappingProxyType(mapas))
    nova = FormulaK1("x", (TermoFormulaK1("dataAtendimento", "tiss", "guia"),))
    outra = _com_entrada(dic, "familiaDiariaTaxa", formula=nova)
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3)
    with pytest.raises(mt.MontadorEntradasError, match="publicou formula"):
        mt.montar_entradas(decisoes["rco_diaria_cobertura"], outra, fatos)


def test_origem_divergente_recusa(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    outra = _com_entrada(dic, "idadeBeneficiario", origem="tiss")
    fatos = mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3)
    with pytest.raises(mt.MontadorEntradasError, match="origem 'tiss' no dicionario"):
        mt.montar_entradas(decisoes["rco_diaria_cobertura"], outra, fatos)


# --- dominios fora do escopo e entradas novas -------------------------------------------------------


def test_dominio_sem_mapeamento_vai_para_pessoa(dic: DicionarioEntradasK1, decisoes: dict[str, Any]) -> None:
    alheia = dataclasses.replace(
        decisoes["rco_geral_carencia"], id="rco_coluna_dut_133", dominio="opme-coluna"
    )
    with pytest.raises(mt.DominioNaoSuportadoError):
        mt.montar_entradas(alheia, dic, mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3))


def test_entrada_nova_no_pacote_recusa_ate_rever_o_montador(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any]
) -> None:
    carencia = decisoes["rco_geral_carencia"]
    com_nova = dataclasses.replace(carencia, entradas=(*carencia.entradas, "etiquetaRastreabilidade"))
    with pytest.raises(mt.MontadorEntradasError, match="ausente do dicionario"):
        mt.montar_entradas(com_nova, dic, mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3))
    do_outro_dominio = dataclasses.replace(carencia, entradas=(*carencia.entradas, "idadeBeneficiario"))
    with pytest.raises(mt.MontadorEntradasError, match="sem mapeamento no dominio 'regras-gerais'"):
        mt.montar_entradas(do_outro_dominio, dic, mt.FatosDoCaso(guia=_guia(), versao_k3_pinada=PIN_SEM_K3))


# --- versao do contrato AMH pinado ------------------------------------------------------------------


def test_versao_manifesto_pinada_le_o_pin_real() -> None:
    from maezo.adapters.amh.contract import CONTRACT_PIN_RELATIVE_PATH, load_contract_pin

    raiz = Path(__file__).resolve().parents[4]
    lock = json.loads((raiz / CONTRACT_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    blocos = [int(m.group(1)) for k in lock if (m := re.fullmatch(r"manifest_v1_([0-9]+)", k))]
    esperado = (1, max(blocos, default=0))
    assert load_contract_pin().versao_manifesto_pinada == esperado
    # O K3 das entradas (v1.16) entra no montador SO quando o pin chegar a ele.
    assert (esperado >= mt.VERSAO_K3_ENTRADAS) is ("manifest_v1_16" in lock)
