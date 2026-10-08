"""Testes do production-validator do plano vendor (VW5+, WAVES §2.9-ii).

Verde contra o estado default-off construído em teste; VERMELHO quando CADA invariante é
violada (mutação in-body — o teste tem dentes, não é captura de snapshot); fail-closed em
unknown. Nada aqui toca engine, DMN, BPMN, flag de produção ou deploy — o validator é leitor e
o go/no-go permanece HUMANO.

Estrutura:
    1. Verdade estrutural da base (o que o validator assume e re-verifica a cada execução).
    2. Verde no estado default-off (os cinco itens PASS).
    3. Vermelho por invariante violada (um item por mutação; os demais permanecem PASS).
    4. Fail-closed em unknown (dado não coletado = FAIL, nunca PASS por omissão).
    5. Sonda SOURCE_UNAVAILABLE (d): comportamento REAL do job VW1-P0 e a contra-prova do
       zero fabricado.
    6. CLI local (exit code 0/1 e JSON).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.vendor_membership_publication_job import (
    VendorMembershipPublicationJob,
    VendorPublicationLedger,
)
from maezo.platform.validators import vendor_plane
from maezo.platform.validators.vendor_plane import (
    DEFAULT_CAPABILITIES_PROFILE,
    EXPECTED_WORKER_BOOTSTRAP_COUNT,
    VendorPlaneTarget,
    local_migration_chain,
    portal_capabilities_default,
    probe_source_unavailable_operative,
    validate_vendor_plane,
)
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS

# ---------------------------------------------------------------------------
# 1. Verdade estrutural da base sob validação
# ---------------------------------------------------------------------------


def test_o_default_de_capabilities_na_base_e_identity() -> None:
    """(a) — o default que nenhuma onda vendor tocou continua sendo o default real do modelo."""
    assert DEFAULT_CAPABILITIES_PROFILE == "identity"
    assert portal_capabilities_default() == "identity"


def test_a_base_tem_exatamente_19_bootstraps_e_o_contrato_e_19() -> None:
    """(c) — a constante é contrato; se a base crescer, o diverge conscientemente (vermelho)."""
    assert EXPECTED_WORKER_BOOTSTRAP_COUNT == 19  # VW4/GP11 wiring: +suppression
    assert len(ALL_WORKER_BOOTSTRAPS) == EXPECTED_WORKER_BOOTSTRAP_COUNT


def test_a_cadeia_local_e_head_linear_0022_revisando_0021() -> None:
    """(b) — leitura estrutural via ScriptDirectory (não regex): uma head, e 0022 revisa 0021
    (0021 = store de supressão GP11 — VW4 wiring; 0022 = acesso do beneficiario, DL-0083)."""
    heads, edges = local_migration_chain()
    assert heads == ("0022",)
    assert edges["0022"] == "0021"
    assert edges["0021"] == "0020"


# ---------------------------------------------------------------------------
# 2. Verde no estado default-off
# ---------------------------------------------------------------------------


def _applied_chain() -> tuple[str, ...]:
    """A cadeia inteira da base, na ordem em que o alembic a enfileira."""
    _, edges = local_migration_chain()
    return tuple(edges)


def _estado_default_off(tmp_path: Path, **overrides: object) -> VendorPlaneTarget:
    ledger = tmp_path / "VW5-METRICS.md"
    ledger.write_text("# VW5-METRICS — dashboard de contagens (baseline→pós-VW3)\n", encoding="utf-8")
    values: dict[str, object] = {
        "capabilities_deployed": "identity",
        "migration_heads": ("0022",),
        "applied_migrations": _applied_chain(),
        "worker_bootstrap_count": EXPECTED_WORKER_BOOTSTRAP_COUNT,
        "published_vendor_memberships": 0,
        "evidence_ledger_path": ledger,
    }
    values.update(overrides)
    return VendorPlaneTarget(**values)  # type: ignore[arg-type]


def test_validador_verde_no_estado_default_off(tmp_path: Path) -> None:
    report = validate_vendor_plane(_estado_default_off(tmp_path))
    assert report.passed is True
    assert {check.name for check in report.checks} == {
        "vendor_flags_default_off",
        "migrations_linear_head",
        "readiness_workers_19_19",
        "vendor_memberships_unpublished_default",
        "evidence_ledger_green",
    }
    assert all(check.passed for check in report.checks)
    por_nome = report.by_name()
    # (a) observado, não presumido:
    assert por_nome["vendor_flags_default_off"].observed["capabilities_default"] == "identity"
    assert por_nome["vendor_flags_default_off"].observed["vendor_granted"] is False
    # (d) a inércia é OPERANTE (sonda comportamental), não declarada:
    assert por_nome["vendor_memberships_unpublished_default"].observed["source_unavailable_operative"] is True
    assert por_nome["vendor_memberships_unpublished_default"].observed["published_vendor_memberships"] == 0
    # (e) evidência é digest, nunca conteúdo:
    assert len(str(por_nome["evidence_ledger_green"].observed["evidence_ledger_sha256"])) == 64
    # o relatório inteiro é JSON-serializável (escalar puro):
    assert json.loads(report.as_json())["passed"] is True


# ---------------------------------------------------------------------------
# 3. Vermelho por invariante violada (um item por mutação)
# ---------------------------------------------------------------------------


def _apenas_este_item_falha(tmp_path: Path, alvo: VendorPlaneTarget, nome: str) -> None:
    report = validate_vendor_plane(alvo)
    falhos = [check.name for check in report.checks if not check.passed]
    assert falhos == [nome]
    assert report.passed is False


def test_a_perfil_vendor_deployado_fica_vermelho(tmp_path: Path) -> None:
    """A quarta literal existe desde a VW1-P4 — nomeá-la é o flip que este item acusa."""
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, capabilities_deployed="identity,staff_cases,human,vendor"),
        "vendor_flags_default_off",
    )


def test_a_default_mudado_na_base_fica_vermelho(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Se alguém tocar o default do modelo, o item (a) fecha — mesmo com o deploy nomeando identity."""
    monkeypatch.setattr(
        vendor_plane, "portal_capabilities_default", lambda: "identity,staff_cases,human,vendor"
    )
    _apenas_este_item_falha(tmp_path, _estado_default_off(tmp_path), "vendor_flags_default_off")


