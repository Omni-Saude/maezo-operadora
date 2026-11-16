"""Tolerancia do prazo de apresentacao da conta no montador do K1 (MZO-TOL, decisao do dono PROG-COORD-22).

A regra do rco e o contrato dizem 30 dias; o Maezo aceita ate 60 por CONFIGURACAO INTERNA unica
(`config/integrations/rco/tolerancia-apresentacao-conta.json`): o montador entrega a
`rco_geral_prazos_conta` `max(0, diasAlemDoPrazoContratual - tolerancia)` quando os dias sao positivos.
Prova:

- os limites do exemplo do dono (30, 31, 60 e 61 dias corridos com contrato de 30), outro prazo de
  contrato, termo nulo (nulo segue nulo) e o caminho da AAE na tabela real de `main` do rco;
- a configuracao fail-closed (invalida = tolerancia 0, nunca amplia);
- a guarda contra tolerancia dobrada (DMN pinado que ja libera faixa positiva limitada = tolerancia 0);
- o registro de auditoria (bruto, tolerancia aplicada, valor entregue, motivo);
- que o ajuste vale so para `diasAlemDoPrazoContratual` (nunca para `prazoComprovacaoUsoVencido`);
- o sigilo: nem a chave nem o numero aparecem nos caminhos que falam com prestador ou beneficiario.

`diasAteApresentacao` nao tem formula publicada (dicionario 1.1.0): para exercitar o caminho inteiro os
testes INJETAM o termo com uma formula falsa (dicionario e `FORMULAS` so do teste); em producao o ajuste
fica inerte enquanto o termo for nulo.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from maezo.platform import rco_manifesto as mf
from maezo.platform.rco_entradas import DicionarioEntradasK1, FormulaK1, TermoFormulaK1
from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers import rco_pin
from tests.unit.tools.workers import rco_recorte

RAIZ = Path(__file__).resolve().parents[4]
PIN_SEM_K3 = (1, 15)
DATA = "2026-12-01"
DIAS_ALEM = "diasAlemDoPrazoContratual"
_TERMO_INJETADO = "diasInjetadosTeste"


@pytest.fixture(scope="module")
def decisoes() -> dict[str, Any]:
    return rco_recorte.decisoes()


@pytest.fixture
def dic_injetado(monkeypatch: pytest.MonkeyPatch) -> DicionarioEntradasK1:
    """Dicionario e montador em que `diasAteApresentacao` tem uma formula FALSA (so do teste): o termo
    vem do cadastro em `dias_injetados_teste`. A formula de `diasAlemDoPrazoContratual` e a real."""
    dic = rco_recorte.dicionario()
    falsa = FormulaK1("teste: dias injetados", (TermoFormulaK1(_TERMO_INJETADO, "cadastro", "teste"),))
    entradas = dict(dic.entradas)
    entradas["diasAteApresentacao"] = dataclasses.replace(entradas["diasAteApresentacao"], formula=falsa)

    def _injetado(termos: Any) -> tuple[int | None, mt.MotivoNulo | None]:
        valor = termos.get(_TERMO_INJETADO)
        return (valor, None) if isinstance(valor, int) else (None, mt.MotivoNulo.TERMO_AUSENTE)

    formulas = dict(mt.FORMULAS)
    formulas["diasAteApresentacao"] = mt._FormulaImplementada(
        texto="teste: dias injetados", termos=((_TERMO_INJETADO, "cadastro"),), calcular=_injetado
    )
    campos = {**mt.CAMPOS_TERMO_CADASTRO, _TERMO_INJETADO: "dias_injetados_teste"}
    mapas = dict(mt.MAPEAMENTOS)
    mapas["regras-gerais"] = MappingProxyType(
        {**mt.MAPEAMENTOS["regras-gerais"], "diasAteApresentacao": mt._COM_FORMULA}
    )
    monkeypatch.setattr(mt, "FORMULAS", MappingProxyType(formulas))
    monkeypatch.setattr(mt, "CAMPOS_TERMO_CADASTRO", MappingProxyType(campos))
    monkeypatch.setattr(mt, "MAPEAMENTOS", MappingProxyType(mapas))
    return dataclasses.replace(dic, entradas=entradas)


@pytest.fixture
def tolerancia(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configuracao de bordo (30) e DMN pinado sem tolerancia embutida."""
    monkeypatch.setattr(mt, "tolerancia_conta_configurada", lambda: 30)
    monkeypatch.setattr(mt, "dmn_prazos_conta_bloqueia_tolerancia", lambda: None)
    yield


