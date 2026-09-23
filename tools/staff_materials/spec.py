"""Entrada fechada do `generate` (`staff-materials-spec.v1`).

Nenhum fato de autoridade tem default: escopo, hostname de D-B, logins, refs de fonte, janelas.
O que o loader do portal e o plugin Java exigem entre os campos e checado aqui, antes de gerar
um byte de chave, para que um rascunho que o aprovador nunca vai conseguir instalar nao chegue
ate ele:

* `identity_verifier.login_role` = login do DSN witness (`materials.py`, conferencia do papel);
* `publication_importer.source_ref` = `case_issuer.source_ref` (o engine confere o importador
  contra o `source_ref` de cada publicacao, `StaffCaseInstallation.signedPublication`);
* `case_issuer.source_namespace` = `policy_ref` da politica staff (o engine resolve o emissor da
  decisao por `entry(..., namespace=policy_ref)`, `StaffCaseInstallation.grant`);
* logins distintos em todas as entradas (`Designation.independent_entries`);
* janela da designacao de no maximo 14 dias (decisao N2 do dono, 23/09/2026).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from maezo.gateway.external_cases.models import Ref, Scope, timestamp
from maezo.gateway.staff_cases.models import Closed, N, T
from maezo.gateway.staff_cases.production_config import Connection, PortalStaffBootstrapError
from maezo.portal.engine.profile import strict_loads

from .pki import HOSTNAME
from .secure_io import MaterialError

MAX_DESIGNATION_WINDOW = timedelta(days=14)
MAX_CERTIFICATE_WINDOW = timedelta(days=397)


class RoleSpec(Closed):
    login_role: Ref
    source_namespace: Ref
    source_ref: Ref


class ConnectionSpec(Closed):
    host: str
    port: N
    database: str
    login: Ref

    def validated(self, ca_file: Literal["session-lock-ca.pem", "native-witness-ca.pem"]) -> Connection:
        return Connection(
            host=self.host,
            port=self.port,
            database=self.database,
            login=self.login,
            tls_server_name=self.host,
            ca_file=ca_file,
            function_pin=None,
        )


class DesignationSpec(Closed):
    designation_ref: Ref
    designation_revision: N
    expected_previous_revision: N
    authority_ref: Ref
    authority_revision: N
    not_before: T
    valid_until: T


class MaterialsSpec(Closed):
    schema_: Literal["staff-materials-spec.v1"] = Field(alias="schema")
    scope: Scope
    native_hostname: str
    certificate_not_after: T
    designation: DesignationSpec
    read_requester: RoleSpec
    identity_verifier: RoleSpec
    native_result: RoleSpec
    case_issuer: RoleSpec
    publication_importer: RoleSpec
    session_lock_connection: ConnectionSpec
    native_witness_connection: ConnectionSpec

    def roles(self) -> dict[str, RoleSpec]:
        return {
            "read_requester": self.read_requester,
            "identity_verifier": self.identity_verifier,
            "native_result": self.native_result,
            "case_issuer": self.case_issuer,
            "publication_importer": self.publication_importer,
        }

    @model_validator(mode="after")
    def coherent(self) -> Self:
        start = timestamp(self.designation.not_before)
        end = timestamp(self.designation.valid_until)
        certificate_end = timestamp(self.certificate_not_after)
        logins = [role.login_role for role in self.roles().values()]
        if not start < end or end - start > MAX_DESIGNATION_WINDOW:
            raise ValueError("janela da designacao invalida ou maior que 14 dias")
        if not end <= certificate_end or certificate_end - start > MAX_CERTIFICATE_WINDOW:
            raise ValueError("certificados precisam cobrir a designacao e durar no maximo 397 dias")
        if int(self.designation.designation_revision) != int(self.designation.expected_previous_revision) + 1:
            raise ValueError("sucessao de designacao invalida")
        if not HOSTNAME.fullmatch(self.native_hostname):
            raise ValueError("hostname nativo invalido")
        if len(set(logins)) != len(logins):
            raise ValueError("cada papel precisa de um login proprio")
        if self.identity_verifier.login_role != self.native_witness_connection.login:
            raise ValueError("o papel identity_verifier e o login do DSN witness")
        if self.publication_importer.source_ref != self.case_issuer.source_ref:
            raise ValueError("o importador publica a fonte do case_issuer")
        if self.session_lock_connection.login == self.native_witness_connection.login:
            raise ValueError("session-lock e witness sao logins diferentes")
        try:
            self.session_lock_connection.validated("session-lock-ca.pem")
            self.native_witness_connection.validated("native-witness-ca.pem")
        except (PortalStaffBootstrapError, ValueError):
            raise ValueError("conexao invalida") from None
        return self


def load_spec(raw: bytes) -> MaterialsSpec:
    try:
        return MaterialsSpec.model_validate(strict_loads(raw), strict=True)
    except Exception as failure:
        # O texto de validacao do pydantic cita o campo, nunca um segredo: o spec nao tem segredo.
        raise MaterialError(f"spec invalido: {failure}") from None
