"""Cerca de zona na CONSTRUCAO do seam FHIR (ADR-0068 I-5; R-020 / DEC-11, decisao do dono 31/10/2026).

O que a ADR-0016 prometia (um `PhiZoneGateway` pseudonimizando o resultado para a Zona Geral) nunca
existiu; a ADR-0068 troca a promessa por uma checagem que existe: o UNICO construtor sancionado
(`gateway/tool_registry.py::build_fhir_seam` / `build_agent_fhir_seam`) RECUSA construir um seam
cujas operacoes devolvem PHI (`OperationSpec.returns_phi`) para um principal que nao e da Zona PHI.

Tres pernas, todas testes NEGATIVOS da guarda de PHI:
  (1) o catalogo declara `returns_phi` em toda operacao `fhir.*` que o `GatedFhirReader` pode decidir;
  (2) o cruzamento `_FHIR_ADAPTER_BY_AGENT` x `security_zone` do `agent.yaml` x `returns_phi` — nenhum
      agente da Zona Geral recebe adaptador FHIR (era o caso do gustavo ate esta ADR);
  (3) o construtor recusa de fato (por `seam.phi_zone`, nunca por argumento de chamada) e constroi
      normalmente para a Zona PHI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
import yaml

from maezo.gateway import tool_registry
from maezo.gateway.effect_classes import OPERATIONS, OperationSpec
from maezo.gateway.effect_pep import PHI_ZONE_GENERAL, PHI_ZONE_PHI, DecisionContext
from maezo.gateway.seams import is_gated_seam
from maezo.gateway.seams._base import SeamContext
from maezo.gateway.seams.fhir import FHIR_OPERATIONS, GatedFhirReader
from maezo.gateway.tool_registry import (
    _FHIR_ADAPTER_BY_AGENT,
    ZoneFenceError,
    build_agent_fhir_seam,
    build_fhir_seam,
)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SPEC_AGENTS: Final[Path] = _REPO_ROOT / "spec" / "agents"
_BASE_URL: Final[str] = "http://fhir.invalid/fhir"


def _security_zone(agent_id: str) -> str:
    data = yaml.safe_load((_SPEC_AGENTS / agent_id / "agent.yaml").read_text(encoding="utf-8"))
    return str(data.get("security_zone", PHI_ZONE_GENERAL))


def _seam(zone: str, principal: str = "rafael") -> SeamContext:
    return SeamContext(tenant="amh", principal=principal, phi_zone=zone, decision=DecisionContext())


class _Settings:
    tenant_id = "amh"
    fhir_base_url = _BASE_URL


# -- (1) catalogo -----------------------------------------------------------------------------------


def test_operation_spec_returns_phi_defaults_to_false() -> None:
    """Default seguro para o catalogo: so devolve PHI quem DECLARA. A cerca e subtrativa, entao o
    default False nunca amplia permissao — ele so deixa de recusar o que nao foi marcado."""
    spec = OperationSpec(operation="x.y", action_class="avaliacao_dmn", autonomy_action=None)
    assert spec.returns_phi is False


def test_every_gated_fhir_operation_is_catalogued_as_returning_phi() -> None:
    assert FHIR_OPERATIONS, "vocabulario do GatedFhirReader vazio — a cerca seria vacua"
    for operation in sorted(FHIR_OPERATIONS):
        spec = OPERATIONS.get(operation)
        assert spec is not None, f"{operation} nao catalogado"
        assert spec.returns_phi is True, f"{operation} devolve PHI clinico e nao declara returns_phi"


def test_gated_fhir_vocabulary_is_exactly_the_fhir_catalogue() -> None:
    catalogued = {op for op in OPERATIONS if op.startswith("fhir.")}
    assert set(FHIR_OPERATIONS) == catalogued


def test_non_phi_reads_are_not_marked() -> None:
    """Agregados k-anonimos e leituras pseudonimizadas da Zona Geral NAO sao `returns_phi` — marcar
    acima do necessario recusaria agentes que leem esses fatos legitimamente."""
    for operation in (
        "population.actuarial_risk",
        "population.population_metrics",
        "amh.get_billing_status",
        "amh.tina_get_elegibilidade",
        "dmn.evaluate",
        "cibseven.get_process_status",
    ):
        assert OPERATIONS[operation].returns_phi is False, operation


# -- (2) cruzamento mapa x zona x returns_phi -------------------------------------------------------


@pytest.mark.parametrize("agent_id", sorted(_FHIR_ADAPTER_BY_AGENT))
def test_no_general_zone_agent_gets_a_phi_returning_fhir_adapter(agent_id: str) -> None:
    returns_phi = any(OPERATIONS[op].returns_phi for op in FHIR_OPERATIONS)
    zone = _security_zone(agent_id)
    assert not (returns_phi and zone != PHI_ZONE_PHI), (
        f"{agent_id} (security_zone={zone!r}) recebe o adaptador FHIR "
        f"{_FHIR_ADAPTER_BY_AGENT[agent_id]!r}, que devolve PHI — ADR-0068 I-5"
    )


def test_gustavo_has_no_fhir_adapter() -> None:
    """Decisao do dono (31/10/2026, DEC-11 (i)): o Gustavo (Zona Geral) trabalha com os fatos do
    processo; o leitor FHIR cru foi removido."""
    assert _security_zone("gustavo") == PHI_ZONE_GENERAL
    assert "gustavo" not in _FHIR_ADAPTER_BY_AGENT


def test_every_general_zone_agent_is_absent_from_the_fhir_map() -> None:
    general = {
        path.parent.name
        for path in _SPEC_AGENTS.glob("*/agent.yaml")
        if not path.parent.name.startswith("_") and _security_zone(path.parent.name) != PHI_ZONE_PHI
    }
    assert general, "nenhum agente da Zona Geral encontrado — o cruzamento seria vacuo"
    assert not (general & set(_FHIR_ADAPTER_BY_AGENT)), sorted(general & set(_FHIR_ADAPTER_BY_AGENT))


# -- (3) o construtor recusa ------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", ["read_patient", "read_patient_summary"])
def test_build_fhir_seam_refuses_a_general_zone_principal(adapter: str) -> None:
    with pytest.raises(ZoneFenceError) as excinfo:
        build_fhir_seam(
            seam=_seam(PHI_ZONE_GENERAL, principal="gustavo"), base_url=_BASE_URL, adapter=adapter
        )
    message = str(excinfo.value)
    assert "gustavo" in message
    assert _BASE_URL not in message, "a recusa nao ecoa configuracao, so tokens limitados"


@pytest.mark.parametrize("adapter", ["read_patient", "read_patient_summary"])
def test_build_fhir_seam_builds_for_a_phi_zone_principal(adapter: str) -> None:
    built = build_fhir_seam(seam=_seam(PHI_ZONE_PHI), base_url=_BASE_URL, adapter=adapter)
    assert isinstance(built, GatedFhirReader)
    assert is_gated_seam(built)


def test_zone_fence_error_is_a_runtime_error_so_roots_go_unready() -> None:
    """I-2: a falha de construcao propaga e deixa o readiness vermelho — mesma forma do
    `CredentialSeparationError`, nunca um seam degradado sem gate."""
    assert issubclass(ZoneFenceError, RuntimeError)


def test_build_agent_fhir_seam_refuses_a_general_zone_agent_in_the_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se alguem recolocar um agente da Zona Geral no mapa, o construtor recusa — a zona vem do
    `agent.yaml` (via `build_agent_seam_context`), nao do chamador."""
    monkeypatch.setitem(tool_registry._FHIR_ADAPTER_BY_AGENT, "gustavo", "read_patient")
    with pytest.raises(ZoneFenceError):
        build_agent_fhir_seam(settings=_Settings(), agent_id="gustavo")


