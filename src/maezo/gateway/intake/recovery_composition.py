"""Recovery factory for the actual existing intake stores and resource read policy.

Deployment must qualify this SAME intake database/authority's original scope.
Environment text alone cannot qualify historical rows or cross-environment imports.
"""

from collections.abc import Callable

from maezo.portal.api.session import HumanSessionResolver

from .models import IntakeError
from .native_composition import AuthIntakeComponents
from .recovery import IntakeRecoveryService
from .recovery_store import PostgresIntakeRecoveryStore, RecoveryScope


def compose_intake_recovery(
    components: AuthIntakeComponents, *, environment: str
) -> Callable[[HumanSessionResolver], IntakeRecoveryService]:
    if type(components) is not AuthIntakeComponents:
        raise IntakeError()
    store = PostgresIntakeRecoveryStore(
        components.admission,
        components.dispatch_store,
        RecoveryScope(tenant=components.admission.tenant, environment=environment),
    )

    def factory(resolver: HumanSessionResolver) -> IntakeRecoveryService:
        store.__post_init__()
        original = components.intake_factory(resolver)
        if original.store is not store.admission or original.authority is not components.authority:
            raise IntakeError()
        return IntakeRecoveryService(resolver, original.authority, store)

    return factory
