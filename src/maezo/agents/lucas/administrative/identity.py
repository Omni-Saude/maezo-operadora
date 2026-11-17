"""Identidade do consumidor administrativo: o único ponto que nomeia o agente.

Parametriza o id de telemetria, as raízes de checkpoint, a versão do grafo do
driver completo e o conjunto de operações por tarefa. O topo de operações é
topologia candidata do ledger admitido, não concessão de autonomia.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from .handoff import ADMINISTRATIVE_TASKS, AdministrativeInputError

_AGENT_ID: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_PRINTABLE_TOKEN: Final[re.Pattern[str]] = re.compile(r"[\x21-\x7e]+\Z")
_ERROR: Final[str] = "administrative_identity_contract_mismatch"


def _token(value: object) -> bool:
    return type(value) is str and _PRINTABLE_TOKEN.match(value) is not None


@dataclass(frozen=True, slots=True)
class AdministrativeIdentity:
    """Identidade fechada; a validação nunca ecoa o valor recebido."""

    agent_id: str
    checkpoint_material_version: str
    checkpoint_thread_prefix: str
    full_runtime_graph_version: str
    full_runtime_thread_prefix: str
    operations_by_task: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        if (
            type(self.agent_id) is not str
            or _AGENT_ID.match(self.agent_id) is None
            or not _token(self.checkpoint_material_version)
            or not _token(self.checkpoint_thread_prefix)
            or not _token(self.full_runtime_graph_version)
            or not _token(self.full_runtime_thread_prefix)
            or not isinstance(self.operations_by_task, Mapping)
            or not self.operations_by_task
        ):
            raise AdministrativeInputError(_ERROR)
        frozen: dict[str, frozenset[str]] = {}
        for task, operations in self.operations_by_task.items():
            if (
                type(task) is not str
                or task not in ADMINISTRATIVE_TASKS
                or type(operations) is not frozenset
                or not operations
                or any(type(operation) is not str for operation in operations)
            ):
                raise AdministrativeInputError(_ERROR)
            frozen[task] = operations
        object.__setattr__(self, "operations_by_task", MappingProxyType(frozen))


_COMMON_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "access.resolve",
        "case.open_or_update",
        "external_wait.settle",
        "notice.prepare_or_send",
        "milestone.publish",
    }
)

LUCAS_ADMINISTRATIVE_IDENTITY: Final[AdministrativeIdentity] = AdministrativeIdentity(
    agent_id="lucas",
    checkpoint_material_version="lucas-administrative.proposed.v1",
    checkpoint_thread_prefix="lucas:administrative",
    full_runtime_graph_version="lucas-administrative-full-driver.proposed.v1",
    full_runtime_thread_prefix="lucas:administrative:full",
    operations_by_task={
        "journey.compras.step": _COMMON_OPERATIONS
        | frozenset(
            {
                "offer.compose",
                "acceptance.record",
                "enrollment.request",
                "reservation.command",
                "fulfillment.observe",
            }
        ),
        "journey.suporte.step": _COMMON_OPERATIONS | frozenset({"feedback.record"}),
    },
)
