"""Os quatro checks de CI da chancela de regras (onda 1; ADR-0080/DL-0194 PROPOSTOS).

Fixtures sintéticas em `tmp_path` provam que cada check consegue ficar VERMELHO; os testes da árvore
real provam que o catálogo REEMBOLSO versionado passa e não é vácuo (8 regras, nenhuma aprovada).
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_catalogo_regras as cat_check
from scripts.ci import check_controle_de_alteracoes as ctl_check
from scripts.ci import check_regras_cobertura as cob_check
from scripts.ci import check_regras_so_acrescimo as acr

from maezo.regras.catalogo.repositorio import ARQUIVO_CONTROLE, carregar_catalogos
from maezo.regras.controle import gerar
from tests.unit.regras.fabrica import ARQ_CATALOGO, catalogo_base, com_hash, criar_repo, gravar, regra_base

_RAIZ = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return criar_repo(tmp_path)


# --- check_catalogo_regras -------------------------------------------------------------------------


def test_catalogo_com_hash_certo_passa(repo: Path) -> None:
    gravar(repo, com_hash(repo, catalogo_base()))
    catalogos, achados = cat_check.verificar(repo)
    assert achados == [] and len(catalogos) == 1


def test_hash_divergente_reprova_e_imprime_o_valor_certo(repo: Path) -> None:
    certo = com_hash(repo, catalogo_base())
    gravar(repo, certo)
    (repo / "spec/policies/p.yaml").write_bytes(
        b"acoes:\n  reembolso:\n    params:\n      teto: 500\n      limite: 2\n"
    )
    _, achados = cat_check.verificar(repo)
    assert len(achados) == 1 and "!= recalculado" in achados[0]
    assert certo["regras"][0]["sha256"] not in achados[0].split("recalculado")[1]


def test_fonte_irresolvivel_reprova(repo: Path) -> None:
    gravar(repo, com_hash(repo, catalogo_base()))
    (repo / "spec/policies/p.yaml").write_bytes(b"acoes: {}\n")
    _, achados = cat_check.verificar(repo)
    assert any("fonte irresolvivel" in a for a in achados)


def test_id_e_alias_repetidos_entre_catalogos_reprovam(repo: Path) -> None:
    gravar(repo, com_hash(repo, catalogo_base()))
    (repo / "docs/processes/contracts/SP-OP-OUTRO-001.md").write_text("# c\n", encoding="utf-8")
    outro = com_hash(repo, catalogo_base())
    outro["processo"] = "SP-OP-OUTRO-001"
    gravar(repo, outro, "spec/regras/catalogo/SP-OP-OUTRO-001.yaml")
    _, achados = cat_check.verificar(repo)
    assert any("`id` repetido em" in a for a in achados)
    assert any("`alias` repetido em" in a for a in achados)


def test_chave_duplicada_no_yaml_reprova(repo: Path) -> None:
    (repo / ARQ_CATALOGO).write_text("schema: a\nschema: b\nprocesso: x\nregras: []\n", encoding="utf-8")
    _, achados = cat_check.verificar(repo)
    assert any("duplicada" in a for a in achados)


def test_catalogo_real_passa_com_8_regras_fin_sem_aprovacao(capsys: pytest.CaptureFixture[str]) -> None:
    """Não-vacuidade: o piloto FIN-01..FIN-08 existe, confere o hash e NADA foi aprovado por agente."""
    catalogos, achados = cat_check.verificar(_RAIZ)
    assert achados == []
    (reembolso,) = [c for c in catalogos if c.processo == "SP-OP-REEMBOLSO-001"]
    assert [r.alias for r in reembolso.regras] == [f"FIN-0{i}" for i in range(1, 9)]
    for regra in reembolso.regras:
        assert regra.estado_declarado == "legado_pendente"
        assert regra.aprovacoes == () and regra.liberacoes == () and regra.comprovacoes == ()
        assert "financas" in regra.aprovam
        assert "faixa" in regra.o_que_nao_e.lower() or "não aprova" in regra.o_que_nao_e.lower()
    assert cat_check.main([]) == 0
    assert "8 regra(s) SEM aprovacao" in capsys.readouterr().out


def test_p3_default_do_blueprint_nas_quatro_regras_de_teto_e_exposicao() -> None:
    """FIN-02/03/05/07: `[financas, dono]` e' o DEFAULT do blueprint (P3 em aberto), dito no arquivo."""
    catalogos, _ = carregar_catalogos(_RAIZ)
    (reembolso,) = [c for c in catalogos if c.processo == "SP-OP-REEMBOLSO-001"]
    por_alias = {r.alias: r for r in reembolso.regras}
    for alias in ("FIN-02", "FIN-03", "FIN-05", "FIN-07"):
        assert set(por_alias[alias].aprovam) == {"financas", "dono"}
    for alias in ("FIN-01", "FIN-04", "FIN-06", "FIN-08"):
        assert por_alias[alias].aprovam == ("financas",)
    texto = (_RAIZ / "spec/regras/catalogo/SP-OP-REEMBOLSO-001.yaml").read_text(encoding="utf-8")
    assert "P3" in texto and "P2" in texto


