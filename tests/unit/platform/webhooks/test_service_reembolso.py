"""A composicao do reembolso em `service.py` (DL-0157/DL-0158, `MAEZO_REEMBOLSO_V1`).

Desligada (o padrao), nada e' construido. Ligada, so' nasce com TODAS as pre-condicoes (Lucas, acesso do
beneficiario, custodia de arquivo, banco); faltando alguma, `None` e o log `reembolso_v1_sem_pre_condicoes` —
o receptor sobe sem o reembolso, nunca aceita um arquivo que nao sabe onde guardar. HOJE a custodia real nao
existe (`_custodia_de_arquivo` devolve `None`), entao a chave ligada sobe sem o reembolso.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import structlog

import maezo.platform.webhooks.service as svc
from maezo.gateway.reembolso_anexos import AnexoGuardado
from maezo.platform.webhooks.whatsapp.reembolso_turno import ReembolsoTurno
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink

_DSN = "postgresql+asyncpg://user:pw@localhost:5432/maezo"
RAIZ = Path(__file__).resolve().parents[4]


def _settings(**overrides: object) -> WhatsAppWebhookSettings:
    base: dict[str, object] = {
        "app_secret": "s3cret",
        "verify_token": "vt",
        "tenant_id": "amh",
        "phi_hmac_key": "test-phi-hmac-key",
        "database_url": _DSN,
    }
    base.update(overrides)
    return WhatsAppWebhookSettings(**base)  # type: ignore[arg-type]


class _Custodia:
    async def guardar_anexo(
        self, *, conversation_id: str, mime: str, conteudo: bytes
    ) -> AnexoGuardado:  # pragma: no cover
        return AnexoGuardado("doc-1", "a" * 64)


def _lucas() -> Any:
    return SimpleNamespace(cibseven=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink())


def _dispatcher(*, acesso: bool = True) -> Any:
    return SimpleNamespace(acesso=object() if acesso else None)


def test_a_chave_nasce_desligada() -> None:
    assert _settings().reembolso_v1 is False


def test_a_chave_le_a_variavel_de_ambiente(monkeypatch: Any) -> None:
    monkeypatch.setenv("MAEZO_REEMBOLSO_V1", "true")
    assert (
        WhatsAppWebhookSettings(  # type: ignore[call-arg]
            app_secret="s", verify_token="v", tenant_id="amh", phi_hmac_key="k", database_url=_DSN
        ).reembolso_v1
        is True
    )


def test_desligada_nada_e_construido() -> None:
    assert svc._build_reembolso_turno(_settings(), _dispatcher(), _lucas(), custodia=_Custodia()) is None


def test_ligada_com_todas_as_pre_condicoes_constroi_o_turno(monkeypatch: Any) -> None:
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-sintetico")
    turno = svc._build_reembolso_turno(
        _settings(reembolso_v1=True), _dispatcher(), _lucas(), custodia=_Custodia()
    )
    assert isinstance(turno, ReembolsoTurno)


def test_ligada_sem_custodia_sobe_sem_o_reembolso_e_diz_por_que() -> None:
    with structlog.testing.capture_logs() as logs:
        turno = svc._build_reembolso_turno(_settings(reembolso_v1=True), _dispatcher(), _lucas())
    assert turno is None
    (aviso,) = [e for e in logs if e["event"] == "reembolso_v1_sem_pre_condicoes"]
    assert aviso["faltando"] == ["custodia_de_arquivo"]


def test_ligada_sem_lucas_acesso_ou_banco_diz_o_que_falta() -> None:
    with structlog.testing.capture_logs() as logs:
        assert (
            svc._build_reembolso_turno(
                _settings(reembolso_v1=True, database_url=None),
                _dispatcher(acesso=False),
                None,
                custodia=_Custodia(),
            )
            is None
        )
    (aviso,) = [e for e in logs if e["event"] == "reembolso_v1_sem_pre_condicoes"]
    assert aviso["faltando"] == ["lucas", "acesso_beneficiario", "database_url"]


def test_hoje_a_custodia_real_nao_existe() -> None:
    assert svc._custodia_de_arquivo(_settings(reembolso_v1=True)) is None


def test_a_chave_esta_no_terraform_com_default_false_e_no_receptor() -> None:
    variaveis = (RAIZ / "deploy/aws-ecs/envs/dev-sa-east-1/variables.tf").read_text(encoding="utf-8")
    bloco = re.search(r'variable "reembolso_v1" \{.*?\n\}\n', variaveis, re.S)
    assert bloco is not None and "default     = false" in bloco.group(0)
    receptor = (RAIZ / "deploy/aws-ecs/envs/dev-sa-east-1/service-webhook-receiver.tf").read_text(
        encoding="utf-8"
    )
    assert 'name = "MAEZO_REEMBOLSO_V1", value = tostring(var.reembolso_v1)' in receptor
    tfvars = (RAIZ / "deploy/aws-ecs/envs/dev-sa-east-1/lucas.auto.tfvars").read_text(encoding="utf-8")
    assert "reembolso_v1" not in tfvars  # a chave NAO sobe ligada em lugar nenhum