def test_build_agent_seams_refuses_a_general_zone_agent_in_the_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(tool_registry._FHIR_ADAPTER_BY_AGENT, "lucas", "read_patient_summary")
    with pytest.raises(ZoneFenceError):
        tool_registry.build_agent_seams(settings=_Settings(), agent_id="lucas")


def test_gustavo_agent_seams_carry_no_fhir() -> None:
    deps = tool_registry.build_agent_seams(settings=_Settings(), agent_id="gustavo")
    assert "fhir" not in deps


@pytest.mark.parametrize("agent_id", sorted(_FHIR_ADAPTER_BY_AGENT))
def test_every_mapped_agent_still_builds(agent_id: str) -> None:
    seam = build_agent_fhir_seam(settings=_Settings(), agent_id=agent_id)
    assert isinstance(seam, GatedFhirReader)
    assert seam.seam_context.phi_zone == PHI_ZONE_PHI


def test_fence_refuses_an_uncatalogued_operation_outside_the_phi_zone() -> None:
    """Fail-closed: uma operacao que o catalogo nao conhece nao pode ser provada livre de PHI."""
    with pytest.raises(ZoneFenceError, match="fhir.nao_catalogada"):
        tool_registry._ensure_zone_admits(_seam(PHI_ZONE_GENERAL), frozenset({"fhir.nao_catalogada"}))


def test_fence_admits_non_phi_operations_for_the_general_zone() -> None:
    """Subtrativa e precisa: operacao catalogada sem `returns_phi` passa para a Zona Geral."""
    tool_registry._ensure_zone_admits(_seam(PHI_ZONE_GENERAL), frozenset({"dmn.evaluate"}))