def _montar(
    dic: DicionarioEntradasK1, decisoes: dict[str, Any], dias: object, prazo: object, aae: str | None = None
) -> mt.EntradasMontadas:
    cadastro: dict[str, object] = {}
    if dias is not None:
        cadastro["dias_injetados_teste"] = dias
    if prazo is not None:
        cadastro["prazo_apresentacao_contratual_dias"] = prazo
    if aae is not None:
        cadastro["autorizacao_extemporanea"] = aae
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": DATA, "tipo_transacao": "ENVIO_LOTE_GUIAS"},
        versao_k3_pinada=PIN_SEM_K3,
        cadastro=cadastro,
    )
    return mt.montar_entradas(decisoes["rco_geral_prazos_conta"], dic, fatos)


# --- limites do exemplo do dono e outro prazo de contrato -------------------------------------------


@pytest.mark.parametrize(
    ("dias_ate", "prazo", "bruto", "entregue"),
    [
        # "30, 31, 60 e 61 dias com contrato de 30": dias alem = 0, 1, 30, 31; entregues 0, 0, 0, 1
        (30, 30, 0, 0),
        (31, 30, 1, 0),
        (60, 30, 30, 0),
        (61, 30, 31, 1),
        (10, 30, -20, -20),  # no prazo: intacto
        # contrato de 45: tolera ate 75 dias corridos
        (45, 45, 0, 0),
        (46, 45, 1, 0),
        (75, 45, 30, 0),
        (76, 45, 31, 1),
        (120, 45, 75, 45),
    ],
)
def test_limites_com_tolerancia_de_30(
    dic_injetado: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    tolerancia: None,
    dias_ate: int,
    prazo: int,
    bruto: int,
    entregue: int,
) -> None:
    montadas = _montar(dic_injetado, decisoes, dias_ate, prazo)
    assert montadas.variaveis["diasAteApresentacao"] == dias_ate  # o termo bruto chega intacto a tabela
    assert montadas.variaveis[DIAS_ALEM] == entregue
    motivo = "aplicada" if bruto > 0 else "dentro_do_prazo_contratual"
    assert dict(montadas.derivacoes[DIAS_ALEM]) == {
        "diasBrutos": bruto,
        "toleranciaAplicada": 30 if bruto > 0 else 0,
        "valorEntregue": entregue,
        "motivoTolerancia": motivo,
    }


@pytest.mark.parametrize(("dias_ate", "prazo"), [(None, 30), (61, None), (None, None), (61, -1)])
def test_termo_nulo_segue_nulo_nunca_zero(
    dic_injetado: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    tolerancia: None,
    dias_ate: object,
    prazo: object,
) -> None:
    montadas = _montar(dic_injetado, decisoes, dias_ate, prazo)
    assert montadas.variaveis[DIAS_ALEM] is None
    esperado = "prazo_contratual_negativo" if prazo == -1 else "termo_da_formula_ausente"
    assert montadas.nulos[DIAS_ALEM] == esperado
    assert dict(montadas.derivacoes[DIAS_ALEM]) == {
        "diasBrutos": None,
        "toleranciaAplicada": None,
        "valorEntregue": None,
        "motivoTolerancia": "termo_nulo",
    }


def test_producao_inerte_enquanto_dias_ate_apresentacao_nao_tem_formula(decisoes: dict[str, Any]) -> None:
    """Sem a formula falsa: o termo e nulo por desenho, a derivada sai nula e nada de configuracao ou pin
    e consultado (o ajuste so os le com dias positivos)."""
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": DATA, "dias_ate_apresentacao": 61},
        versao_k3_pinada=PIN_SEM_K3,
        cadastro={"prazo_apresentacao_contratual_dias": 30},
    )
    montadas = mt.montar_entradas(decisoes["rco_geral_prazos_conta"], rco_recorte.dicionario(), fatos)
    assert montadas.variaveis[DIAS_ALEM] is None
    assert montadas.derivacoes[DIAS_ALEM]["motivoTolerancia"] == "termo_nulo"


@pytest.mark.parametrize(
    ("dias", "tol", "esperado"),
    [
        (None, 30, None),
        (-3, 30, -3),
        (0, 30, 0),
        (1, 30, 0),
        (30, 30, 0),
        (31, 30, 1),
        (31, 0, 31),
        (5, 365, 0),
    ],
)
def test_aplicar_tolerancia(dias: int | None, tol: int, esperado: int | None) -> None:
    assert mt.aplicar_tolerancia(dias, tol) == esperado


