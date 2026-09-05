"""Cerca: a role `plan` do modulo `github-oidc` nao pode voltar a `ReadOnlyAccess`.

GAP D6-02 / decisao do dono R-041 (opcao A). Ate esta mudanca,
`deploy/terraform/modules/github-oidc/main.tf` anexava a policy GERENCIADA
`arn:aws:iam::aws:policy/ReadOnlyAccess` a role assumida por qualquer workflow de PULL
REQUEST (`token.actions.githubusercontent.com:sub` = `...:pull_request`). Isso dava
leitura da conta INTEIRA a um gatilho que qualquer pessoa que abre um PR dispara, numa
organizacao onde a protecao por revisor de CODEOWNERS ja' foi provada inerte.

POR QUE O TESTE OLHA A FONTE E NAO UM PLAN. `terraform validate` aceitaria o attachment
de volta sem uma palavra (e' sintaticamente perfeito) e `terraform plan` exigiria
credencial AWS, que este repositorio nao tem — o gap D13-03 registra que o IaC nunca
rodou. A unica cerca possivel hoje e' sobre o texto do modulo, e ela e' suficiente:
o defeito era exatamente uma linha de texto.

O QUE CADA TESTE PROVA (reverta a correcao e o nome dito aqui fica VERMELHO):
  * `test_nenhuma_policy_gerenciada_da_aws_e_anexada_a_role_plan` — o attachment antigo.
  * `test_policy_do_plan_so_concede_leitura_nos_servicos_decididos` — o escopo aprovado.
  * `test_policy_do_plan_nao_le_segredo` — o `floor_note` da propria decisao.
"""

from __future__ import annotations

import re
from typing import Final

import pytest

from tests.unit.deploy._hcl_probe import (
    attr_strings,
    block_body,
    read_tf,
    strip_comments,
    sub_blocks,
)

_MODULE: Final[str] = "modules/github-oidc/main.tf"

#: Servicos que a decisao R-041 aprovou para a role `plan`, derivados dos 6 modulos de
#: `deploy/terraform/modules/` e das raizes de `deploy/terraform/envs/`.
_SERVICOS_PERMITIDOS: Final[frozenset[str]] = frozenset({"rds", "ecr", "eks", "aps", "iam", "ec2"})

#: `Describe*`/`Get*`/`List*` — os unicos verbos que um `terraform plan` precisa.
_VERBOS_DE_LEITURA: Final[tuple[str, ...]] = ("Describe", "Get", "List")


@pytest.fixture(scope="module")
def fonte() -> str:
    return read_tf(_MODULE)


@pytest.fixture(scope="module")
def codigo(fonte: str) -> str:
    """`fonte` sem comentarios: a cerca e' sobre o que a AWS recebe, nao sobre a prosa.

    O proprio bloco que documenta a decisao R-041 cita `ReadOnlyAccess` pelo nome para
    explicar o que foi removido; uma cerca que olhasse o texto cru se auto-reprovaria.
    """
    return strip_comments(fonte)


@pytest.fixture(scope="module")
def acoes_do_plan(fonte: str) -> list[str]:
    corpo = block_body(fonte, r'data\s+"aws_iam_policy_document"\s+"plan_policy"\s*')
    statements = sub_blocks(corpo, "statement")
    assert statements, "`plan_policy` sem nenhum `statement`"
    acoes: list[str] = []
    for statement in statements:
        assert attr_strings(statement, "effect") == ["Allow"], "statement de `plan_policy` sem effect Allow"
        acoes.extend(attr_strings(statement, "actions"))
    assert acoes, "`plan_policy` nao concede nenhuma acao"
    return acoes


