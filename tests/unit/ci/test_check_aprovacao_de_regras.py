"""A cerca do registro de aprovacoes (DL-0149) — e a prova de que ela consegue ficar vermelha.

Os casos sinteticos montam uma arvore `spec/processes/{bpmn,dmn}` minima em `tmp_path`; os dois
ultimos testes leem o repositorio de verdade (nao-vacuidade e paridade com o deploy).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.check_aprovacao_de_regras import (
    _REPO_ROOT,
    artefatos_publicaveis,
    carregar,
    main,
    sha256_normalizado,
    verificar,
)

_HOJE = date(2026, 10, 19)
_BPMN = "spec/processes/bpmn/SP-X.bpmn"
_DMN = "spec/processes/dmn/regra_x.dmn"


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    (tmp_path / "spec/processes/bpmn").mkdir(parents=True)
    (tmp_path / "spec/processes/dmn").mkdir(parents=True)
    # bytes explicitos: `write_text` no Windows grava CRLF e mascararia o teste de fim de linha
    (tmp_path / _BPMN).write_bytes(b"<definitions>Status: DRAFT</definitions>\n")
    (tmp_path / _DMN).write_bytes(b"<definitions>regra aprovada</definitions>\n")
    # nao-publicavel: o deploy ignora; a cerca tambem
    (tmp_path / "spec/processes/dmn/x-ratification.yaml").write_text("a: 1\n", encoding="utf-8")
    return tmp_path


def _aprovado(raiz: Path, **over: Any) -> dict[str, Any]:
    base = {
        "artefato": _DMN,
        "sha256": sha256_normalizado(raiz / _DMN),
        "aprovado_por": "Fulana de Tal",
        "papel": "financas",
        "aprovado_em": "2026-10-18",
        "evidencia": "DL-0000",
    }
    base.update(over)
    return base


def _registro(raiz: Path, **over: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "aprovados": [_aprovado(raiz, **over)],
        "pendentes": [{"artefato": _BPMN, "responsavel": "a definir"}],
    }


def test_registro_completo_passa(raiz: Path) -> None:
    assert verificar(_registro(raiz), raiz=raiz, hoje=_HOJE) == []


def test_rascunho_fora_do_registro_reprova(raiz: Path) -> None:
    """O RED da pendencia 27: rascunho publicavel que ninguem registrou."""
    dados = {"version": 1, "aprovados": [_aprovado(raiz)], "pendentes": []}
    achados = verificar(dados, raiz=raiz, hoje=_HOJE)
    assert any(_BPMN in a and "FORA do registro" in a and "rascunho" in a for a in achados)


def test_artefato_novo_sem_marca_tambem_tem_de_ser_registrado(raiz: Path) -> None:
    """A regra nao depende do texto livre de status: publicavel sem registro reprova sempre."""
    (raiz / "spec/processes/dmn/nova.dmn").write_text("<definitions/>\n", encoding="utf-8")
    achados = verificar(_registro(raiz), raiz=raiz, hoje=_HOJE)
    assert any("nova.dmn" in a and "FORA do registro" in a for a in achados)


def test_hash_alterado_de_regra_aprovada_reprova(raiz: Path) -> None:
    """O segundo RED: editaram a regra aprovada e ninguem aprovou de novo."""
    dados = _registro(raiz)
    (raiz / _DMN).write_text("<definitions>regra EDITADA</definitions>\n", encoding="utf-8")
    achados = verificar(dados, raiz=raiz, hoje=_HOJE)
    assert any(_DMN in a and "NOVA aprovacao" in a for a in achados)


def test_crlf_nao_muda_o_hash(raiz: Path) -> None:
    """Checkout Windows (`core.autocrlf=true`) e CI Linux tem de concordar."""
    dados = _registro(raiz)
    (raiz / _DMN).write_bytes((raiz / _DMN).read_bytes().replace(b"\n", b"\r\n"))
    assert verificar(dados, raiz=raiz, hoje=_HOJE) == []


@pytest.mark.parametrize(
    ("campo", "valor", "trecho"),
    [
        ("aprovado_por", "", "sem `aprovado_por`"),
        ("evidencia", None, "sem `evidencia`"),
        ("papel", "estagiario", "papel 'estagiario' desconhecido"),
        ("aprovado_em", "ontem", "nao e' data ISO"),
        ("aprovado_em", "2026-12-01", "no futuro"),
        ("sha256", "abc", "malformado"),
    ],
)
def test_aprovacao_incompleta_reprova(raiz: Path, campo: str, valor: Any, trecho: str) -> None:
    achados = verificar(_registro(raiz, **{campo: valor}), raiz=raiz, hoje=_HOJE)
    assert any(trecho in a for a in achados), achados


def test_entrada_orfa_e_duplicada_reprovam(raiz: Path) -> None:
    dados = _registro(raiz)
    dados["pendentes"].append({"artefato": "spec/processes/dmn/apagada.dmn", "responsavel": "x"})
    dados["pendentes"].append({"artefato": _DMN, "responsavel": "x"})
    achados = verificar(dados, raiz=raiz, hoje=_HOJE)
    assert any("apagada.dmn" in a and "nao e' artefato publicavel" in a for a in achados)
    assert any(_DMN in a and "duas vezes" in a for a in achados)


def test_pendente_sem_responsavel_reprova(raiz: Path) -> None:
    dados = _registro(raiz)
    dados["pendentes"] = [{"artefato": _BPMN, "responsavel": ""}]
    assert any("sem `responsavel`" in a for a in verificar(dados, raiz=raiz, hoje=_HOJE))


def test_publicaveis_sao_exatamente_o_que_o_deploy_publica() -> None:
    """Paridade com `engine_deploy.collect_artifacts`: se o deploy passar a publicar outra coisa,
    a cerca tem de enxergar o mesmo conjunto — senao ela vigia um conjunto que nao e' o publicado."""
    from maezo.platform.deploy.engine_deploy import collect_artifacts

    processes = _REPO_ROOT / "spec" / "processes"
    do_deploy = [p.relative_to(_REPO_ROOT).as_posix() for p in collect_artifacts(processes)]
    assert artefatos_publicaveis(processes) == do_deploy


def test_o_registro_real_do_repo_passa_e_nao_e_vazio(capsys: pytest.CaptureFixture[str]) -> None:
    """Nao-vacuidade: o registro de verdade cobre todo publicavel, e a saida mostra os pendentes."""
    dados = carregar()
    assert dados["pendentes"], "registro sem pendentes — ou alguem aprovou tudo, ou apagou a lista"
    assert main([]) == 0
    saida = capsys.readouterr().out
    assert "PUBLICADO(S) SEM APROVACAO" in saida
    assert "lucas_billing_admissibility.dmn" in saida
