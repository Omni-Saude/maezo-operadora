"""Cercas fail-closed do executor de dev (decisao do dono de 24/09/2026).

Dado sintetico so na faixa de teste do dev: conta AWS 203312548462, ambiente `dev`, tenant `amh`,
guia `^SYN-[A-Z0-9]+$`. Nunca staging/prod. Cada valor tem de bater EXATAMENTE; ausente = recusa.
"""

from __future__ import annotations

from collections.abc import Mapping

from .core import GUIDE_PATTERN, SyntheticRefusedError

DEV_ACCOUNT_ID = "203312548462"
DEV_ENVIRONMENT = "dev"
DEV_TENANT = "amh"
#: Variaveis de ambiente EXPLICITAS do operador (nao sao segredo).
ACCOUNT_ENV = "MAEZO_DEV_SYN_AWS_ACCOUNT_ID"
ENVIRONMENT_ENV = "MAEZO_DEV_SYN_ENVIRONMENT"


class DevGuardError(SyntheticRefusedError):
    """O alvo nao e a faixa sintetica do dev: nada e escrito."""


def check_guide(guide_number: object) -> str:
    if not isinstance(guide_number, str) or not GUIDE_PATTERN.fullmatch(guide_number):
        raise DevGuardError(f"guia fora de {GUIDE_PATTERN.pattern}: {guide_number!r}")
    return guide_number


def check_target(
    env: Mapping[str, str], *, environment: object, tenant: object, guide_number: object
) -> None:
    """Ambiente (env E configuracao) = `dev`, tenant = `amh`, conta explicita = 203312548462, guia `SYN-`."""
    account = env.get(ACCOUNT_ENV)
    if account != DEV_ACCOUNT_ID:
        raise DevGuardError(f"{ACCOUNT_ENV}={account!r}: so a conta {DEV_ACCOUNT_ID} (dev)")
    declared = env.get(ENVIRONMENT_ENV)
    if declared != DEV_ENVIRONMENT or environment != DEV_ENVIRONMENT:
        raise DevGuardError(f"ambiente {declared!r}/{environment!r}: so {DEV_ENVIRONMENT!r}")
    if tenant != DEV_TENANT:
        raise DevGuardError(f"tenant {tenant!r}: so {DEV_TENANT!r}")
    check_guide(guide_number)