# --- check_regras_cobertura / check_controle_de_alteracoes (arvore real) ----------------------------


def test_cobertura_real_passa() -> None:
    assert cob_check.verificar(_RAIZ) == []


def test_controle_real_passa_e_divergente_reprova(repo: Path) -> None:
    assert ctl_check.verificar(_RAIZ) == []
    gravar(repo, com_hash(repo, catalogo_base()))
    assert any("nao existe" in a for a in ctl_check.verificar(repo))
    catalogos, _ = carregar_catalogos(repo)
    (repo / ARQUIVO_CONTROLE).write_text(gerar(catalogos), encoding="utf-8")
    assert ctl_check.verificar(repo) == []
    (repo / ARQUIVO_CONTROLE).write_text(gerar(catalogos) + "# editado a mao\n", encoding="utf-8")
    assert any("diverge do gerado" in a for a in ctl_check.verificar(repo))


# --- check_regras_so_acrescimo: nucleo puro ------------------------------------------------------


_MANUAL = {"evidencia_url": "https://x/1", "evidencia_sha256": "b" * 64, "transcrito_por": "P"}
_APROV = {
    "papel": "financas",
    "aprovador": "Pessoa Ficticia",
    "decisao": "de_acordo",
    "regra_sha256": "a" * 64,
    "data": "2026-11-18",
    "valida_ate": "2027-11-18",
    "recibo": None,
    "transcricao_manual": _MANUAL,
}


def _cats(**over: Any) -> dict[str, Any]:
    return {ARQ_CATALOGO: catalogo_base(regra_base(**over))}


def _comparar(base: dict[str, Any], head: dict[str, Any], **kw: Any) -> list[str]:
    return acr.comparar(
        base,
        head,
        kw.get("base_rec", {}),
        kw.get("head_rec", {}),
        kw.get("base_ctl"),
        kw.get("head_ctl"),
    )


def test_so_acrescimo_igual_e_acrescimo_passam() -> None:
    base = _cats(aprovacoes=[_APROV])
    assert _comparar(base, copy.deepcopy(base)) == []
    head = _cats(aprovacoes=[_APROV, {**_APROV, "papel": "dono"}])
    assert _comparar(base, head) == []


def test_regra_removida_reprova() -> None:
    head = {ARQ_CATALOGO: {**catalogo_base(), "regras": []}}
    assert any("sumiu" in a for a in _comparar(_cats(), head))


def test_id_que_muda_de_processo_reprova() -> None:
    base = _cats()
    head = {"spec/regras/catalogo/SP-OP-OUTRO-001.yaml": catalogo_base()}
    assert any("mudou de" in a for a in _comparar(base, head))


@pytest.mark.parametrize(
    "head_aprov",
    [
        [],
        [{**_APROV, "aprovador": "Outra Pessoa"}],
        [{**_APROV, "papel": "dono"}, _APROV],
    ],
    ids=["removida", "editada", "reordenada"],
)
def test_aprovacao_removida_editada_ou_reordenada_reprova(head_aprov: list[Any]) -> None:
    base = _cats(aprovacoes=[_APROV])
    achados = _comparar(base, _cats(aprovacoes=head_aprov))
    assert any("`aprovacoes`" in a for a in achados), achados


