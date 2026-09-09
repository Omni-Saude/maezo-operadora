"""Static technical failures; upstream strings never become PHI-bearing API errors."""

from typing import Literal

RefusalCode = Literal[
    "authentication_unavailable",
    "authority_unavailable",
    "task_unavailable",
    "operation_forbidden",
    "revision_conflict",
    "form_projection_unavailable",
    "form_contract_unavailable",
    "admission_unavailable",
    "credential_scope_mismatch",
    "production_capabilities_unavailable",
]


class GatewayRefusalError(Exception):
    def __init__(self, code: RefusalCode) -> None:
        self.code = code
        super().__init__(code)
