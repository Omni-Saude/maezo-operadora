"""Cerca: o teto de custo nao pode ganhar `default`, e o alarme nao pode virar opcional.

GAP B-02-b / decisao do dono R-045. Ate esta mudanca,
`grep -rn aws_budgets_budget deploy/` e `grep -rn cost_anomaly deploy/` retornavam ZERO
linhas: no dia em que `AWS_ENABLED` fosse ligado, o gasto comecaria sem teto e sem alarme.

O MECANISMO QUE ESTES TESTES PROTEGEM E' A AUSENCIA DE UM DEFAULT. O valor mensal e'
insumo de FINANCAS e a decisao o diferiu de proposito: `variable "monthly_budget_amount"`
sem `default` faz `terraform plan` falhar FECHADO ate o dono informar o teto. Um agente
futuro que "conserte" o plan vermelho acrescentando `default = 0` reabre o gap inteiro sem
que nenhum alarme toque — e' exatamente isso que
`test_o_teto_mensal_nao_tem_default` transforma num teste vermelho.

A outra metade e' o contrario: monitor e assinatura de anomalia sao INCONDICIONAIS (sem
`count`, sem `for_each`, sem `var.enabled`), porque instalar o detector e' trivial
enquanto nao ha' gasto e vira urgencia no dia do flip.
"""

from __future__ import annotations

from typing import Final

import pytest

from tests.unit.deploy._hcl_probe import (
    TERRAFORM_ROOT,
    attr_raw,
    attr_strings,
    block_body,
    direct_attrs,
    has_attr,
    has_block,
    read_tf,
    sub_blocks,
)

_MODULO: Final[str] = "modules/cost-guardrails"
_MAIN: Final[str] = f"{_MODULO}/main.tf"
_VARIAVEIS: Final[str] = f"{_MODULO}/variables.tf"

#: Raizes de ambiente que precisam instanciar o modulo. Derivadas do disco para que um
#: ambiente novo entre na cerca sozinho, em vez de escapar por omissao.
_ENVS: Final[tuple[str, ...]] = tuple(
    sorted(p.name for p in (TERRAFORM_ROOT / "envs").iterdir() if (p / "main.tf").is_file())
)

#: Variaveis cuja AUSENCIA DE DEFAULT e' o guardrail, e nao um esquecimento.
_SEM_DEFAULT_POR_DECISAO: Final[tuple[str, ...]] = ("monthly_budget_amount", "notification_email")


def test_ha_pelo_menos_uma_raiz_de_ambiente() -> None:
    """Se `envs/` ficasse vazio, as cercas por ambiente passariam em branco."""
    assert _ENVS, "nenhuma raiz de ambiente encontrada em deploy/terraform/envs/"


@pytest.mark.parametrize("arquivo", ["main.tf", "variables.tf", "outputs.tf", "versions.tf"])
def test_o_modulo_esta_completo(arquivo: str) -> None:
    assert (TERRAFORM_ROOT / _MODULO / arquivo).is_file(), f"{_MODULO}/{arquivo} ausente"


@pytest.mark.parametrize("variavel", _SEM_DEFAULT_POR_DECISAO)
def test_o_teto_mensal_nao_tem_default(variavel: str) -> None:
    """R-045: sem valor de financas, `terraform plan` tem de falhar fechado."""
    corpo = direct_attrs(block_body(read_tf(_VARIAVEIS), rf'variable\s+"{variavel}"\s*'))
    assert not has_attr(corpo, "default"), (
        f'`variable "{variavel}"` ganhou um `default` em {_VARIAVEIS}. A ausencia de default E\' o '
        "guardrail da decisao R-045: com um default, o `terraform plan` passa e o gasto volta a nao "
        "ter teto informado por quem responde por ele."
    )


@pytest.mark.parametrize("recurso", ["aws_ce_anomaly_monitor", "aws_ce_anomaly_subscription"])
def test_a_deteccao_de_anomalia_e_incondicional(recurso: str) -> None:
    fonte = read_tf(_MAIN)
    assert has_block(fonte, rf'resource\s+"{recurso}"\s+"'), f"{recurso} ausente em {_MAIN}"
    corpo = direct_attrs(block_body(fonte, rf'resource\s+"{recurso}"\s+"[a-z_]+"\s*'))
    assert not has_attr(corpo, "count"), f"{recurso} ganhou `count`: a decisao R-045 o quer incondicional"
    assert not has_attr(corpo, "for_each"), f"{recurso} ganhou `for_each`: R-045 o quer incondicional"


def test_a_assinatura_de_anomalia_avisa_alguem() -> None:
    """Um detector sem destinatario e' um guardrail silenciosamente inerte."""
    corpo = block_body(read_tf(_MAIN), r'resource\s+"aws_ce_anomaly_subscription"\s+"alerts"\s*')
    assinantes = sub_blocks(corpo, "subscriber")
    assert assinantes, "`aws_ce_anomaly_subscription` sem bloco `subscriber`"
    for assinante in assinantes:
        assert attr_strings(assinante, "type") == ["EMAIL"]
        assert "var.notification_email" in attr_raw(assinante, "address"), (
            "o endereco do alerta nao vem de `var.notification_email`"
        )
    assert "aws_ce_anomaly_monitor.service.arn" in attr_raw(corpo, "monitor_arn_list"), (
        "a assinatura nao esta ligada ao monitor de anomalia deste modulo"
    )


def test_o_orcamento_tira_o_teto_da_variable_sem_default() -> None:
    corpo = block_body(read_tf(_MAIN), r'resource\s+"aws_budgets_budget"\s+"monthly"\s*')
    diretos = direct_attrs(corpo)
    assert not has_attr(diretos, "count"), "`aws_budgets_budget` ganhou `count`"
    assert not has_attr(diretos, "for_each"), "`aws_budgets_budget` ganhou `for_each`"
    assert "var.monthly_budget_amount" in attr_raw(corpo, "limit_amount"), (
        "`limit_amount` deixou de vir de `var.monthly_budget_amount`: o teto voltaria a ser um "
        "numero escrito por um agente, e nao o valor aprovado por financas."
    )
    assert attr_strings(corpo, "budget_type") == ["COST"]
    assert attr_strings(corpo, "time_unit") == ["MONTHLY"]


@pytest.mark.parametrize("env", _ENVS)
def test_toda_raiz_de_ambiente_instancia_os_guardrails(env: str) -> None:
    corpo = block_body(read_tf(f"envs/{env}/main.tf"), r'module\s+"cost_guardrails"\s*')
    diretos = direct_attrs(corpo)
    assert not has_attr(diretos, "count"), f"envs/{env} tornou os guardrails condicionais via `count`"
    assert not has_attr(diretos, "for_each"), f"envs/{env} tornou os guardrails condicionais via `for_each`"
    assert f"../../{_MODULO}" in attr_raw(corpo, "source")
    assert "var.monthly_budget_amount" in attr_raw(corpo, "monthly_budget_amount")


@pytest.mark.parametrize("env", _ENVS)
def test_a_falha_fechada_chega_ate_a_raiz_do_ambiente(env: str) -> None:
    """De nada adianta o modulo exigir o teto se a raiz inventar um default para ele."""
    fonte = read_tf(f"envs/{env}/variables.tf")
    for variavel in ("monthly_budget_amount", "cost_alert_email"):
        corpo = direct_attrs(block_body(fonte, rf'variable\s+"{variavel}"\s*'))
        assert not has_attr(corpo, "default"), (
            f"envs/{env}/variables.tf deu `default` a `{variavel}`, anulando a falha fechada de R-045"
        )
