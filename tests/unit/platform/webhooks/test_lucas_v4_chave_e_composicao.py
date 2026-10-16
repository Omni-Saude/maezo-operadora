"""`MAEZO_LUCAS_V4` (DL-0114): a chave, o Terraform e a composicao do receptor.

- nasce DESLIGADA e le o nome canonico do ambiente; ligada EXIGE `MAEZO_LUCAS_CONSULTA_VALORES` (sem os
  fatos de valor nao ha' como saber quem e' o pagador), que por sua vez exige o acesso e a fonte `amh`;
- no Terraform: variavel com default `false` e validacao, valor `true` so' no dev (`lucas.auto.tfvars`),
  env no contrato COMPARTILHADO do receptor (que o simulador e o lembrete herdam), nenhum outro ambiente;
- a composicao: o turno nasce desligado, e a fonte v4 (segundo sinal) so' existe com a chave, os DOIS
  contratos novos pinados, a fonte AMH, o acesso e um executor governado — hoje nunca.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.agents.lucas.fonte_v4 import (
    ARTIFACT_DETALHAMENTO,
    ARTIFACT_SEGUNDA_VIA,
    LOCK_KEY_DETALHAMENTO,
    LOCK_KEY_SEGUNDA_VIA,
    ExecutorGovernado,
    FonteV4,
)
from maezo.platform.webhooks import service as svc
from maezo.platform.webhooks.whatsapp.acesso_ponte import RefsVerificadas
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from tests.unit.platform.webhooks.test_service_roteador import _amh, _settings

_REPO = Path(__file__).resolve().parents[4]
_DEV = _REPO / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"


# --- 1. a chave -----------------------------------------------------------------------------------------
def test_a_chave_nasce_desligada_e_tem_o_nome_canonico() -> None:
    assert _settings().lucas_v4 is False
    campo = WhatsAppWebhookSettings.model_fields["lucas_v4"]
    assert campo.default is False and "MAEZO_LUCAS_V4" in str(campo.validation_alias)


def test_ligada_le_o_ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAEZO_LUCAS_V4", "true")
    monkeypatch.setenv("MAEZO_LUCAS_CONSULTA_VALORES", "true")
    monkeypatch.setenv("MAEZO_ACESSO_BENEFICIARIO", "true")
    assert _settings(**_amh(tmp_path)).lucas_v4 is True


def test_ligada_com_os_valores_e_o_acesso_sobe(tmp_path: Path) -> None:
    settings = _settings(
        lucas_v4=True, lucas_consulta_valores=True, acesso_beneficiario=True, **_amh(tmp_path)
    )
    assert settings.lucas_v4 is True


def test_ligada_sem_a_consulta_de_valores_recusa_o_boot(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="MAEZO_LUCAS_V4 exige MAEZO_LUCAS_CONSULTA_VALORES"):
        _settings(lucas_v4=True, acesso_beneficiario=True, **_amh(tmp_path))


def test_ligada_sem_o_acesso_recusa_o_boot_pela_cerca_dos_valores(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="MAEZO_ACESSO_BENEFICIARIO"):
        _settings(lucas_v4=True, lucas_consulta_valores=True, **_amh(tmp_path))


# --- 2. o Terraform -------------------------------------------------------------------------------------
def test_terraform_variavel_default_false_validada_e_valor_true_so_no_dev() -> None:
    variaveis = (_DEV / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "lucas_v4"', 1)[1].split("\n}\n", 1)[0]
    assert "default     = false" in bloco and "nullable    = false" in bloco
    assert "!var.lucas_v4 || var.lucas_consulta_valores" in bloco
    tfvars = (_DEV / "lucas.auto.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^lucas_v4 = true$", tfvars, re.MULTILINE)
    # a dependencia que a validacao exige tambem esta' ligada no dev (senao o plan falha)
    assert re.search(r"^lucas_consulta_valores = true$", tfvars, re.MULTILINE)
    envs = _DEV.parent
    for outro in envs.iterdir():
        if outro.is_dir() and outro.name != "dev-sa-east-1":
            for arquivo in outro.rglob("*.tf*"):
                assert "lucas_v4" not in arquivo.read_text(encoding="utf-8"), arquivo


def test_terraform_a_env_vai_no_contrato_compartilhado_do_receptor() -> None:
    """`environment_app` e' herdado pelo receptor, pelo simulador e pelo lembrete: uma linha, tres tasks."""
    receptor = (_DEV / "service-webhook-receiver.tf").read_text(encoding="utf-8")
    linha = '{ name = "MAEZO_LUCAS_V4", value = tostring(var.lucas_v4) }'
    assert receptor.count(linha) == 1
    assert (
        receptor.index("environment_app = concat(")
        < receptor.index(linha)
        < receptor.index("environment_amh =")
    )
    for arquivo in ("task-simulador.tf", "task-lembrete-cobranca.tf"):
        texto = (_DEV / arquivo).read_text(encoding="utf-8")
        assert "local.webhook_receiver_contrato.environment_app" in texto and "MAEZO_LUCAS_V4" not in texto