def test_a_perfil_com_forma_invalida_fica_vermelho(tmp_path: Path) -> None:
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, capabilities_deployed="identity,,vendor"),
        "vendor_flags_default_off",
    )


def test_b_heads_bifurcadas_ficam_vermelhas(tmp_path: Path) -> None:
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, migration_heads=("0019", "0021")),
        "migrations_linear_head",
    )


def test_b_0019_faltando_na_aplicacao_fica_vermelho(tmp_path: Path) -> None:
    """0020/0021 aplicadas sem 0019 não é head linear vendor — cadeia quebrada no banco."""
    sem_0019 = tuple(rev for rev in _applied_chain() if rev != "0019")
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, applied_migrations=sem_0019),
        "migrations_linear_head",
    )


def test_c_readiness_degradada_fica_vermelha(tmp_path: Path) -> None:
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, worker_bootstrap_count=17),
        "readiness_workers_19_19",
    )


def test_c_bootstrap_20_na_base_fica_vermelho(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Um 19º bootstrap na base diverge do CONTRATO 18 — vermelho até re-aprovação consciente."""
    monkeypatch.setattr(vendor_plane, "local_worker_bootstrap_count", lambda: 20)
    _apenas_este_item_falha(tmp_path, _estado_default_off(tmp_path), "readiness_workers_19_19")


def test_d_store_populado_no_default_fica_vermelho(tmp_path: Path) -> None:
    """Membership vendor publicada no estado default = flag-off violada EM DADO, não em config."""
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, published_vendor_memberships=2),
        "vendor_memberships_unpublished_default",
    )


def test_d_job_que_fabrica_zero_fica_vermelho(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Um job que responda `VendorJobResult(0, 0)` para store vazio (zero FABRICADO) é o modo de
    falha exato que o item (d) existe para acusar — a sonda o converte em vermelho."""

    class _JobFabricante:
        def __init__(self, **seams: object) -> None: ...

        async def run(self) -> object:
            return ("published", 0)  # respondeu sem recusar: exatamente a fabricação proibida

    monkeypatch.setattr(vendor_plane, "VendorMembershipPublicationJob", _JobFabricante)
    _apenas_este_item_falha(tmp_path, _estado_default_off(tmp_path), "vendor_memberships_unpublished_default")


def test_e_ledger_ausente_ou_vazio_fica_vermelho(tmp_path: Path) -> None:
    vazio = tmp_path / "vazio.md"
    vazio.write_text("", encoding="utf-8")
    _apenas_este_item_falha(
        tmp_path,
        _estado_default_off(tmp_path, evidence_ledger_path=tmp_path / "inexistente.md"),
        "evidence_ledger_green",
    )
    _apenas_este_item_falha(
        tmp_path, _estado_default_off(tmp_path, evidence_ledger_path=vazio), "evidence_ledger_green"
    )


# ---------------------------------------------------------------------------
# 4. Fail-closed em unknown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("campo", "nome"),
    [
        ("capabilities_deployed", "vendor_flags_default_off"),
        ("migration_heads", "migrations_linear_head"),
        ("applied_migrations", "migrations_linear_head"),
        ("worker_bootstrap_count", "readiness_workers_19_19"),
        ("published_vendor_memberships", "vendor_memberships_unpublished_default"),
        ("evidence_ledger_path", "evidence_ledger_green"),
    ],
)
def test_dado_nao_coletado_e_fail_nunca_pass_por_omissao(tmp_path: Path, campo: str, nome: str) -> None:
    _apenas_este_item_falha(tmp_path, _estado_default_off(tmp_path, **{campo: None}), nome)


