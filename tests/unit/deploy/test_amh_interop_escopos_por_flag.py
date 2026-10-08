"""Escopos do interop da AMH so' entram com a flag de cada um (revisao do PR #700, P1-a).

O Cognito recusa o token INTEIRO se o app client nao tiver um escopo pedido. Pedir `interop/subject.verify` e
`interop/consent.write` sempre derrubaria identidade, cobranca e TINA em dev enquanto o client nao os tiver.
A cerca: com as flags desligadas, o pedido de token e' IDENTICO ao da main (os tres escopos de sempre), no
Terraform E no default do receptor; os escopos do acesso so' entram com `acesso_beneficiario`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.deploy._hcl_probe import REPO_ROOT

_ENV = REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"
#: O valor da main (antes do #700): o pedido de token com as flags desligadas tem de ser ESTE.
ESCOPOS_DA_MAIN = "interop/billing.read interop/subject.resolve interop/profile.read"


def _expressao_efetiva() -> str:
    fonte = (_ENV / "amh-interop.tf").read_text(encoding="utf-8")
    casamento = re.search(
        r"^  amh_interop_scopes_efetivos = (join\(.*?\n  \)\))$", fonte, re.MULTILINE | re.DOTALL
    )
    assert casamento, "amh_interop_scopes_efetivos nao encontrado na forma join(concat(...))"
    return casamento.group(1)


def test_tfvars_nao_sobrescreve_os_escopos() -> None:
    tfvars = (_ENV / "amh-interop.auto.tfvars").read_text(encoding="utf-8")
    assert not re.search(r"^\s*amh_interop_scopes\s*=", tfvars, re.MULTILINE)


def test_default_da_variavel_e_o_da_main() -> None:
    variaveis = (_ENV / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "amh_interop_scopes" {', 1)[1].split("\n}", 1)[0]
    assert f'default     = "{ESCOPOS_DA_MAIN}"' in bloco


def test_escopos_do_acesso_so_com_a_flag_do_acesso() -> None:
    expr = _expressao_efetiva()
    assert 'var.acesso_beneficiario ? ["interop/subject.verify", "interop/consent.write"] : []' in expr
    assert 'var.helena_consultas_amh ? ["interop/tina.read"] : []' in expr
    assert expr.count("interop/subject.verify") == 1 and expr.count("interop/consent.write") == 1


def test_default_do_receptor_e_o_da_main() -> None:
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    assert WhatsAppWebhookSettings.model_fields["amh_interop_scopes"].default == ESCOPOS_DA_MAIN


@pytest.mark.skipif(
    shutil.which("terraform") is None, reason="terraform ausente: as cercas textuais acima valem"
)
@pytest.mark.parametrize(
    ("consultas", "acesso", "esperado"),
    [
        (False, False, ESCOPOS_DA_MAIN),
        (True, False, ESCOPOS_DA_MAIN + " interop/tina.read"),
        (False, True, ESCOPOS_DA_MAIN + " interop/subject.verify interop/consent.write"),
        (True, True, ESCOPOS_DA_MAIN + " interop/tina.read interop/subject.verify interop/consent.write"),
    ],
)
def test_avaliacao_real_da_expressao(tmp_path: Path, consultas: bool, acesso: bool, esperado: str) -> None:
    (tmp_path / "main.tf").write_text(
        f'variable "amh_interop_scopes" {{\n  default = "{ESCOPOS_DA_MAIN}"\n}}\n'
        f'variable "helena_consultas_amh" {{\n  default = {str(consultas).lower()}\n}}\n'
        f'variable "acesso_beneficiario" {{\n  default = {str(acesso).lower()}\n}}\n'
        f"locals {{\n  amh_interop_scopes_efetivos = {_expressao_efetiva()}\n}}\n",
        encoding="utf-8",
    )
    saida = subprocess.run(  # noqa: S603 - binario local, entrada fixa do teste
        ["terraform", "console"],  # noqa: S607
        input="local.amh_interop_scopes_efetivos\n",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
        check=True,
    )
    assert saida.stdout.strip() == f'"{esperado}"'
