"""R117/ADR-0019 fail-closed population policy, independent of autonomy shadow mode.

Trust boundary: canonical deployment artifact + separately authenticated human act bound
by SHA-256 to its complete bytes. Metadata in YAML is never authentication. The verifier
port has no production implementation: loading refuses until that boundary is supplied by
an independently reviewed composition. No environment/caller path or numeric override.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

POLICY_ID = "maezo.population-egress.v1"
CANONICAL_PATH = Path(__file__).resolve().parents[4] / "spec/policies/privacy/population-egress-v1.yaml"
# The verifier must authenticate the DPO/legal act and exact policy digest; names alone
# are insufficient. Deployment integration is deliberately absent, never a true stub.
RatificationVerifier = Callable[[str, str, str], bool]


class PopulationPolicyUnavailableError(ValueError):
    """Technical refusal; no aggregate or policy input is included in the error."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if type(key) is not str or key in result:
            raise PopulationPolicyUnavailableError("population_policy_invalid")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class PopulationPolicy:
    k_min: int
    allowed_metrics: frozenset[str]
    source_sha256: str
    policy_id: str = POLICY_ID

    def validate(self, aggregate: Any) -> None:
        """Validate the existing CohortAggregate contract before serving any result."""
        # Local import avoids making the canonical manifest loader import an agent graph.
        # A provider-controlled method/duck type cannot certify its own returned shape.
        from maezo.agents.andre.graph import CohortAggregate

        if (
            type(aggregate) is not CohortAggregate
            or type(getattr(aggregate, "cohort_id", None)) is not str
            or type(getattr(aggregate, "dataset_ref", None)) is not str
            or type(getattr(aggregate, "metrics", None)) is not dict
            or any(type(name) is not str for name in aggregate.metrics)
            or type(getattr(aggregate, "suppressed", None)) is not tuple
            or any(type(name) is not str for name in aggregate.suppressed)
            or type(getattr(aggregate, "cohort_size", None)) is not int
            or type(getattr(aggregate, "k_anonymity", None)) is not int
        ):
            raise PopulationPolicyUnavailableError("population_result_shape_refused")
        if (
            self.policy_id != POLICY_ID
            or aggregate.k_anonymity < self.k_min
            or aggregate.cohort_size < aggregate.k_anonymity
            or not set(aggregate.metrics) <= self.allowed_metrics
            or not set(aggregate.suppressed) <= self.allowed_metrics
            or any(
                type(v) not in (int, float) or (type(v) is float and not math.isfinite(v))
                for v in aggregate.metrics.values()
            )
            or CohortAggregate.has_resolvable_phi(aggregate)
        ):
            raise PopulationPolicyUnavailableError("population_result_refused")


def parse_verified_policy(raw: bytes, verifier: RatificationVerifier | None) -> PopulationPolicy:
    """Pure parser for canonical loader and synthetic fixtures; not a path override."""
    try:
        data = yaml.load(raw.decode("utf-8"), Loader=_UniqueLoader)
        if type(data) is not dict or set(data) != {
            "schema_version",
            "policy_id",
            "status",
            "k_min",
            "allowed_metrics",
            "ratificacao",
        }:
            raise ValueError
        rat = data["ratificacao"]
        if (
            type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or data["policy_id"] != POLICY_ID
            or data["status"] != "RATIFICADO"
            or type(data["k_min"]) is not int
            or data["k_min"] < 1
            or type(rat) is not dict
            or set(rat) != {"ratificado", "revisor", "ratificado_em", "evidence_ref"}
            or rat["ratificado"] is not True
            or any(
                type(rat[k]) is not str or not rat[k].strip()
                for k in ("revisor", "ratificado_em", "evidence_ref")
            )
            or type(data["allowed_metrics"]) is not list
            or not data["allowed_metrics"]
            or any(type(k) is not str or not k.strip() for k in data["allowed_metrics"])
            or len(set(data["allowed_metrics"])) != len(data["allowed_metrics"])
        ):
            raise ValueError
        if date.fromisoformat(rat["ratificado_em"]).isoformat() != rat["ratificado_em"]:
            raise ValueError
        digest = hashlib.sha256(raw).hexdigest()
        if verifier is None or verifier(POLICY_ID, digest, rat["evidence_ref"]) is not True:
            raise ValueError
        return PopulationPolicy(data["k_min"], frozenset(data["allowed_metrics"]), digest)
    except (ValueError, TypeError, KeyError, yaml.YAMLError, RecursionError):
        raise PopulationPolicyUnavailableError("population_policy_unavailable") from None


def load_population_policy() -> PopulationPolicy:
    """Canonical only. No human-act verifier has been provisioned; refusal is intentional."""
    try:
        raw = CANONICAL_PATH.read_bytes()
    except OSError:
        raise PopulationPolicyUnavailableError("population_policy_unavailable") from None
    return parse_verified_policy(raw, verifier=None)
