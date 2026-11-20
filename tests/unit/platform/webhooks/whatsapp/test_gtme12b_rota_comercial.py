"""GTM-E-12b: rota do despachante para o agente `comercial`, so' decisao, fechada. Nada envia."""

from __future__ import annotations

import ast
import inspect
import itertools
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.comercial import graph as comercial_graph
from maezo.platform.webhooks.whatsapp import rota_comercial as rc
from maezo.platform.webhooks.whatsapp.rota_comercial import DecisaoDeRota, MotivoRota, avaliar_rota

_FONTE = Path(rc.__file__).resolve()
_RAIZ = _FONTE.parents[5]
_SRC = _RAIZ / "src"


class _StrFilha(str):
    """Subclasse de str: o tipo exato e' exigido, entao ela nunca passa por `prospect`."""


_TIPOS = (
    "prospect",
    "lead",
    "beneficiario",
    "desconhecido",
    "",
    "Prospect",
    " prospect",
    None,
    1,
    _StrFilha("prospect"),
)
_BOOLS = (True, False, None, 1, 0, "true", "True")


def _avaliar(tipo: object, sessao: object, janela: object) -> DecisaoDeRota:
    return avaliar_rota(
        tipo_titular=tipo, sessao_iniciada_pelo_titular=sessao, dentro_da_janela_de_entrada=janela
    )


@pytest.fixture
def _ligada(monkeypatch: pytest.MonkeyPatch) -> None:
    # So' nestes testes: prova as recusas que vem depois da chave e da guarda. Producao nao tem como ligar.
    monkeypatch.setattr(rc, "ROTA_COMERCIAL_LIGADA", True)
    monkeypatch.setattr(rc, "GUARDA_DE_PROSPECT_NO_ACESSO", True)


def _literal_unico(nome: str) -> object:
    atribuicoes: list[ast.AnnAssign | ast.Assign] = []
    for n in ast.walk(ast.parse(_FONTE.read_text(encoding="utf-8"))):
        alvos = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == nome for t in alvos):
            assert isinstance(n, ast.AnnAssign | ast.Assign)
            atribuicoes.append(n)
    assert len(atribuicoes) == 1, nome
    valor = atribuicoes[0].value
    assert isinstance(valor, ast.Constant), nome
    return valor.value


def _recusada(decisao: DecisaoDeRota, motivo: MotivoRota) -> None:
    assert decisao == DecisaoDeRota(permitido=False, motivo=motivo, agente_id=None, acao_pretendida=None)


# ---------------------------------------------------------------- estado fechado


def test_chave_desligada_fixada_no_codigo() -> None:
    assert rc.ROTA_COMERCIAL_LIGADA is False
    assert _literal_unico("ROTA_COMERCIAL_LIGADA") is False


def test_chave_nao_liga_sem_a_guarda_do_fluxo_de_acesso() -> None:
    # Bloqueio da ligacao futura: com o acesso de beneficiario ligado, numero fora do cadastro cai no fluxo
    # de consentimento e identificacao do beneficiario. Falha se alguem puser a chave em True sem a guarda.
    ligada = _literal_unico("ROTA_COMERCIAL_LIGADA")
    guarda = _literal_unico("GUARDA_DE_PROSPECT_NO_ACESSO")
    assert guarda is False  # hoje a guarda nao existe
    assert ligada is not True or guarda is True


def test_ambiente_nao_liga_a_rota(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in (
        "ROTA_COMERCIAL_LIGADA",
        "MAEZO_ROTA_COMERCIAL",
        "MAEZO_ROTA_COMERCIAL_LIGADA",
        "COMERCIAL_LIGADO",
    ):
        monkeypatch.setenv(nome, "true")
    _recusada(_avaliar("prospect", True, True), MotivoRota.ROUTE_SWITCH_OFF)


def test_nunca_permitido_com_a_chave_desligada_para_nenhuma_entrada() -> None:
    combinacoes = list(itertools.product(_TIPOS, _BOOLS, _BOOLS))
    assert len(combinacoes) == len(_TIPOS) * len(_BOOLS) ** 2
    for tipo, sessao, janela in combinacoes:
        _recusada(_avaliar(tipo, sessao, janela), MotivoRota.ROUTE_SWITCH_OFF)


def test_assinatura_fechada_e_nomeada() -> None:
    parametros = inspect.signature(avaliar_rota).parameters
    assert list(parametros) == ["tipo_titular", "sessao_iniciada_pelo_titular", "dentro_da_janela_de_entrada"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parametros.values())
    posicional: Any = avaliar_rota
    with pytest.raises(TypeError):
        posicional("prospect", True, True)


# ---------------------------------------------------------------- recusas depois da chave (so' em teste)


def test_chave_ligada_sem_guarda_recusa_para_qualquer_entrada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "ROTA_COMERCIAL_LIGADA", True)
    for tipo, sessao, janela in itertools.product(_TIPOS, _BOOLS, _BOOLS):
        _recusada(_avaliar(tipo, sessao, janela), MotivoRota.ROUTE_ACCESS_FLOW_GUARD_MISSING)