# --- caminho da AAE na tabela real (main do rco, sem tolerancia no DMN) -----------------------------

_FEEL_CMP = re.compile(r'^(<=|<|>=|>)\s*(-?\d+|"[^"]*")$')
_FEEL_INTERVALO = re.compile(r"^\[(-?\d+)\.\.(-?\d+)\]$")
_NS = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


def _partes(celula: str) -> list[str]:
    return [p.strip() for p in re.findall(r'(?:"[^"]*"|[^,])+', celula)]


def _casa(celula: str, valor: object) -> bool:
    """Leitura das celulas que esta tabela usa (`-`, `null`, literais, `not(...)`, comparacao,
    intervalo fechado), com a semantica do Camunda 7: comparacao com nulo nao casa."""
    celula = celula.strip()
    if celula == "-":
        return True
    if celula.startswith("not(") and celula.endswith(")"):
        return not _casa(celula[4:-1], valor)
    for parte in _partes(celula):
        if parte == "null":
            if valor is None:
                return True
            continue
        if valor is None:
            continue
        if parte.startswith('"'):
            if valor == parte.strip('"'):
                return True
            continue
        cmp = _FEEL_CMP.fullmatch(parte)
        if cmp is not None:
            alvo: Any = cmp.group(2).strip('"') if cmp.group(2).startswith('"') else int(cmp.group(2))
            v: Any = valor
            if {"<": v < alvo, "<=": v <= alvo, ">": v > alvo, ">=": v >= alvo}[cmp.group(1)]:
                return True
            continue
        intervalo = _FEEL_INTERVALO.fullmatch(parte)
        if intervalo is not None:
            if int(intervalo.group(1)) <= int(valor) <= int(intervalo.group(2)):  # type: ignore[call-overload]
                return True
            continue
        raise AssertionError(f"celula nao lida pelo teste: {parte!r}")
    return False


def _avaliar(variaveis: dict[str, object]) -> str:
    root = ET.fromstring(rco_recorte.PRAZOS_CONTA_DMN.read_bytes())
    tabela = next(root.iter(f"{_NS}decisionTable"))
    colunas = [
        (i.find(f"{_NS}inputExpression/{_NS}text").text or "").strip()  # type: ignore[union-attr]
        for i in tabela.findall(f"{_NS}input")
    ]
    casadas = []
    for regra in tabela.findall(f"{_NS}rule"):
        celulas = [(e.find(f"{_NS}text").text or "") for e in regra.findall(f"{_NS}inputEntry")]  # type: ignore[union-attr]
        if all(_casa(c, variaveis.get(n)) for n, c in zip(colunas, celulas, strict=True)):
            saida = regra.findall(f"{_NS}outputEntry")[0].find(f"{_NS}text")
            casadas.append((saida.text or "").strip('"'))  # type: ignore[union-attr]
    assert len(casadas) == 1, casadas  # UNIQUE
    return casadas[0]


@pytest.mark.parametrize(
    ("dias_ate", "aae", "decisao"),
    [
        # 1 e 30 dias alem com AAE nao concedida (C058/C059 da branch cancelada, traduzidos): entregue 0
        (31, "nao_concedida", "liberar"),
        (60, "nao_concedida", "liberar"),
        (60, None, "liberar"),
        (60, "em_analise", "liberar"),
        # acima da tolerancia: a AAE da tabela volta a valer
        (61, "concedida", "liberar"),
        (61, "nao_concedida", "glosar"),
        (61, "nao_solicitada", "glosar"),
        (61, "em_analise", "revisao_humana"),
        (61, None, "revisao_humana"),
    ],
)
def test_aae_concedida_ou_ausente_na_tabela_real(
    dic_injetado: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    tolerancia: None,
    dias_ate: int,
    aae: str | None,
    decisao: str,
) -> None:
    montadas = _montar(dic_injetado, decisoes, dias_ate, 30, aae)
    assert montadas.variaveis["autorizacaoExtemporanea"] == aae
    assert _avaliar(dict(montadas.variaveis)) == decisao


def test_tabela_real_de_main_nao_tolera() -> None:
    """A forma atual da tabela (glosa com `> 0`): a guarda nao acha nada, o pin passa."""
    raw = rco_recorte.PRAZOS_CONTA_DMN.read_bytes()
    assert mf.tolerancia_prazo_conta_no_dmn(raw, "geral_prazos_conta.dmn") == []


