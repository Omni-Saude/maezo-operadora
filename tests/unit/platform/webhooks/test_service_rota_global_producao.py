"""DL-0192: guarda "nao producao" da rota global na composicao (`service._rota_global`), falha fechada.

Em modo de producao (`runtime_mode` diferente de `local`), sem autorizacao registrada, o transporte global
NAO e' construido (o processo fica sem rota global, como com a lista vazia) e o log nao leva conteudo. Com a
autorizacao (o rotulo literal da rota na env dedicada, ou a PONTE PROVISORIA da excecao regional do DPO que o
receptor do dev carrega, contada pelo evento `rota_global_autorizada_por_ponte`), e fora de producao, a rota
sai como antes. Em prod a ponte nunca autoriza: o terraform de prod recusa excecao regional nao vazia.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

import maezo.platform.webhooks.service as svc
from maezo.runtime.inference.excecao_regional import ENV_PHI_EXCECAO_REGIONAL, ROTA_EXCECAO_US
from maezo.runtime.inference.experimento_global import ROTA_DE_RESIDENCIA, TransporteGlobalExperimental
from maezo.runtime.inference.rota_global import RotaGlobal
from tests.unit.deploy._hcl_probe import (
    REPO_ROOT,
    attr_raw,
    attr_strings,
    block_body,
    strip_comments,
    sub_blocks,
)

_CONVERSA = "wa:amh:hk1_" + "d" * 64
_DSN = "postgresql+asyncpg://user:pw@localhost:5432/maezo"


class _Settings:
    def __init__(self, runtime_mode: object, *, conversas: str = _CONVERSA, todas: bool = False) -> None:
        if runtime_mode is not None:
            self.runtime_mode = runtime_mode
        self.helena_saude_gerativa_conversas = conversas
        self.helena_rota_global_todas = todas


@pytest.fixture(autouse=True)
def _sem_autorizacao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(svc.ENV_AUTORIZACAO_ROTA_GLOBAL, raising=False)
    monkeypatch.delenv(ENV_PHI_EXCECAO_REGIONAL, raising=False)


@pytest.mark.parametrize("modo", ["production", "kubernetes", "", None])
@pytest.mark.parametrize("todas", [False, True])
def test_producao_sem_autorizacao_nao_constroi_o_transporte(
    monkeypatch: pytest.MonkeyPatch, modo: object, todas: bool
) -> None:
    construidos: list[object] = []
    monkeypatch.setattr(TransporteGlobalExperimental, "__init__", lambda self, **_: construidos.append(self))
    with capture_logs() as logs:
        rota = svc._rota_global(_Settings(modo, todas=todas))  # type: ignore[arg-type]
    assert rota is None and construidos == []
    recusas = [e for e in logs if e["event"] == "helena_rota_global_recusada_sem_autorizacao"]
    assert len(recusas) == 1 and recusas[0]["log_level"] == "error"
    assert set(recusas[0]) == {
        "event",
        "log_level",
        "rota",
        "conversas_autorizadas",
        "todas_as_conversas",
        "env_da_autorizacao",
    }
    assert _CONVERSA not in str(recusas[0])  # so' contagens, nunca o id da conversa
    assert not [e for e in logs if e["event"] == "helena_rota_global_ligada"]


@pytest.mark.parametrize("valor", ["true", "1", "sim", ROTA_DE_RESIDENCIA + "x", ROTA_EXCECAO_US])
def test_autorizacao_so_com_o_rotulo_literal(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv(svc.ENV_AUTORIZACAO_ROTA_GLOBAL, valor)
    assert svc._rota_global(_Settings("production")) is None  # type: ignore[arg-type]


def test_excecao_regional_com_outro_rotulo_nao_autoriza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PHI_EXCECAO_REGIONAL, ROTA_DE_RESIDENCIA)
    assert svc._rota_global(_Settings("production")) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("env", "valor", "autorizacao"),
    [
        (svc.ENV_AUTORIZACAO_ROTA_GLOBAL, ROTA_DE_RESIDENCIA, "rotulo_da_rota"),
        (ENV_PHI_EXCECAO_REGIONAL, ROTA_EXCECAO_US, "ponte_provisoria_excecao_regional"),
    ],
)
def test_producao_com_autorizacao_constroi_como_hoje(
    monkeypatch: pytest.MonkeyPatch, env: str, valor: str, autorizacao: str
) -> None:
    monkeypatch.setenv(env, valor)
    with capture_logs() as logs:
        rota = svc._rota_global(_Settings("production"))  # type: ignore[arg-type]
    assert isinstance(rota, RotaGlobal) and isinstance(rota.transporte, TransporteGlobalExperimental)
    assert rota.conversas == frozenset({_CONVERSA}) and rota.todas is False
    ligada = [e for e in logs if e["event"] == "helena_rota_global_ligada"]
    assert len(ligada) == 1 and ligada[0]["autorizacao"] == autorizacao


def test_fora_de_producao_constroi_como_hoje() -> None:
    rota = svc._rota_global(_Settings("local", todas=True))  # type: ignore[arg-type]
    assert isinstance(rota, RotaGlobal) and rota.todas is True


@pytest.mark.parametrize("modo", ["production", "local"])
def test_sem_lista_e_sem_todas_nada_muda(modo: str) -> None:
    with capture_logs() as logs:
        assert svc._rota_global(_Settings(modo, conversas="")) is None  # type: ignore[arg-type]
    assert logs == []


def test_despachante_de_producao_sem_autorizacao_nasce_sem_rota_global() -> None:
    """O fluxo degrada sem quebrar: o despachante e' construido e a Helena fica no caminho de sempre (zona
    PHI, sem porta unica nem roteador pela rota global)."""
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    settings = WhatsAppWebhookSettings(
        app_secret="s3cret",
        verify_token="vt",
        tenant_id="amh",
        phi_hmac_key="test-phi-hmac-key",
        database_url=_DSN,
        runtime_mode="production",
        helena_rota_global_todas=True,
    )
    dispatcher, _ = svc._build_dispatcher(settings)
    assert dispatcher.rota_global is None


_PONTE = "rota_global_autorizada_por_ponte"
_FRASE_DA_PONTE = (
    "a exceção regional de 21/10 não nomeia a rota global; a ponte existe só para não mudar o "
    "comportamento do dev antes de o dono definir `MAEZO_ROTA_GLOBAL_AUTORIZACAO` no deploy."
)


def test_producao_sem_nenhuma_das_duas_nao_monta() -> None:
    with capture_logs() as logs:
        assert svc._rota_global(_Settings("production")) is None  # type: ignore[arg-type]
    assert not [e for e in logs if e["event"] in {_PONTE, "helena_rota_global_ligada"}]


def test_producao_com_a_env_nova_monta_sem_passar_pela_ponte(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(svc.ENV_AUTORIZACAO_ROTA_GLOBAL, ROTA_DE_RESIDENCIA)
    monkeypatch.setenv(ENV_PHI_EXCECAO_REGIONAL, ROTA_EXCECAO_US)  # as duas: a env nova vence
    with capture_logs() as logs:
        assert isinstance(svc._rota_global(_Settings("production")), RotaGlobal)  # type: ignore[arg-type]
    assert not [e for e in logs if e["event"] == _PONTE]


def test_producao_so_com_a_ponte_monta_e_conta_cada_montagem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PHI_EXCECAO_REGIONAL, ROTA_EXCECAO_US)
    with capture_logs() as logs:
        for _ in range(3):
            assert isinstance(svc._rota_global(_Settings("production")), RotaGlobal)  # type: ignore[arg-type]
    pontes = [e for e in logs if e["event"] == _PONTE]
    assert len(pontes) == 3
    for evento in pontes:
        # So' rotulos fixos: nada da conversa nem de conteudo.
        assert set(evento) == {"event", "log_level", "rota", "ponte", "env_da_autorizacao"}
        assert evento["ponte"] == "ponte_provisoria_excecao_regional" and _CONVERSA not in str(evento)


def test_a_ponte_e_provisoria_no_nome_e_no_motivo() -> None:
    assert svc._PONTE_PROVISORIA_EXCECAO_REGIONAL == "ponte_provisoria_excecao_regional"
    assert svc.PONTE_PROVISORIA_MOTIVO == _FRASE_DA_PONTE
    dl = (REPO_ROOT / "docs" / "decisions-log.md").read_text(encoding="utf-8")
    linha = next(x for x in dl.splitlines() if x.startswith("| 2026-11-18 | DL-0192 |"))
    assert _FRASE_DA_PONTE in linha


def test_em_prod_a_ponte_nunca_autoriza_o_terraform_recusa_excecao_nao_vazia() -> None:
    """O validador de prod (`deploy/aws-ecs/envs/prod-sa-east-1/excecao-regional-phi.tf`) so' aceita vazio,
    e o receptor de prod recebe a excecao SO' dessa variavel: o rotulo da ponte nunca chega ao processo de
    prod."""
    prod = REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "prod-sa-east-1"
    fonte = strip_comments((prod / "excecao-regional-phi.tf").read_text(encoding="utf-8"))
    variavel = block_body(fonte, r'variable\s+"phi_excecao_regional"\s*\{')
    assert attr_strings(variavel, "default") == [""]
    validacoes = sub_blocks(variavel, "validation")
    assert len(validacoes) == 1
    assert attr_raw(validacoes[0], "condition") == 'var.phi_excecao_regional == ""'
    receptor = strip_comments((prod / "service-webhook-receiver.tf").read_text(encoding="utf-8"))
    linhas = [x.strip() for x in receptor.splitlines() if '"MAEZO_PHI_EXCECAO_REGIONAL"' in x]
    assert linhas == ['{ name = "MAEZO_PHI_EXCECAO_REGIONAL", value = var.phi_excecao_regional },']
    assert not [
        p for p in Path(prod).glob("*.tfvars") if "phi_excecao_regional" in p.read_text(encoding="utf-8")
    ]
