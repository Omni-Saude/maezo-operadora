"""A cerca do registro de aprovacoes (DL-0148) — e a prova de que ela consegue ficar vermelha.

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


# --- isencao PROVISORIA de `spec/processes/dmn/rco/` (ADR-0075 secao g; decisao do dono P7) ------

_RCO = "spec/processes/dmn/rco/rco_k1.dmn"


def test_rco_fora_do_registro_e_isento_provisoriamente_mas_o_primeiro_nivel_nao(raiz: Path) -> None:
    """Pacote K1 (`dmn/rco/`) fora do registro NAO reprova (ate' P7); tabela do Maezo fora do
    registro continua reprovando, e subpasta solta de `rco/` nem e' publicavel."""
    (raiz / "spec/processes/dmn/rco/solta").mkdir(parents=True)
    (raiz / _RCO).write_bytes(b"<definitions>k1</definitions>\n")
    (raiz / "spec/processes/dmn/rco/solta/fora.dmn").write_bytes(b"<definitions/>\n")
    (raiz / "spec/processes/dmn/nova_maezo.dmn").write_bytes(b"<definitions>nova</definitions>\n")
    publicaveis = artefatos_publicaveis(raiz / "spec/processes")
    assert _RCO in publicaveis
    assert "spec/processes/dmn/rco/solta/fora.dmn" not in publicaveis
    achados = verificar(_registro(raiz), raiz=raiz, hoje=_HOJE)
    assert [a for a in achados if "FORA do registro" in a] == [
        "spec/processes/dmn/nova_maezo.dmn: publicavel e FORA do registro. O deploy o enviaria ao motor "
        "sem ninguem responder por ele. Registre em `aprovados` (com quem aprovou) ou em `pendentes`."
    ]
    assert not any(_RCO in a for a in achados)


def test_rco_registrado_explicitamente_continua_checado(raiz: Path) -> None:
    """A isencao so' dispensa a LINHA; uma aprovacao explicita de arquivo de `rco/` e' checada."""
    (raiz / "spec/processes/dmn/rco").mkdir(parents=True)
    (raiz / _RCO).write_bytes(b"<definitions>k1</definitions>\n")
    dados = _registro(raiz)
    dados["aprovados"].append({**_aprovado(raiz), "artefato": _RCO, "sha256": "0" * 64})
    assert any(_RCO in a and "os bytes mudaram" in a for a in verificar(dados, raiz=raiz, hoje=_HOJE))


def test_isencao_rco_cita_a_pendencia_p7() -> None:
    from scripts.ci.check_aprovacao_de_regras import MOTIVO_ISENCAO_RCO, isento_provisoriamente

    assert "P7" in MOTIVO_ISENCAO_RCO and "PROVISORIA" in MOTIVO_ISENCAO_RCO
    assert isento_provisoriamente("spec/processes/dmn/rco/rco_a.dmn")
    assert not isento_provisoriamente("spec/processes/dmn/rco/solta/x.dmn")
    assert not isento_provisoriamente("spec/processes/dmn/auth_sla.dmn")


def test_o_registro_real_lista_o_exemplo_rco_como_isento(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    saida = capsys.readouterr().out
    assert "spec/processes/dmn/rco/rco_exemplo_sintetico.dmn" in saida
    assert "P7" in saida


# --- varios papeis por artefato (onda 0 da chancela de regras; DL-0194/ADR-0080 PROPOSTOS) ---------


def _aprov(papel_: str, **over: Any) -> dict[str, Any]:
    base = {
        "papel": papel_,
        "aprovado_por": f"Pessoa {papel_}",
        "aprovado_em": "2026-10-18",
        "evidencia": "PR #1",
    }
    base.update(over)
    return base


def _multi(raiz: Path, aprovam: Any, aprovacoes: Any) -> dict[str, Any]:
    entrada: dict[str, Any] = {"artefato": _DMN, "sha256": sha256_normalizado(raiz / _DMN)}
    if aprovam is not None:
        entrada["aprovam"] = aprovam
    entrada["aprovacoes"] = aprovacoes
    return {"version": 1, "aprovados": [entrada], "pendentes": [{"artefato": _BPMN, "responsavel": "x"}]}


def test_multi_papel_conjunto_exato_passa(raiz: Path) -> None:
    dados = _multi(raiz, ["financas", "dono"], [_aprov("dono"), _aprov("financas")])
    assert verificar(dados, raiz=raiz, hoje=_HOJE) == []


def test_multi_papel_sem_aprovam_passa_com_varios_papeis(raiz: Path) -> None:
    dados = _multi(raiz, None, [_aprov("financas"), _aprov("juridico")])
    assert verificar(dados, raiz=raiz, hoje=_HOJE) == []


def test_aprovacao_parcial_reprova(raiz: Path) -> None:
    """O RED da lacuna 1.1.2: um papel de dois nao e' aprovacao."""
    achados = verificar(_multi(raiz, ["financas", "dono"], [_aprov("financas")]), raiz=raiz, hoje=_HOJE)
    assert any("PARCIAL" in a and "'dono'" in a for a in achados), achados


def test_papel_sobrando_reprova(raiz: Path) -> None:
    dados = _multi(raiz, ["financas"], [_aprov("financas"), _aprov("dpo")])
    achados = verificar(dados, raiz=raiz, hoje=_HOJE)
    assert any("'dpo'" in a and "conjunto e' exato" in a for a in achados), achados


def test_papel_repetido_na_entrada_reprova(raiz: Path) -> None:
    dados = _multi(raiz, ["financas"], [_aprov("financas"), _aprov("financas", aprovado_por="Outra")])
    assert any("mais de uma vez" in a for a in verificar(dados, raiz=raiz, hoje=_HOJE))


@pytest.mark.parametrize(
    ("aprovam", "trecho"),
    [
        ([], "lista nao vazia"),
        ("financas", "lista nao vazia"),
        (["financas", "financas"], "repete"),
        (["financas", "estagiario"], "desconhecido"),
    ],
)
def test_aprovam_invalido_reprova(raiz: Path, aprovam: Any, trecho: str) -> None:
    achados = verificar(_multi(raiz, aprovam, [_aprov("financas")]), raiz=raiz, hoje=_HOJE)
    assert any("`aprovam`" in a and trecho in a for a in achados), achados


@pytest.mark.parametrize(
    ("over", "trecho"),
    [
        ({"aprovado_por": ""}, "(aprovacao #1): aprovacao sem `aprovado_por`"),
        ({"evidencia": None}, "(aprovacao #1): aprovacao sem `evidencia`"),
        ({"papel": "estagiario"}, "papel 'estagiario' desconhecido"),
        ({"aprovado_em": "2026-12-01"}, "no futuro"),
        ({"aprovado_em": "ontem"}, "nao e' data ISO"),
    ],
)
def test_item_de_aprovacoes_incompleto_reprova(raiz: Path, over: dict[str, Any], trecho: str) -> None:
    achados = verificar(_multi(raiz, None, [_aprov("financas", **over)]), raiz=raiz, hoje=_HOJE)
    assert any(trecho in a for a in achados), achados


@pytest.mark.parametrize("aprovacoes", [[], "financas", [["financas"]]])
def test_aprovacoes_malformada_reprova(raiz: Path, aprovacoes: Any) -> None:
    achados = verificar(_multi(raiz, None, aprovacoes), raiz=raiz, hoje=_HOJE)
    assert any("`aprovacoes` tem de ser uma lista nao vazia" in a for a in achados), achados


def test_misturar_forma_antiga_e_nova_reprova(raiz: Path) -> None:
    dados = _multi(raiz, None, [_aprov("financas")])
    dados["aprovados"][0]["aprovado_por"] = "Alguem"
    assert any("mistura a forma antiga" in a for a in verificar(dados, raiz=raiz, hoje=_HOJE))


def test_forma_antiga_com_aprovam_exige_o_mesmo_papel(raiz: Path) -> None:
    """A forma de um papel continua aceita; com `aprovam` de dois papeis ela e' parcial."""
    assert verificar(_registro(raiz, aprovam=["financas"]), raiz=raiz, hoje=_HOJE) == []
    achados = verificar(_registro(raiz, aprovam=["financas", "dono"]), raiz=raiz, hoje=_HOJE)
    assert any("PARCIAL" in a for a in achados), achados


def test_hash_alterado_reprova_tambem_na_forma_multi_papel(raiz: Path) -> None:
    dados = _multi(raiz, ["financas", "dono"], [_aprov("financas"), _aprov("dono")])
    (raiz / _DMN).write_bytes(b"<definitions>regra EDITADA</definitions>\n")
    assert any("NOVA aprovacao" in a for a in verificar(dados, raiz=raiz, hoje=_HOJE))


def test_pendente_pode_declarar_aprovam_e_o_dominio_e_checado(raiz: Path) -> None:
    dados = _registro(raiz)
    dados["pendentes"][0]["aprovam"] = ["financas", "dono"]
    assert verificar(dados, raiz=raiz, hoje=_HOJE) == []
    dados["pendentes"][0]["aprovam"] = ["financas", "atuaria"]
    assert any("desconhecido" in a for a in verificar(dados, raiz=raiz, hoje=_HOJE))