# --- configuracao: unica, estrita, fail-closed ------------------------------------------------------


def test_configuracao_de_bordo_vale_30_com_procedencia() -> None:
    caminho = RAIZ / mt.TOLERANCIA_CONTA_RELATIVO
    assert mt.ler_tolerancia_conta(caminho) == 30
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    assert "PROG-COORD-22" in dados["decidido_por"]


_VALIDO: dict[str, Any] = {
    "_comentario": ["x"],
    "tolerancia_apresentacao_conta_dias": 30,
    "decidido_por": "dono",
    "decidido_em": "2026-11-16",
}


def _arquivo(tmp_path: Path, conteudo: object) -> Path:
    caminho = tmp_path / "tolerancia.json"
    caminho.write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo), encoding="utf-8")
    return caminho


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        (_VALIDO, 30),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": 0}, 0),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": 365}, 365),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": 366}, None),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": -1}, None),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": True}, None),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": "30"}, None),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": 30.0}, None),
        ({**_VALIDO, "tolerancia_apresentacao_conta_dias": None}, None),
        ({**_VALIDO, "decidido_por": " "}, None),
        ({**_VALIDO, "decidido_em": None}, None),
        ({**_VALIDO, "por_prestador": {"123": 90}}, None),  # nunca por prestador, caso ou tenant
        ({k: v for k, v in _VALIDO.items() if k != "tolerancia_apresentacao_conta_dias"}, None),
        ([30], None),
        ('{"tolerancia_apresentacao_conta_dias": 30, "tolerancia_apresentacao_conta_dias": 90}', None),
        ('{"tolerancia_apresentacao_conta_dias": ', None),
    ],
)
def test_configuracao_estrita(tmp_path: Path, conteudo: object, esperado: int | None) -> None:
    assert mt.ler_tolerancia_conta(_arquivo(tmp_path, conteudo)) == esperado


def test_configuracao_ausente_le_none(tmp_path: Path) -> None:
    assert mt.ler_tolerancia_conta(tmp_path / "nao-existe.json") is None


