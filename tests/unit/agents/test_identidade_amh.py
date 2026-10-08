"""Resolvedor de sujeito e consentimento da fonte real: so' UM candidato, so' consentimento concedido."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.lucas.identidade_amh import FonteDeConsentimentoAmh, ResolvedorDeSujeitoAmh
from maezo.ports.consent import ConsentDecision
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailureReason, PortResult
from maezo.ports.subject_resolution import SubjectCandidate, SubjectResolution

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e6f"


def _resolucao(*refs: str) -> SubjectResolution:
    candidatos = tuple(
        SubjectCandidate(r, "titular" if i == 0 else "dependente", True) for i, r in enumerate(refs)
    )
    resultado = {0: "nenhum", 1: "unico"}.get(len(candidatos), "multiplos")
    return SubjectResolution(resultado=resultado, candidatos=candidatos, campos_ausentes=frozenset())


class _Port:
    def __init__(self, resultado: PortResult[SubjectResolution]) -> None:
        self.resultado = resultado
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def resolve_by_phone(self, phone_hash: str, **kw: Any) -> PortResult[SubjectResolution]:
        self.chamadas.append((phone_hash, kw))
        return self.resultado

    async def get_profile(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover - nao usado aqui
        raise AssertionError


def _resolvedor(port: _Port) -> ResolvedorDeSujeitoAmh:
    return ResolvedorDeSujeitoAmh(
        port=port, amh_tenant="omni", hash_scheme="maezo-hk1", purpose_of_use="purpose1"
    )


async def test_um_unico_candidato_resolve() -> None:
    port = _Port(PortResult.ok(_resolucao(REF)))
    assert await _resolvedor(port).portable_ref("pseudo", phone_hash="hk1_abc") == REF
    hash_enviado, kw = port.chamadas[0]
    assert hash_enviado == "hk1_abc"
    assert kw == {
        "amh_tenant": "omni",
        "hash_scheme": "maezo-hk1",
        "purpose_of_use": "purpose1",
        "timeout_seconds": DEFAULT_PORT_TIMEOUT_SECONDS,  # o Lucas fica no padrao do port
    }


async def test_prazo_da_chamada_configuravel_chega_ao_port() -> None:
    """DL-0079: a Helena passa o prazo total da identidade como teto da chamada de resolucao."""
    port = _Port(PortResult.ok(_resolucao(REF)))
    resolvedor = ResolvedorDeSujeitoAmh(
        port=port,
        amh_tenant="omni",
        hash_scheme="maezo-hk1",
        purpose_of_use="purpose1",
        timeout_seconds=15.0,
    )
    assert await resolvedor.portable_ref("pseudo", phone_hash="hk1_abc") == REF
    assert port.chamadas[0][1]["timeout_seconds"] == 15.0


@pytest.mark.parametrize("prazo", [0, -1.0, float("nan"), float("inf"), True, "5"])
def test_prazo_da_chamada_invalido_recusa_na_construcao(prazo: Any) -> None:
    with pytest.raises(ValueError):
        ResolvedorDeSujeitoAmh(
            port=_Port(PortResult.ok(_resolucao(REF))),
            amh_tenant="omni",
            hash_scheme="maezo-hk1",
            purpose_of_use="purpose1",
            timeout_seconds=prazo,
        )


async def test_titular_e_dependente_no_mesmo_aparelho_nao_resolve_ninguem() -> None:
    port = _Port(PortResult.ok(_resolucao(REF, OUTRO)))
    assert await _resolvedor(port).portable_ref("pseudo", phone_hash="hk1_abc") is None


async def test_nenhum_cadastro_nao_resolve() -> None:
    port = _Port(PortResult.ok(_resolucao()))
    assert await _resolvedor(port).portable_ref("pseudo", phone_hash="hk1_abc") is None


async def test_sem_hash_do_telefone_nem_chama_a_amh() -> None:
    port = _Port(PortResult.ok(_resolucao(REF)))
    assert await _resolvedor(port).portable_ref("pseudo", phone_hash=None) is None
    assert await _resolvedor(port).portable_ref("pseudo", phone_hash="") is None
    assert port.chamadas == []


@pytest.mark.parametrize("razao", list(PortFailureReason))
async def test_toda_recusa_do_port_nao_resolve(razao: PortFailureReason) -> None:
    port = _Port(PortResult.refused(razao))
    assert await _resolvedor(port).portable_ref("pseudo", phone_hash="hk1_abc") is None


# --- consentimento -------------------------------------------------------------------------------


def _decisao(**sobre: Any) -> ConsentDecision:
    base: dict[str, Any] = {
        "consent_decision_ref": "consent-1",
        "portable_subject_ref": REF,
        "purpose_of_use": "purpose1",
        "granted": True,
        "consent_revision": 3,
        "decided_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    base.update(sobre)
    return ConsentDecision(**base)


class _Consent:
    def __init__(self, resultado: PortResult[ConsentDecision]) -> None:
        self.resultado = resultado

    async def latest_decision(
        self, ref: str, *, purpose_of_use: str, **kw: Any
    ) -> PortResult[ConsentDecision]:
        return self.resultado


async def test_consentimento_concedido_devolve_a_referencia() -> None:
    fonte = FonteDeConsentimentoAmh(_Consent(PortResult.ok(_decisao())))  # type: ignore[arg-type]
    assert await fonte.decisao(REF, "purpose1") == "consent-1"


@pytest.mark.parametrize(
    "sobre",
    [{"granted": False}, {"portable_subject_ref": OUTRO}, {"purpose_of_use": "outro"}],
)
async def test_consentimento_negado_ou_de_outro_sujeito_ou_proposito_nao_autoriza(
    sobre: dict[str, Any],
) -> None:
    fonte = FonteDeConsentimentoAmh(_Consent(PortResult.ok(_decisao(**sobre))))  # type: ignore[arg-type]
    assert await fonte.decisao(REF, "purpose1") is None


@pytest.mark.parametrize("razao", list(PortFailureReason))
async def test_sem_decisao_ou_fonte_fora_nao_autoriza(razao: PortFailureReason) -> None:
    fonte = FonteDeConsentimentoAmh(_Consent(PortResult.refused(razao)))  # type: ignore[arg-type]
    assert await fonte.decisao(REF, "purpose1") is None
