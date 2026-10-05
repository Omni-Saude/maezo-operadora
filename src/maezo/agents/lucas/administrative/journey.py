"""Candidate complete Compras/Suporte entry points over the same DUR2 driver.

No production ingress is mounted; these factories do not invoke the one-OP
graph or install actor/authority/source providers, checkpoints or policy grants.
"""

from __future__ import annotations

from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyContractError
from maezo.gateway.capabilities.journeys.driver import JourneyDriver


def compras_journey(binding: JourneyBinding, driver: JourneyDriver | None = None) -> JourneyDriver:
    if binding.task_ref != "journey.compras.step":
        raise JourneyContractError()
    if driver is not None:
        if driver.binding != binding:
            raise JourneyContractError()
        return driver
    return JourneyDriver(binding)


def suporte_journey(binding: JourneyBinding, driver: JourneyDriver | None = None) -> JourneyDriver:
    if binding.task_ref != "journey.suporte.step":
        raise JourneyContractError()
    if driver is not None:
        if driver.binding != binding:
            raise JourneyContractError()
        return driver
    return JourneyDriver(binding)
