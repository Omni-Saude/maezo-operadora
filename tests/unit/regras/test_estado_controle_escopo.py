"""Chancela de regras: derivação pura do estado (§3.6), controle de alterações gerado e cobertura."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from maezo.regras.catalogo.escopo import validar_escopo, verificar_cobertura
from maezo.regras.catalogo.estado import (
    ESTADOS,
    Decisao,
    EntradaDoEstado,
    Liberacao,
    Publicacao,
    Rodada,
    derivar_estado,
)
from maezo.regras.catalogo.schema import validar_catalogo
from maezo.regras.controle import CABECALHO, gerar, linhas
from tests.unit.regras.fabrica import ARQ_CATALOGO, PROCESSO, catalogo_base, regra_base

H = "a" * 64
H2 = "b" * 64
_HOJE = date(2026, 11, 19)
RODADA = Rodada("r1", H, "legado", "aberta")


def _entrada(**over: Any) -> EntradaDoEstado:
    base: dict[str, Any] = {
        "estado_declarado": "legado_pendente",
        "sha256_vigente": H,
        "aprovam": ("financas", "dono"),
        "hoje": _HOJE,
        "rodada": RODADA,
    }
    base.update(over)
    return EntradaDoEstado(**base)


def _d(papel: str, decisao: str = "de_acordo", **over: Any) -> Decisao:
    base: dict[str, Any] = {
        "decisao_id": f"d-{papel}",
        "rodada_id": "r1",
        "regra_sha256": H,
        "papel": papel,
        "decisao": decisao,
    }
    base.update(over)
    return Decisao(**base)


def _estado(**over: Any) -> str:
    return derivar_estado(_entrada(**over)).estado


# --- estado ---------------------------------------------------------------------------------------


def test_legado_sem_rodada_e_legado_pendente() -> None:
    assert _estado(rodada=None) == "legado_pendente"


def test_rodada_aberta_incompleta_e_em_validacao() -> None:
    assert _estado(decisoes=[_d("financas")]) == "em_validacao"
    assert _estado(rodada=replace(RODADA, tipo="revalidacao"), decisoes=[]) == "em_revalidacao"


def test_um_nao_vigente_e_ajuste_pedido() -> None:
    assert _estado(decisoes=[_d("financas"), _d("dono", "nao")]) == "ajuste_pedido"


def test_nao_substituido_nao_conta() -> None:
    decisoes = [_d("financas"), _d("dono", "nao", substituida=True, decisao_id="d-x"), _d("dono")]
    assert _estado(decisoes=decisoes) == "aguardando_liberacao"


@pytest.mark.parametrize("campo", ["rodada_id", "regra_sha256"])
def test_decisao_de_outra_rodada_ou_outro_hash_nao_conta(campo: str) -> None:
    outra = _d("dono", **{campo: "r9" if campo == "rodada_id" else H2})
    assert _estado(decisoes=[_d("financas"), outra]) == "em_validacao"


def test_condicao_sem_comprovacao_e_com_comprovacao() -> None:
    cond = _d("dono", "de_acordo_com_ressalva", ressalva_tipo="condicao")
    assert _estado(decisoes=[_d("financas"), cond]) == "aprovada_com_condicao"
    comprovada = _estado(decisoes=[_d("financas"), cond], condicoes_comprovadas=frozenset({"d-dono"}))
    assert comprovada == "aguardando_liberacao"


def test_observacao_nao_bloqueia() -> None:
    obs = _d("dono", "de_acordo_com_ressalva", ressalva_tipo="observacao")
    assert _estado(decisoes=[_d("financas"), obs]) == "aguardando_liberacao"


def test_liberar_e_devolver() -> None:
    todos = [_d("financas"), _d("dono")]
    assert _estado(decisoes=todos, liberacao=Liberacao("r1", H, "liberar")) == "aprovada_aguardando_merge"
    assert _estado(decisoes=todos, liberacao=Liberacao("r1", H, "devolver")) == "em_revalidacao"
    assert _estado(decisoes=todos, liberacao=Liberacao("r0", H, "liberar")) == "aguardando_liberacao"


def test_rodada_de_outro_hash_ou_fechada_e_ignorada() -> None:
    assert (
        _estado(rodada=replace(RODADA, regra_sha256=H2), decisoes=[_d("financas"), _d("dono")])
        == "legado_pendente"
    )
    assert _estado(rodada=replace(RODADA, situacao="cancelada")) == "legado_pendente"


def test_aprovada_no_git_e_publicacao() -> None:
    ok = frozenset({"financas", "dono"})
    assert _estado(rodada=None, aprovados_no_git=ok) == "aprovada"
    assert _estado(rodada=None, aprovados_no_git=ok, publicacao=Publicacao(True, True)) == "em_homologacao"
    assert _estado(rodada=None, aprovados_no_git=ok, publicacao=Publicacao(True, True, True)) == "em_producao"
    assert _estado(rodada=None, aprovados_no_git=frozenset({"financas"})) == "legado_pendente"


def test_regra_ativa_que_mudou_sem_aprovacao_esta_em_revalidacao() -> None:
    assert _estado(rodada=None, estado_declarado="ativa") == "em_revalidacao"


def test_remocao_e_removida() -> None:
    assert _estado(estado_declarado="removida") == "removida"
    assert (
        _estado(rodada=replace(RODADA, tipo="remocao"), decisoes=[_d("financas"), _d("dono")]) == "em_remocao"
    )
    assert _estado(rodada=None, estado_declarado="em_remocao") == "em_remocao"


def test_nada_muda_por_tempo_so_a_flag_de_validade() -> None:
    """RF-27: nenhuma regra e' aprovada por decurso de prazo; vencida segue e alerta (RF-46)."""
    ok = frozenset({"financas", "dono"})
    antes = derivar_estado(_entrada(rodada=None, aprovados_no_git=ok, valida_ate=date(2027, 11, 18)))
    depois = derivar_estado(
        _entrada(rodada=None, aprovados_no_git=ok, valida_ate=date(2027, 11, 18), hoje=date(2027, 11, 19))
    )
    assert (antes.estado, antes.validade_vencida) == ("aprovada", False)
    assert (depois.estado, depois.validade_vencida) == ("aprovada", True)
    sem_resposta = derivar_estado(_entrada(hoje=date(2030, 1, 1)))
    assert sem_resposta.estado == "em_validacao"