@pytest.mark.usefixtures("_ligada")
@pytest.mark.parametrize("tipo", [t for t in _TIPOS if not (type(t) is str and t == "prospect")])
def test_sujeito_que_nao_e_prospect_recusa(tipo: object) -> None:
    # Beneficiario, lead e desconhecido recusam; desconhecido nunca vira prospect.
    _recusada(_avaliar(tipo, True, True), MotivoRota.ROUTE_SUBJECT_KIND_NOT_ALLOWED)


@pytest.mark.usefixtures("_ligada")
@pytest.mark.parametrize("sessao", [b for b in _BOOLS if b is not True])
def test_sessao_nao_iniciada_pelo_prospect_recusa(sessao: object) -> None:
    _recusada(_avaliar("prospect", sessao, True), MotivoRota.ROUTE_SESSION_NOT_SUBJECT_INITIATED)


@pytest.mark.usefixtures("_ligada")
@pytest.mark.parametrize("janela", [b for b in _BOOLS if b is not True])
def test_fora_da_janela_de_entrada_recusa(janela: object) -> None:
    _recusada(_avaliar("prospect", True, janela), MotivoRota.ROUTE_OUTSIDE_INBOUND_WINDOW)


@pytest.mark.usefixtures("_ligada")
def test_ordem_das_recusas() -> None:
    _recusada(_avaliar("beneficiario", False, False), MotivoRota.ROUTE_SUBJECT_KIND_NOT_ALLOWED)
    _recusada(_avaliar("prospect", False, False), MotivoRota.ROUTE_SESSION_NOT_SUBJECT_INITIATED)


@pytest.mark.usefixtures("_ligada")
def test_unico_caminho_devolve_so_a_acao_pretendida() -> None:
    permitidas = [c for c in itertools.product(_TIPOS, _BOOLS, _BOOLS) if _avaliar(*c).permitido]
    assert permitidas == [("prospect", True, True)]
    assert _avaliar("prospect", True, True) == DecisaoDeRota(
        permitido=True, motivo=None, agente_id="comercial", acao_pretendida="send_prospect_message"
    )


# ---------------------------------------------------------------- id e acao batem com o agente e a autonomia


def test_id_e_acao_batem_com_o_grafo_e_o_vocabulario() -> None:
    assert rc.AGENTE_ID == comercial_graph.AGENT_ID
    assert rc.ACAO_PRETENDIDA == comercial_graph.ACAO_ENVIO_PROSPECT
    l0 = yaml.safe_load((_RAIZ / "spec/policies/autonomy/L0-core.yaml").read_text(encoding="utf-8"))
    assert l0["actions"][rc.ACAO_PRETENDIDA] == {"level": "L3"}


# ---------------------------------------------------------------- isolamento


def _imports(fonte: str) -> set[str]:
    modulos: set[str] = set()
    for n in ast.walk(ast.parse(fonte)):
        if isinstance(n, ast.Import):
            modulos.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            modulos.add("." * n.level + (n.module or ""))
    return modulos


def test_modulo_so_importa_biblioteca_padrao() -> None:
    # Sem sender, sem grafo, sem settings, sem ambiente, sem log.
    assert _imports(_FONTE.read_text(encoding="utf-8")) == {"__future__", "dataclasses", "enum", "typing"}


def test_modulo_nao_le_ambiente_nem_settings() -> None:
    arvore = ast.parse(_FONTE.read_text(encoding="utf-8"))
    nomes = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)}
    nomes |= {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)}
    proibidos = {"os", "environ", "getenv", "settings", "Settings", "get_settings", "open", "logger"}
    proibidos |= {"logging", "structlog"}
    assert not nomes & proibidos
    # Nenhuma string de modulo de agente (a cerca da fronteira le strings fora de docstring).
    assert "maezo.agents" not in _FONTE.read_text(encoding="utf-8")


def test_nada_em_src_importa_o_modulo() -> None:
    citantes = sorted(
        str(p.relative_to(_SRC))
        for p in _SRC.rglob("*.py")
        if p.resolve() != _FONTE and "rota_comercial" in p.read_text(encoding="utf-8")
    )
    assert citantes == []
