"""DL-0101 — regras neonatais (r9-r13) da `triage_redflag_pediatric.dmn` e a ratificacao do dono.

A tabela e' lida do ARTEFATO, pelo mesmo leitor FIRST-hit dos manifestos de candidato.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
import yaml

from maezo.agents.helena.prompts import SINTOMA_CODIGOS_BY_POPULATION
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_TABELA = DMN_DIR / "triage_redflag_pediatric.dmn"
_RATIFICACAO = DMN_DIR / "triage-redflag-pediatric-neonatal-ratification.yaml"

_CODIGOS_NEONATAIS = (
    "choro_inconsolavel",
    "recusa_alimentar",
    "hipotonia",
    "cianose",
    "vomitos_persistentes",
)


def _entrada(codigo: str, idade_meses: int | None, intensidade: str = "desconhecida") -> dict[str, Any]:
    return {"sintoma_codigo": codigo, "intensidade": intensidade, "idade_meses": idade_meses}


@pytest.mark.parametrize("codigo", _CODIGOS_NEONATAIS)
@pytest.mark.parametrize("intensidade", ["leve", "moderada", "grave", "desconhecida"])
def test_recem_nascido_com_sinal_de_sofrimento_e_emergencia_em_qualquer_intensidade(
    codigo: str, intensidade: str
) -> None:
    veredito = evaluate(read_live_table(_TABELA), _entrada(codigo, 0, intensidade))

    assert veredito.regra in {"r9", "r10", "r11", "r12", "r13"}
    assert veredito.saidas["red_flag"] is True
    assert veredito.saidas["prioridade"] == "P1"
    assert veredito.saidas["conduta"] == "ESCALATE_EMERGENCY"
    assert "28 dias" in veredito.saidas["motivo"]


@pytest.mark.parametrize("codigo", _CODIGOS_NEONATAIS)
@pytest.mark.parametrize("idade", [1, 2, 6, 11])
def test_a_regra_neonatal_nao_vale_para_lactente_com_um_mes_ou_mais(codigo: str, idade: int) -> None:
    veredito = evaluate(read_live_table(_TABELA), _entrada(codigo, idade))

    assert veredito.regra == "r8"
    assert veredito.saidas["red_flag"] is False


@pytest.mark.parametrize(("codigo", "regra"), [("febre", "r1"), ("dificuldade_respiratoria", "r2")])
def test_febre_e_respiracao_do_recem_nascido_ja_eram_cobertas(codigo: str, regra: str) -> None:
    veredito = evaluate(read_live_table(_TABELA), _entrada(codigo, 0))

    assert veredito.regra == regra
    assert veredito.saidas["conduta"] == "ESCALATE_EMERGENCY"


def test_idade_desconhecida_nao_dispara_a_regra_neonatal() -> None:
    """Sem a idade a regra nao casa: quem preenche `idade_meses=0` do recem-nascido dito em palavras e' o
    grafo, e o fail-safe `_alerta_neonatal` cobre o resto."""
    veredito = evaluate(read_live_table(_TABELA), _entrada("choro_inconsolavel", None))

    assert veredito.regra == "r8"


def test_os_codigos_neonatais_estao_na_lista_pediatrica_do_prompt() -> None:
    assert set(_CODIGOS_NEONATAIS) <= set(SINTOMA_CODIGOS_BY_POPULATION["pediatric"])


# --- a ratificacao do dono ------------------------------------------------------------------------


def _ratificacao() -> dict[str, Any]:
    dados = yaml.safe_load(_RATIFICACAO.read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def test_a_ratificacao_do_dono_esta_registrada_com_todos_os_campos() -> None:
    dados = _ratificacao()

    assert dados["ratificado"] is True
    assert dados["revisor"] == "Márcio Filipe Filó do Carmo"
    assert dados["capacidade"] == "Dono da organização, CTO e DPO"
    assert dados["ratificado_em"] == "2026-10-14"
    base = str(dados["attestation_basis"])
    assert "não médica" in base and "médico-auditor" in base and "192" in base
    assert "PLACEHOLDER" not in str(dados).upper()


def test_a_ratificacao_esta_amarrada_aos_bytes_da_tabela_viva() -> None:
    """Editar a tabela sem refazer a ratificacao quebra o build (runbook dmn-bpmn-deployment §4)."""
    dados = _ratificacao()

    assert dados["tabela_viva"]["path"] == "spec/processes/dmn/triage_redflag_pediatric.dmn"
    assert dados["tabela_viva"]["sha256"] == hashlib.sha256(_TABELA.read_bytes()).hexdigest()


def test_as_regras_ratificadas_sao_exatamente_as_neonatais_da_tabela() -> None:
    tabela = read_live_table(_TABELA)
    neonatais = [
        r.rule_id for r in tabela.rules if r.inputs[tabela.input_names.index("idade_meses")] == "< 1"
    ]

    assert neonatais == _ratificacao()["regras_ratificadas"]


def test_o_registro_de_ratificacao_nao_e_artefato_implantavel_e_a_tabela_e() -> None:
    """`deploy-processes` publica a tabela (`triage_redflag_pediatric.dmn`); o registro `.yaml` nunca vai."""
    from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir

    artefatos = {path.resolve() for path in collect_artifacts(resolve_spec_processes_dir())}

    assert _TABELA.resolve() in artefatos
    assert _RATIFICACAO.resolve() not in artefatos
