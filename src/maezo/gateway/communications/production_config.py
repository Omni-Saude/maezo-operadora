"""Closed production inputs for the PHI communication-content read service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import CommunicationScope

Ref = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Role = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]

MATERIAL_PARENT = "/run/maezo-phi-communication-materials"
MATERIAL_DIRECTORY = MATERIAL_PARENT + "/current"
SCRATCH_DIRECTORY = "/run/maezo-phi-communication-scratch"
MAX_BUNDLE = 131072
PRIVATE_FILES = frozenset(
    {
        "identity-reader-dsn.txt",
        "content-reader-dsn.txt",
        "authority-reader-dsn.txt",
        "identity-owner-client-key.pem",
        "content-keys.json",
    }
)
PUBLIC_FILES = frozenset(
    {
        "identity-reader-ca.pem",
        "content-reader-ca.pem",
        "authority-reader-ca.pem",
        "identity-owner-ca.pem",
        "identity-owner-client-certificate.pem",
    }
)
FILES = PRIVATE_FILES | PUBLIC_FILES


class PhiProductionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("phi_communication_production_unavailable")


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
    )


def fixed_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or value.rstrip("/") != f"https://{parsed.netloc}"
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise PhiProductionError()
    return value.rstrip("/")


class ObjectPin(Closed):
    schema_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    oid: int = Field(gt=0)
    owner: Role
    kind: Literal["r", "p", "v"]
    definition_sha256: Digest | None = None

    @model_validator(mode="after")
    def definition_matches_kind(self) -> Self:
        if (self.kind == "v") != (self.definition_sha256 is not None):
            raise PhiProductionError()
        return self

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"


class RegisteredDatabase(Closed):
    identity_source_ref: Ref
    system_ref: Ref
    database_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    database_oid: int = Field(gt=0)
    database_incarnation: Ref
    registration_receipt_ref: Ref
    registration_digest: Digest
    objects: tuple[ObjectPin, ...]

    @model_validator(mode="after")
    def exact_objects(self) -> Self:
        names = tuple(pin.qualified_name for pin in self.objects)
        required = {
            "public.portal_sessions",
            "public.portal_memberships",
            "portal_communication.content",
            "portal_communication.message",
            "portal_communication.intended_recipient",
            "portal_communication.inbox",
            "portal_communication.authority_head",
            "portal_communication.authority_publication",
        }
        if set(names) != required or len(names) != len(required):
            raise PhiProductionError()
        return self


class ReaderProfile(Closed):
    login: Role
    host: str
    port: int = Field(ge=1, le=65535)
    tls_server_name: str
    dsn_file: Literal[
        "identity-reader-dsn.txt",
        "content-reader-dsn.txt",
        "authority-reader-dsn.txt",
    ]
    ca_file: Literal[
        "identity-reader-ca.pem",
        "content-reader-ca.pem",
        "authority-reader-ca.pem",
    ]
    objects: tuple[str, ...]
    privileges: tuple[Literal["SELECT"], ...] = ("SELECT",)

    @model_validator(mode="after")
    def exact_profile(self) -> Self:
        if (
            not self.objects
            or tuple(sorted(set(self.objects))) != self.objects
            or self.host != self.tls_server_name
            or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host)
            or ".." in self.host
        ):
            raise PhiProductionError()
        return self


class IdentityOwnerProfile(Closed):
    origin: str
    purpose: Literal["identity-mismatch-revocation.v1"]
    identity_source_ref: Ref
    requester_spki_sha256: Digest
    server_spki_sha256: Digest
    ca_file: Literal["identity-owner-ca.pem"]
    certificate_file: Literal["identity-owner-client-certificate.pem"]
    private_key_file: Literal["identity-owner-client-key.pem"]
    valid_until: datetime

    @model_validator(mode="after")
    def fixed_endpoint(self) -> Self:
        fixed_origin(self.origin)
        return self

    @field_validator("valid_until")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise PhiProductionError()
        return value


class ContentKeyStatus(Closed):
    source_ref: Ref
    source_revision: str = Field(pattern=r"^(0|[1-9][0-9]*)$")
    source_digest: Digest
    source_receipt_ref: Ref
    observed_at: datetime
    valid_until: datetime
    active_key_id: Ref
    key_ids: tuple[Ref, ...]

    @model_validator(mode="after")
    def current_snapshot_shape(self) -> Self:
        if (
            self.observed_at >= self.valid_until
            or tuple(sorted(set(self.key_ids))) != self.key_ids
            or self.active_key_id not in self.key_ids
        ):
            raise PhiProductionError()
        return self

    @field_validator("observed_at", "valid_until")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise PhiProductionError()
        return value


class PhiPublicManifest(Closed):
    schema_: Literal["portal-phi-communications-material.v1"] = Field(alias="schema")
    profile: Literal["content-read"]
    material_version_id: str = Field(pattern=r"^[A-Za-z0-9-]{32,64}$")
    scope: CommunicationScope
    issuer: str
    public_origin: str
    portal_settings_ref: Ref
    portal_settings_digest: Digest
    issued_at: datetime
    valid_until: datetime
    registered_database: RegisteredDatabase
    identity_reader: ReaderProfile
    content_reader: ReaderProfile
    authority_reader: ReaderProfile
    identity_owner: IdentityOwnerProfile
    content_keys: ContentKeyStatus
    files: dict[str, Digest | None]

    @model_validator(mode="after")
    def exact_manifest(self) -> Self:
        fixed_origin(self.public_origin)
        database = {pin.qualified_name for pin in self.registered_database.objects}
        expected = {
            self.identity_reader.dsn_file: {
                "public.portal_memberships",
                "public.portal_sessions",
            },
            self.content_reader.dsn_file: {
                "portal_communication.content",
                "portal_communication.inbox",
                "portal_communication.intended_recipient",
                "portal_communication.message",
            },
            self.authority_reader.dsn_file: {
                "portal_communication.authority_head",
                "portal_communication.authority_publication",
            },
        }
        profiles = (self.identity_reader, self.content_reader, self.authority_reader)
        if (
            self.issued_at >= self.valid_until
            or len({profile.login for profile in profiles}) != 3
            or len({profile.dsn_file for profile in profiles}) != 3
            or any(set(profile.objects) != expected[profile.dsn_file] for profile in profiles)
            or any(not set(profile.objects).issubset(database) for profile in profiles)
            or self.identity_owner.identity_source_ref != self.registered_database.identity_source_ref
            or set(self.files) != FILES
            or any(self.files[name] is None for name in PUBLIC_FILES)
            or any(self.files[name] is not None for name in PRIVATE_FILES)
        ):
            raise PhiProductionError()
        return self

    @field_validator("issued_at", "valid_until")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise PhiProductionError()
        return value


class PhiProductionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAEZO_PHI_COMMUNICATION_",
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    capabilities: Literal["content-read"]
    material_directory: Literal["/run/maezo-phi-communication-materials/current"]
    material_version_id: str = Field(pattern=r"^[A-Za-z0-9-]{32,64}$")
    public_manifest_sha256: Digest
    registered_database_digest: Digest
    identity_source_ref: Ref
    key_source_digest: Digest
    maximum_seconds: int = Field(ge=1, le=10)


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
        raise PhiProductionError()
    return value