def test_configuracao_invalida_entrega_o_bruto_com_tolerancia_zero(
    dic_injetado: DicionarioEntradasK1,
    decisoes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ruim = _arquivo(tmp_path, {**_VALIDO, "tolerancia_apresentacao_conta_dias": 9999})
    monkeypatch.setattr(mt, "tolerancia_conta_configurada", lambda: mt.ler_tolerancia_conta(ruim))
    monkeypatch.setattr(mt, "dmn_prazos_conta_bloqueia_tolerancia", lambda: None)
    montadas = _montar(dic_injetado, decisoes, 61, 30)
    assert montadas.variaveis[DIAS_ALEM] == 31  # o prazo do contrato, nunca mais largo
    assert dict(montadas.derivacoes[DIAS_ALEM]) == {
        "diasBrutos": 31,
        "toleranciaAplicada": 0,
        "valorEntregue": 31,
        "motivoTolerancia": "configuracao_invalida",
    }


# --- guarda contra tolerancia dobrada ----------------------------------------------------------------

_CABECALHO = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d" name="d" namespace="http://example.invalid/t">
  <decision id="rco_geral_prazos_conta" name="sintetico">
    <decisionTable id="t" hitPolicy="UNIQUE">
      <input id="i1"><inputExpression typeRef="integer">
        <text>diasAlemDoPrazoContratual</text></inputExpression></input>
      <input id="i2"><inputExpression typeRef="string">
        <text>autorizacaoExtemporanea</text></inputExpression></input>
      <output id="o1" name="decisao" typeRef="string"/>
      <output id="o2" name="idRegra" typeRef="string"/>
"""


def _dmn_sintetico(*linhas: tuple[str, str, str]) -> bytes:
    regras = "".join(
        f'<rule id="r{i}"><inputEntry><text>{dias}</text></inputEntry>'
        f"<inputEntry><text>{aae}</text></inputEntry>"
        f'<outputEntry><text>{decisao}</text></outputEntry><outputEntry><text>""</text></outputEntry></rule>'
        for i, (dias, aae, decisao) in enumerate(linhas)
    )
    return (_CABECALHO + regras + "</decisionTable></decision></definitions>").encode()


_SEM_TOLERANCIA = (
    ("&lt;= 0", "-", '"liberar"'),
    ("&gt; 0", '"concedida"', '"liberar"'),
    ("&gt; 0", "-", '"glosar"'),
)


@pytest.mark.parametrize(
    "linha",
    [
        ("[1..30]", "-", '"liberar"'),
        ("[0..30]", "-", '"liberar"'),
        ("(0..30]", "-", '"liberar"'),
        ("&lt;= 30", "-", '"liberar"'),
        ("15", "-", '"liberar"'),
        ("null, [1..30]", "-", '"liberar"'),
        ("[1..30]", "-", "decisaoVariavel"),  # decisao nao literal: fail-closed
        ("not(&gt; 30)", "-", '"liberar"'),  # nao lida: fail-closed
    ],
)
def test_guarda_acha_tolerancia_embutida(linha: tuple[str, str, str]) -> None:
    achados = mf.tolerancia_prazo_conta_no_dmn(_dmn_sintetico(*_SEM_TOLERANCIA, linha), "sint.dmn")
    assert len(achados) == 1 and "linha 3" in achados[0], achados


@pytest.mark.parametrize(
    "linha",
    [
        ("[1..30]", "-", '"glosar"'),  # faixa limitada que nao libera
        ("&gt; 30", "-", '"liberar"'),  # sem limite superior
        ("&gt;= 1", '"concedida"', '"liberar"'),
        ("&lt; 0, null", "-", '"revisao_humana"'),
        ("null", "-", '"liberar"'),
        ("-", "-", '"liberar"'),
    ],
)
def test_guarda_nao_acha_o_que_nao_e_tolerancia(linha: tuple[str, str, str]) -> None:
    assert mf.tolerancia_prazo_conta_no_dmn(_dmn_sintetico(*_SEM_TOLERANCIA, linha), "sint.dmn") == []


def test_guarda_dmn_ilegivel_e_achado() -> None:
    assert mf.tolerancia_prazo_conta_no_dmn(b"<nao-fecha", "x.dmn")


def test_guarda_ignora_tabela_sem_a_entrada() -> None:
    raw = rco_recorte.CARENCIA_DMN.read_bytes()
    assert mf.tolerancia_prazo_conta_no_dmn(raw, "geral_carencia.dmn") == []


def _pin_com_tabela(achados: tuple[str, ...]) -> rco_pin.RcoRulesPin:
    tabela = rco_pin.RcoPinnedTable(
        "rco_geral_prazos_conta",
        "spec/processes/dmn/rco/x.dmn",
        "sha256:" + "0" * 64,
        tolerancia_no_dmn=achados,
    )
    return rco_pin.RcoRulesPin(estado="fixado", tables=MappingProxyType({tabela.decision_key: tabela}))


def test_runtime_com_dmn_que_ja_tolera_entrega_o_bruto(
    dic_injetado: DicionarioEntradasK1, decisoes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrato 30 + a folga do proprio DMN = 60: o Maezo zera a sua e entrega os dias brutos."""
    monkeypatch.setattr(mt, "tolerancia_conta_configurada", lambda: 30)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: _pin_com_tabela(("linha 3: libera faixa",)))
    montadas = _montar(dic_injetado, decisoes, 61, 30)
    assert montadas.variaveis[DIAS_ALEM] == 31
    assert dict(montadas.derivacoes[DIAS_ALEM]) == {
        "diasBrutos": 31,
        "toleranciaAplicada": 0,
        "valorEntregue": 31,
        "motivoTolerancia": "dmn_ja_tolera",
    }


def test_runtime_com_dmn_limpo_aplica(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: _pin_com_tabela(()))
    assert mt.dmn_prazos_conta_bloqueia_tolerancia() is None


def test_runtime_sem_tabela_ou_pin_ilegivel_nao_verifica(monkeypatch: pytest.MonkeyPatch) -> None:
    vazio = rco_pin.RcoRulesPin(estado="sem_pacote", tables=MappingProxyType({}))
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: vazio)
    assert mt.dmn_prazos_conta_bloqueia_tolerancia() is mt.MotivoTolerancia.DMN_NAO_VERIFICADO

    def _quebra() -> rco_pin.RcoRulesPin:
        raise rco_pin.RcoRulesPinError("pin ilegivel")

    monkeypatch.setattr(rco_pin, "rco_rules_pin", _quebra)
    assert mt.dmn_prazos_conta_bloqueia_tolerancia() is mt.MotivoTolerancia.DMN_NAO_VERIFICADO