# --- 3. a composicao do receptor ---------------------------------------------------------------------
def test_o_turno_do_lucas_nasce_desligado_e_sem_fonte_v4() -> None:
    settings = _settings(roteador_lucas_enabled=True)
    dispatcher, _ = svc._build_dispatcher(settings)
    turno = svc._build_lucas_turno(settings, dispatcher)
    assert turno is not None and turno.lucas_v4 is False and turno.fonte_v4 is None


class _Executor(ExecutorGovernado):
    registered_operations = frozenset({"amh.get_billing_detalhamento", "amh.get_billing_segunda_via"})

    async def execute(self, request: Any) -> Any:  # pragma: no cover - nao e' chamado aqui
        raise AssertionError


def _pin(tmp_path: Path, *, blocos: bool) -> Path:
    bruto: dict[str, Any] = {}
    if blocos:
        bruto[LOCK_KEY_DETALHAMENTO] = {"artifacts": [{"path": ARTIFACT_DETALHAMENTO, "sha256": "a" * 64}]}
        bruto[LOCK_KEY_SEGUNDA_VIA] = {"artifacts": [{"path": ARTIFACT_SEGUNDA_VIA, "sha256": "b" * 64}]}
    caminho = tmp_path / "contracts.lock.json"
    caminho.write_text(json.dumps(bruto), encoding="utf-8")
    return caminho


_SETTINGS_V4 = SimpleNamespace(lucas_v4=True, tenant_id="amh", lucas_cobranca_prazo_s=15.0)


def test_sem_os_contratos_pinados_nao_ha_fonte_v4(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAEZO_AMH_CONTRACT_PIN", str(_pin(tmp_path, blocos=False)))
    interop = SimpleNamespace(executor_v4=_Executor())
    assert svc._build_fonte_v4_amh(_SETTINGS_V4, interop, refs=RefsVerificadas()) is None  # type: ignore[arg-type]


def test_pin_ilegivel_ou_ausente_nunca_derruba_o_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAEZO_AMH_CONTRACT_PIN", str(tmp_path / "nao-existe.json"))
    assert svc._build_fonte_v4_amh(_SETTINGS_V4, None) is None  # type: ignore[arg-type]


def test_contratos_pinados_mas_sem_fonte_amh_ou_acesso_ou_executor_nao_ha_fonte_v4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAEZO_AMH_CONTRACT_PIN", str(_pin(tmp_path, blocos=True)))
    executor = _Executor()
    assert svc._build_fonte_v4_amh(_SETTINGS_V4, None, refs=RefsVerificadas()) is None  # type: ignore[arg-type]
    assert svc._build_fonte_v4_amh(_SETTINGS_V4, SimpleNamespace(executor_v4=executor), refs=None) is None  # type: ignore[arg-type]
    # o executor real do gateway ainda nao existe (runbook): pinado e sem executor = sem fonte
    assert svc._build_fonte_v4_amh(_SETTINGS_V4, SimpleNamespace(), refs=RefsVerificadas()) is None  # type: ignore[arg-type]


def test_com_tudo_a_fonte_v4_nasce_com_as_duas_rotas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAEZO_AMH_CONTRACT_PIN", str(_pin(tmp_path, blocos=True)))
    interop = SimpleNamespace(executor_v4=_Executor())
    fonte = svc._build_fonte_v4_amh(_SETTINGS_V4, interop, refs=RefsVerificadas())  # type: ignore[arg-type]
    assert isinstance(fonte, FonteV4) and fonte.tem_detalhamento and fonte.tem_segunda_via


def test_a_chave_desligada_o_pin_nao_liga_nada(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAEZO_AMH_CONTRACT_PIN", str(_pin(tmp_path, blocos=True)))
    desligada = SimpleNamespace(lucas_v4=False, tenant_id="amh", lucas_cobranca_prazo_s=15.0)
    interop = SimpleNamespace(executor_v4=_Executor())
    assert svc._build_fonte_v4_amh(desligada, interop, refs=RefsVerificadas()) is None  # type: ignore[arg-type]