def test_alvo_vazio_falha_em_todos_os_itens() -> None:
    """`VendorPlaneTarget()` sem nenhum fato: cinco FAIL, veredito FAIL — o inverso do stub
    always-pass que o ledger de evidência existe para impossibilitar."""
    report = validate_vendor_plane(VendorPlaneTarget())
    assert report.passed is False
    assert [check.passed for check in report.checks] == [False] * 5
    assert all("unknown" in check.detail or "error" in check.detail for check in report.checks)


# ---------------------------------------------------------------------------
# 5. Sonda SOURCE_UNAVAILABLE (d): comportamento real do job VW1-P0
# ---------------------------------------------------------------------------


def test_a_sonda_prova_a_inercia_honesta_do_job_real(tmp_path: Path) -> None:
    """Contra store vazio E ausente, o job VW1-P0 REAL recusa com `ReadRefusalError` e o
    publisher nunca é alcançado — `SOURCE_UNAVAILABLE` operante, não zero fabricado."""
    ok, detalhe = probe_source_unavailable_operative()
    assert ok is True, detalhe


def test_job_real_com_store_vazio_recusa_e_nunca_publica(tmp_path: Path) -> None:
    """A prova independe do validator: o módulo do alvo, conduzido à mão, recusa."""

    class _PublisherDenuncia:
        chamado = False

        async def publish(self, payload: bytes) -> None:
            _PublisherDenuncia.chamado = True

    class _LeitorVazio:
        async def channels(self, tenant: str) -> tuple[object, ...] | None:
            return ()

    ledger = VendorPublicationLedger(path=tmp_path / "ledger.json", tenant="test-tenant")
    job = VendorMembershipPublicationJob(
        channels=_LeitorVazio(),  # type: ignore[arg-type]
        memberships=_PublisherDenuncia(),  # type: ignore[arg-type]
        publisher=_PublisherDenuncia(),  # type: ignore[arg-type]
        ledger=ledger,
    )
    with pytest.raises(ReadRefusalError):
        asyncio.run(job.run())
    assert _PublisherDenuncia.chamado is False


# ---------------------------------------------------------------------------
# 6. CLI local
# ---------------------------------------------------------------------------


def _cli_fatos(tmp_path: Path) -> list[str]:
    _, edges = local_migration_chain()
    ledger = tmp_path / "VW5-METRICS.md"
    ledger.write_text("# VW5-METRICS\n", encoding="utf-8")
    return [
        "--capabilities",
        "identity",
        "--migration-heads",
        "0022",
        "--applied-migrations",
        ",".join(edges),
        "--worker-count",
        str(EXPECTED_WORKER_BOOTSTRAP_COUNT),
        "--published-memberships",
        "0",
        "--evidence-ledger",
        str(ledger),
    ]


def test_cli_verde_sai_0_e_vermelho_sai_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert vendor_plane.main(_cli_fatos(tmp_path)) == 0
    saida = capsys.readouterr().out
    assert "[PASS]" in saida and "VEREDITO: PASS" in saida

    assert (
        vendor_plane.main([*_cli_fatos(tmp_path), "--capabilities", "identity,staff_cases,human,vendor"]) == 1
    )
    assert "VEREDITO: FAIL" in capsys.readouterr().out


def test_cli_sem_fatos_sai_1_e_json_e_escalar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A CLI sem nenhuma flag é o caso 'nada coletado': fail-closed, exit 1."""
    assert vendor_plane.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert len(payload["checks"]) == 5