def test_todo_estado_derivado_esta_no_conjunto_fechado() -> None:
    casos = [
        _entrada(rodada=None),
        _entrada(decisoes=[_d("financas"), _d("dono", "nao")]),
        _entrada(rodada=None, aprovados_no_git=frozenset({"financas", "dono"})),
    ]
    assert {derivar_estado(c).estado for c in casos} <= set(ESTADOS)


# --- controle de alteracoes ---------------------------------------------------------------------


def _catalogo_validado(repo: Path, **over: Any) -> Any:
    validado, achados = validar_catalogo(
        catalogo_base(regra_base(**over)), arquivo=ARQ_CATALOGO, raiz=repo, hoje=_HOJE
    )
    assert validado is not None, achados
    return validado


def test_controle_vazio_quando_nada_foi_decidido(repo: Path) -> None:
    texto = gerar([_catalogo_validado(repo)])
    assert texto.startswith(CABECALHO)
    assert "linhas: []" in texto


def test_controle_tem_uma_linha_por_evento_e_e_deterministico(repo: Path) -> None:
    manual = {"evidencia_url": "https://x/1", "evidencia_sha256": "b" * 64, "transcrito_por": "P"}
    aprov = {
        "papel": "financas",
        "aprovador": "Pessoa Ficticia",
        "decisao": "de_acordo",
        "regra_sha256": H,
        "data": "2026-11-18",
        "valida_ate": "2027-11-18",
        "recibo": None,
        "transcricao_manual": manual,
    }
    hist = {"pr": 7, "o_que_mudou": "Teto ajustado.", "motivo": "Decisao registrada."}
    cat = _catalogo_validado(repo, aprovacoes=[aprov], historico_de_mudanca=[hist])
    lin = linhas([cat])
    assert [x["evento"] for x in lin] == ["mudanca", "aprovacao"]
    assert lin[1]["aprovador"] == "Pessoa Ficticia" and lin[1]["recibo"] == ""
    assert gerar([cat]) == gerar([cat])