def test_nenhuma_policy_gerenciada_da_aws_e_anexada_a_role_plan(codigo: str) -> None:
    """A role `plan` nao pode receber `ReadOnlyAccess` nem qualquer outra policy da AWS."""
    assert "ReadOnlyAccess" not in codigo, (
        "`ReadOnlyAccess` voltou a `deploy/terraform/modules/github-oidc/main.tf`: "
        "e' a policy GERENCIADA que a decisao R-041 removeu, e a AWS a EXPANDE sem nossa revisao."
    )
    assert "plan_readonly" not in codigo, "o attachment `plan_readonly` voltou ao modulo"
    assert "arn:aws:iam::aws:policy/" not in codigo, (
        "alguma policy gerenciada da AWS foi anexada em `github-oidc`: o modulo so' pode usar "
        "policies inline definidas neste repositorio."
    )


def test_a_role_plan_recebe_a_policy_inline_deste_repositorio(fonte: str) -> None:
    corpo = block_body(fonte, r'resource\s+"aws_iam_role_policy_attachment"\s+"plan_least_privilege"\s*')
    assert "aws_iam_role.plan.name" in corpo, "o attachment nao aponta para a role `plan`"
    assert "aws_iam_policy.plan_least_privilege.arn" in corpo, (
        "o attachment da role `plan` nao aponta para `aws_iam_policy.plan_least_privilege`"
    )
    politica = block_body(fonte, r'resource\s+"aws_iam_policy"\s+"plan_least_privilege"\s*')
    assert "data.aws_iam_policy_document.plan_policy.json" in politica, (
        "`aws_iam_policy.plan_least_privilege` nao renderiza `plan_policy`"
    )


def test_policy_do_plan_so_concede_leitura_nos_servicos_decididos(acoes_do_plan: list[str]) -> None:
    """Toda acao e' `<servico>:<Describe|Get|List>...` num dos servicos aprovados."""
    for acao in acoes_do_plan:
        assert acao != "*", "acao coringa `*` na policy da role `plan`"
        assert not acao.endswith(":*"), f"acao coringa de servico inteiro: {acao!r}"
        assert re.fullmatch(r"[a-z0-9-]+:[A-Za-z0-9*]+", acao), f"acao malformada: {acao!r}"
        servico, verbo = acao.split(":", 1)
        assert servico in _SERVICOS_PERMITIDOS, (
            f"servico {servico!r} fora do escopo aprovado em R-041 {sorted(_SERVICOS_PERMITIDOS)}"
        )
        assert verbo.startswith(_VERBOS_DE_LEITURA), (
            f"acao {acao!r} nao e' de leitura: a role `plan` so' pode Describe*/Get*/List*"
        )


def test_todos_os_servicos_decididos_estao_cobertos(acoes_do_plan: list[str]) -> None:
    """Nenhum dos 6 servicos pode sumir por acidente — o plan quebraria em CI, nao aqui."""
    cobertos = {acao.split(":", 1)[0] for acao in acoes_do_plan}
    assert cobertos == _SERVICOS_PERMITIDOS, (
        f"servicos cobertos {sorted(cobertos)} != escopo aprovado {sorted(_SERVICOS_PERMITIDOS)}"
    )


def test_policy_do_plan_nao_le_segredo(acoes_do_plan: list[str]) -> None:
    """`floor_note` de R-041: nenhuma leitura de segredo entra na policy da role `plan`."""
    for acao in acoes_do_plan:
        servico = acao.split(":", 1)[0]
        assert servico != "secretsmanager", f"acao de Secrets Manager na role `plan`: {acao!r}"
        assert servico != "kms", f"acao de KMS na role `plan`: {acao!r}"


def test_a_role_deploy_permanece_intacta(fonte: str) -> None:
    """A decisao R-041 fala SO' da role `plan`; a role de apply nao pode ter mudado."""
    corpo = block_body(fonte, r'data\s+"aws_iam_policy_document"\s+"deploy_policy"\s*')
    sids = {sid for statement in sub_blocks(corpo, "statement") for sid in attr_strings(statement, "sid")}
    assert sids == {"ECRAuthToken", "ECRPush", "EKSDescribe", "SecretsRead"}, (
        f"os statements da role `deploy` mudaram: {sorted(sids)}"
    )
