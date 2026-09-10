"""Mechanical portal-auth-intake.v1 money codec; no clinical/ceiling policy.

Trace: approved E04 mechanical amendment v1. Legacy Double hydration is unchanged.
"""

import re
from dataclasses import dataclass
from typing import Any

_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{2}")
TYPE = "maezo-auth-exact-decimal.v1"


@dataclass(frozen=True, slots=True, repr=False)
class AuthExactAmount:
    cents: int

    def __post_init__(self) -> None:
        if type(self.cents) is not int or not 0 <= self.cents <= 2**63 - 1:
            raise ValueError("invalid AUTH exact amount")

    @property
    def decimal_text(self) -> str:
        whole, fraction = divmod(self.cents, 100)
        return f"{whole}.{fraction:02d}"

    @classmethod
    def parse(cls, value: object) -> "AuthExactAmount":
        if type(value) is not str or len(value) > 20 or not _PATTERN.fullmatch(value):
            raise ValueError("invalid AUTH exact amount")
        whole, fraction = value.split(".")
        return cls(int(whole) * 100 + int(fraction))


def hydrate_auth_amount(entry: dict[str, Any], *, input_profile: str) -> AuthExactAmount:
    """For the qualified native acquisition adapter only; profile text is no grant.

    The installed native definition/acquisition owner must select this codec after
    authenticated result verification. A caller/browser cannot select a profile.
    """
    if (
        input_profile != "portal-auth-intake.v1"
        or type(entry) is not dict
        or entry.keys() != {"type", "value"}
        or entry["type"] != TYPE
    ):
        raise ValueError("invalid AUTH amount wire")
    return AuthExactAmount.parse(entry["value"])