def test_carregador_do_pin_registra_o_achado_da_tabela(tmp_path: Path) -> None:
    """O runtime le o achado com a MESMA funcao do gate, sobre os bytes pinados (exemplo sintetico)."""
    rules = tmp_path / "spec" / "processes" / "dmn" / "rco"
    rules.mkdir(parents=True)
    raw = _dmn_sintetico(*_SEM_TOLERANCIA, ("[1..30]", "-", '"liberar"'))
    (rules / "prazos.dmn").write_bytes(raw)
    lock = {
        "lock_format": {"name": "rco-rules-lock", "version": "2.0.0", "rules_dir": "spec/processes/dmn/rco"},
        "estado": "sem_pacote",
        "provenance": {"rco_repo": "x", "rco_commit_sha": None},
        "manifest_pin": None,
        "artifacts": [],
        "exemplos_sinteticos": [
            {"path": "spec/processes/dmn/rco/prazos.dmn", "sha256": mf.sha256_prefixado(raw)}
        ],
    }
    caminho = tmp_path / "config" / "integrations" / "rco" / "rules.lock.json"
    caminho.parent.mkdir(parents=True)
    caminho.write_text(json.dumps(lock), encoding="utf-8")
    pin = rco_pin.load_rco_rules_pin(caminho, rules_dir=rules)
    assert len(pin.tables["rco_geral_prazos_conta"].tolerancia_no_dmn) == 1


# --- so diasAlemDoPrazoContratual -------------------------------------------------------------------


def test_ajuste_so_em_dias_alem_do_prazo_contratual() -> None:
    com_ajuste = {nome for nome, impl in mt.FORMULAS.items() if impl.ajuste is not None}
    assert com_ajuste == {DIAS_ALEM}
    # A comprovacao de uso da OPME de urgencia segue o prazo do contrato: nao e mapeada pelo montador
    # nem tem formula ou ajuste aqui; se um dominio a mapear, este teste obriga a rever.
    assert "prazoComprovacaoUsoVencido" not in mt.FORMULAS
    assert all("prazoComprovacaoUsoVencido" not in mapa for mapa in mt.MAPEAMENTOS.values())


# --- sigilo: nada da tolerancia no que fala com prestador ou beneficiario ---------------------------

#: Caminhos que falam com o beneficiario (Helena, seus textos e o manual K2) e com o prestador (avisos de
#: glosa e demonstrativo de contas, recurso de glosa, a agente de contas e os textos de saida comuns).
VARREDURA_SIGILO = (
    "src/maezo/agents/helena",
    "spec/agents/helena",
    "src/maezo/domain/comunicacao",
    "src/maezo/agents/marina",
    "spec/agents/marina",
    "src/maezo/tools/workers/contas.py",
    "src/maezo/tools/workers/recurso.py",
    "src/maezo/platform/notification_bridge.py",
    "config/integrations/rco/k2.lock.json",
)
_PROIBIDO = re.compile(
    r"tolerancia[-_ ]?apresenta|toleranciaAplicada|motivoTolerancia|"
    r"toler[aâ]ncia\s+(interna|de\s+apresenta|do\s+prazo|da\s+conta)|\b60\s*dias\b|sessenta\s+dias",
    re.IGNORECASE,
)


def _arquivos_varridos() -> list[Path]:
    arquivos: list[Path] = []
    for rel in VARREDURA_SIGILO:
        caminho = RAIZ / rel
        assert caminho.exists(), rel  # a varredura nao pode ficar vazia por caminho renomeado
        arquivos.extend(
            [caminho] if caminho.is_file() else sorted(p for p in caminho.rglob("*") if p.is_file())
        )
    return arquivos


def test_padrao_de_sigilo_pega_a_chave_e_o_numero() -> None:
    assert _PROIBIDO.search(mt.CHAVE_TOLERANCIA_CONTA)
    assert _PROIBIDO.search(mt.TOLERANCIA_CONTA_RELATIVO)
    assert _PROIBIDO.search("aceitamos a conta em ate 60 dias")
    assert _PROIBIDO.search("tolerância interna")


def test_sigilo_nos_caminhos_de_prestador_e_beneficiario() -> None:
    achados: list[str] = []
    for arquivo in _arquivos_varridos():
        if arquivo.suffix in {".pyc"}:
            continue
        texto = arquivo.read_text(encoding="utf-8", errors="replace")
        achados.extend(f"{arquivo.relative_to(RAIZ)}: {m.group(0)!r}" for m in _PROIBIDO.finditer(texto))
    assert achados == []