@pytest.mark.parametrize("lista", ["liberacoes", "comprovacoes", "historico_de_mudanca"])
def test_demais_listas_tambem_so_crescem(lista: str) -> None:
    base = _cats(**{lista: [{"x": 1}]})
    assert any(f"`{lista}`" in a for a in _comparar(base, _cats(**{lista: []})))


def test_hash_mudou_sem_historico_reprova_e_com_historico_passa() -> None:
    base = _cats()
    assert any("sem linha nova" in a for a in _comparar(base, _cats(sha256="1" * 64)))
    hist = [{"pr": 9, "o_que_mudou": "Teto.", "motivo": "Decisao."}]
    assert _comparar(base, _cats(sha256="1" * 64, historico_de_mudanca=hist)) == []


@pytest.mark.parametrize(
    ("antes", "depois", "ok"),
    [
        ("legado_pendente", "ativa", True),
        ("ativa", "em_remocao", True),
        ("em_remocao", "removida", True),
        ("em_remocao", "ativa", True),
        ("ativa", "legado_pendente", False),
        ("legado_pendente", "removida", False),
        ("removida", "ativa", False),
    ],
)
def test_transicoes_de_estado(antes: str, depois: str, ok: bool) -> None:
    achados = _comparar(_cats(estado_declarado=antes), _cats(estado_declarado=depois))
    assert (achados == []) is ok, achados


def test_regra_removida_e_congelada() -> None:
    base = _cats(estado_declarado="removida")
    assert any(
        "estado final" in a for a in _comparar(base, _cats(estado_declarado="removida", titulo="Outro"))
    )


def test_recibo_removido_ou_alterado_reprova() -> None:
    rec = {"spec/regras/recibos/SP-OP-TESTE-001/rcb_1.json": b"{}"}
    assert any("recibo removido" in a for a in _comparar(_cats(), _cats(), base_rec=rec, head_rec={}))
    alterado = {k: b'{"x":1}' for k in rec}
    assert any("recibo alterado" in a for a in _comparar(_cats(), _cats(), base_rec=rec, head_rec=alterado))


def test_linha_do_controle_que_some_reprova() -> None:
    linha = {"evento": "aprovacao", "regra_id": "TESTE.FAIXA.TETO", "seq": 1}
    base_ctl = {"schema": "s", "linhas": [linha]}
    assert any(
        "o controle so' cresce" in a
        for a in _comparar(_cats(), _cats(), base_ctl=base_ctl, head_ctl={"linhas": []})
    )
    assert (
        _comparar(_cats(), _cats(), base_ctl=base_ctl, head_ctl={"linhas": [linha, {**linha, "seq": 2}]})
        == []
    )


def test_data_do_yaml_e_texto_iso_comparam_iguais() -> None:
    from datetime import date

    base = _cats(aprovacoes=[{**_APROV, "data": date(2026, 11, 18)}])
    assert _comparar(base, _cats(aprovacoes=[_APROV])) == []


# --- check_regras_so_acrescimo: git de verdade ---------------------------------------------------


def _git(raiz: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=raiz, check=True, capture_output=True)


def test_carregar_base_le_o_commit_e_main_compara(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@exemplo.invalid")
    _git(repo, "config", "user.name", "teste")
    _git(repo, "config", "core.autocrlf", "false")
    gravar(repo, com_hash(repo, catalogo_base(regra_base(aprovacoes=[_APROV]))))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    base_cat, base_rec, base_ctl = acr.carregar_base(repo, commit)
    assert list(base_cat) == [ARQ_CATALOGO] and base_rec == {} and base_ctl is None
    gravar(repo, com_hash(repo, catalogo_base(regra_base(aprovacoes=[]))))
    head_cat, head_rec, head_ctl = acr.carregar_head(repo)
    achados = acr.comparar(base_cat, head_cat, base_rec, head_rec, base_ctl, head_ctl)
    assert any("`aprovacoes`" in a for a in achados)