# --- escopo e cobertura --------------------------------------------------------------------------


def _escopo(*artefatos: dict[str, Any], portao: str = "observar", desde_pr: Any = None) -> dict[str, Any]:
    return {
        "schema": "maezo-regras-escopo.v1",
        "processos": [
            {
                "processo": PROCESSO,
                "portao": portao,
                "desde_pr": desde_pr,
                "artefatos_cobertos": list(artefatos),
            }
        ],
    }


def test_cobertura_verde_quando_toda_folha_tem_regra(repo: Path) -> None:
    escopo, achados = validar_escopo(
        _escopo({"arquivo": "spec/policies/p.yaml", "caminho": ["acoes", "reembolso", "params", "teto"]})
    )
    assert achados == []
    assert verificar_cobertura(repo, escopo, [_catalogo_validado(repo)]) == []


def test_chave_nova_sob_caminho_coberto_sem_regra_reprova(repo: Path) -> None:
    """RF-07: `limite` esta' sob o caminho coberto e nenhuma regra aponta para ele."""
    escopo, _ = validar_escopo(
        _escopo({"arquivo": "spec/policies/p.yaml", "caminho": ["acoes", "reembolso", "params"]})
    )
    achados = verificar_cobertura(repo, escopo, [_catalogo_validado(repo)])
    assert len(achados) == 1 and "limite" in achados[0] and "RF-07" in achados[0]


def test_fonte_no_ancestral_cobre_a_folha(repo: Path) -> None:
    escopo, _ = validar_escopo(
        _escopo({"arquivo": "spec/policies/p.yaml", "caminho": ["acoes", "reembolso", "params"]})
    )
    cat = _catalogo_validado(
        repo, fonte=[{"arquivo": "spec/policies/p.yaml", "caminho": ["acoes", "reembolso"]}]
    )
    assert verificar_cobertura(repo, escopo, [cat]) == []


def test_dmn_inteiro_exige_uma_regra_por_linha(repo: Path) -> None:
    (repo / "spec/processes/dmn").mkdir(parents=True)
    (repo / "spec/processes/dmn/t.dmn").write_text(
        '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"><decision id="d">'
        '<decisionTable id="dt" hitPolicy="FIRST"><input id="i"/><output id="o"/>'
        '<rule id="r1"/><rule id="r2"/></decisionTable></decision></definitions>',
        encoding="utf-8",
    )
    escopo, _ = validar_escopo(_escopo({"arquivo": "spec/processes/dmn/t.dmn"}))
    cat = _catalogo_validado(
        repo, tipo="dmn_linha", fonte=[{"arquivo": "spec/processes/dmn/t.dmn", "regra": "r1"}]
    )
    achados = verificar_cobertura(repo, escopo, [cat])
    assert len(achados) == 1 and "'r2'" in achados[0]


@pytest.mark.parametrize(
    ("dados", "trecho"),
    [
        ({"schema": "x", "processos": []}, "`schema`"),
        (_escopo({"arquivo": "a", "caminho": ["x"]}, portao="ligado"), "`portao`"),
        (_escopo({"arquivo": "a", "caminho": ["x"]}, portao="exigir"), "exige `desde_pr`"),
        (_escopo({"arquivo": "a", "linha": 3}), "forma fora"),
        (_escopo(), "lista nao vazia"),
    ],
)
def test_escopo_malformado(dados: dict[str, Any], trecho: str) -> None:
    _, achados = validar_escopo(dados)
    assert any(trecho in a for a in achados), achados


def test_processo_do_escopo_sem_catalogo_e_artefato_irresolvivel(repo: Path) -> None:
    escopo, _ = validar_escopo(_escopo({"arquivo": "spec/policies/p.yaml", "caminho": ["nada"]}))
    assert any("sem catalogo" in a for a in verificar_cobertura(repo, escopo, []))
    assert any("irresolvivel" in a for a in verificar_cobertura(repo, escopo, [_catalogo_validado(repo)]))
