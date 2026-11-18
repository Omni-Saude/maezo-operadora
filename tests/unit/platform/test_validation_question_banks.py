"""E-SF-25 (EP-REG-12a, TB-E-11-a): banco de questionario da declaracao de saude, esqueleto e lint.

Testes nomeados pelo plano: `test_declaration_bank_concept_allowlist`,
`test_bank_requires_resolvable_approvals`, `test_guidance_has_art10_sections` e
`test_ds_declaration_mode_rejects_out_of_scope_concept`. Todo banco aqui e' SINTETICO; nenhum conteudo
clinico.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.validation import question_banks as qb
from maezo.platform.validation.cli import validate_artifacts

REPO = Path(__file__).resolve().parents[3]
BANCO_PUBLICADO = REPO / "spec" / "health" / "question-banks" / "ds-sintetico-v0.yaml"


def _banco() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(BANCO_PUBLICADO.read_text(encoding="utf-8")))


def _real(**kw: Any) -> dict[str, Any]:
    banco = _banco()
    banco.update(
        {
            "synthetic": False,
            "clinicalApprovalRef": "APR-CLIN-0001",
            "regulatoryApprovalRef": "APR-REG-0001",
            **kw,
        }
    )
    for secao in banco["sections"]:
        for pergunta in secao["questions"]:
            pergunta["prompt"] = pergunta["prompt"].replace("[SINTETICO] ", "")
    return banco


def _codigos(banco: object, **kw: Any) -> set[str]:
    return {p.split(":")[0] for p in qb.problemas_do_banco(banco, **kw)}


def _primeira(banco: dict[str, Any]) -> dict[str, Any]:
    return banco["sections"][0]["questions"][0]


# --- o banco publicado ----------------------------------------------------------------------


def test_banco_publicado_passa_no_lint_e_e_sintetico() -> None:
    banco = _banco()
    assert qb.problemas_do_banco(banco) == []
    assert banco["synthetic"] is True and banco["mode"] == "declaration"


def test_banco_sintetico_nunca_carrega_para_uso() -> None:
    with pytest.raises(qb.BancoRecusadoError) as exc:
        qb.carregar_para_uso(_banco(), aprovacoes={"SINTETICO-aprovacao-clinica"})
    assert "DS_SYNTHETIC_NOT_LOADABLE" in exc.value.problemas


def test_registro_de_aprovacoes_publicado_e_vazio() -> None:
    assert not qb.APROVACOES_PUBLICADAS
    with pytest.raises(qb.BancoRecusadoError):
        qb.carregar_para_uso(_real())


# --- regras nomeadas pelo plano -------------------------------------------------------------


@pytest.mark.parametrize("conceito", sorted(qb.CONCEITOS - {"known_condition"}))
def test_declaration_bank_concept_allowlist(conceito: str) -> None:
    banco = _banco()
    _primeira(banco)["concept"] = conceito
    assert "DS_CONCEPT_OUT_OF_SCOPE" in _codigos(banco)


def test_ds_declaration_mode_rejects_out_of_scope_concept() -> None:
    # Violacao injetada do plano: pergunta de habito de vida em banco de declaracao.
    banco = _banco()
    banco["sections"][0]["questions"].append(
        {
            "ref": "q-habito",
            "prompt": "[SINTETICO] Pergunta sobre habito diario",
            "help": "Ajuda sintetica",
            "kind": "text",
            "concept": "daily_life",
            "options": [],
            "required": False,
            "dependsOn": None,
        }
    )
    assert qb.problemas_do_banco(banco) == ["DS_CONCEPT_OUT_OF_SCOPE:q-habito"]


def test_modo_de_cuidado_aceita_os_demais_conceitos() -> None:
    banco = _banco()
    banco["mode"] = "care"
    _primeira(banco)["concept"] = "care_priority"
    assert qb.problemas_do_banco(banco) == []


@pytest.mark.parametrize(
    ("campo", "valor", "codigo"),
    [
        ("clinicalApprovalRef", "APR-CLIN-9999", "DS_APPROVAL_UNRESOLVED"),
        ("regulatoryApprovalRef", "APR-REG-9999", "DS_APPROVAL_UNRESOLVED"),
        ("regulatoryApprovalRef", "SINTETICO-aprovacao-regulatoria", "DS_APPROVAL_SYNTHETIC"),
        ("clinicalApprovalRef", "", "DS_APPROVAL_UNRESOLVED"),
        ("regulatoryApprovalRef", None, "DS_APPROVAL_UNRESOLVED"),
    ],
)
def test_bank_requires_resolvable_approvals(campo: str, valor: object, codigo: str) -> None:
    aprovacoes = {"APR-CLIN-0001", "APR-REG-0001"}
    assert qb.problemas_do_banco(_real(), aprovacoes=aprovacoes) == []
    assert qb.carregar_para_uso(_real(), aprovacoes=aprovacoes)["ref"] == "ds-sintetico"
    banco = _real(**{campo: valor})
    assert codigo in _codigos(banco, aprovacoes=aprovacoes)
    with pytest.raises(qb.BancoRecusadoError):
        qb.carregar_para_uso(banco, aprovacoes=aprovacoes)


@pytest.mark.parametrize("secao", qb.SECOES_OBRIGATORIAS)
def test_guidance_has_art10_sections(secao: str) -> None:
    banco = _banco()
    banco["guidanceText"] = banco["guidanceText"].replace(secao, "## Outra secao")
    assert "DS_GUIDANCE_SECTION_MISSING" in _codigos(banco)


@pytest.mark.parametrize("onde", ["guidanceText", "prompt", "help"])
def test_agravo_nao_e_construido(onde: str) -> None:
    banco = _banco()
    if onde == "guidanceText":
        banco["guidanceText"] += "## Agravo\nSecao sintetica.\n"
    else:
        _primeira(banco)[onde] += " com agravo"
    assert "DS_AGRAVO_NOT_BUILT" in _codigos(banco)


@pytest.mark.parametrize(
    "mutacao",
    [
        lambda b: b["sections"][0]["questions"][0].update(prompt="Pergunta sem marca"),
        lambda b: b.update(clinicalApprovalRef="APR-CLIN-0001"),
    ],
    ids=["pergunta-sem-marca", "aprovacao-sem-prefixo"],
)
def test_banco_sintetico_tem_de_ser_marcado(mutacao: Any) -> None:
    banco = _banco()
    mutacao(banco)
    assert "DS_SYNTHETIC_UNMARKED" in _codigos(banco)


# --- forma fechada --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutacao",
    [
        lambda b: b.update(extra="x"),
        lambda b: b.update(mode="triagem"),
        lambda b: b.update(schema="outro"),
        lambda b: b.update(sections=[]),
        lambda b: b["sections"][0]["questions"][0].update(kind="scale"),
        lambda b: b["sections"][0]["questions"][0].update(concept="cid"),
        lambda b: b["sections"][0]["questions"][0].update(options=[{"value": "sim", "label": "Sim"}]),
        lambda b: b["sections"][0]["questions"][1].update(options=[{"value": "a", "label": "A"}]),
        lambda b: b["sections"][0]["questions"][1].update(dependsOn={"questionRef": "q-9", "value": "sim"}),
        lambda b: b["sections"][0]["questions"][1].update(
            dependsOn={"questionRef": "q-1", "value": "talvez"}
        ),
        lambda b: b["sections"][0]["questions"][1].update(ref="q-1"),
        lambda b: b["sections"][0]["questions"][0].update(required="sim"),
        lambda b: b["sections"][0]["questions"][0].update(observacao="livre"),
        lambda b: b["sections"][0]["questions"][0].update(prompt="[SINTETICO] \x00"),
    ],
    ids=[
        "chave-extra",
        "modo-fora",
        "schema",
        "sem-secao",
        "tipo-fora",
        "conceito-fora",
        "escolha-com-uma-opcao",
        "texto-com-opcao",
        "dependencia-inexistente",
        "dependencia-opcao-inexistente",
        "ref-duplicada",
        "required-nao-bool",
        "chave-extra-na-pergunta",
        "caractere-de-controle",
    ],
)
def test_forma_fechada(mutacao: Any) -> None:
    banco = _banco()
    mutacao(banco)
    assert "DS_SHAPE_INVALID" in _codigos(banco)


@pytest.mark.parametrize("bruto", [None, [], "texto", 1])
def test_banco_ilegivel_recusa(bruto: object) -> None:
    assert _codigos(bruto) == {"DS_SHAPE_INVALID"}


# --- ligacao no validate-artifacts ----------------------------------------------------------


def test_validate_artifacts_cobre_spec_health() -> None:
    assert validate_artifacts([str(REPO / "spec" / "health")]) == 0
    assert "spec/health" in (REPO / "Makefile").read_text(encoding="utf-8").split("validate-artifacts:")[1]


def test_validate_artifacts_recusa_banco_invalido(tmp_path: Path) -> None:
    pasta = tmp_path / "question-banks"
    pasta.mkdir()
    banco = _banco()
    _primeira(banco)["concept"] = "treatment"
    (pasta / "ruim.yaml").write_text(yaml.safe_dump(banco), encoding="utf-8")
    assert validate_artifacts([str(tmp_path)]) == 1
    vazio = tmp_path / "vazio"
    (vazio / "question-banks").mkdir(parents=True)
    assert validate_artifacts([str(vazio)]) == 1
