"""Exact public carriers; synthetic TISS shape grants no source/effect authority."""

from __future__ import annotations

from typing import Any

import pytest
from lxml import etree
from tests.unit.gateway.tiss.test_provider_schema import WS, XML

from maezo.gateway.tiss import Direction, Profile, ProviderSchemaValidator
from maezo.gateway.tiss import provider_schema as module


def validate(**updates: Any) -> module.ProviderSchemaResult:
    values: dict[str, Any] = {
        "payload": XML,
        "profile": Profile.XML,
        "direction": Direction.OPERATOR_TO_PROVIDER,
        "operation": "PROTOCOLO_RECEBIMENTO",
        "catalogue_version": module.CATALOGUE_VERSION,
    }
    values.update(updates)
    return ProviderSchemaValidator().validate(**values)


class HookString(str):
    def __new__(cls, value: str, hooks: list[str]) -> HookString:
        item = super().__new__(cls, value)
        item.hooks = hooks
        return item

    hooks: list[str]

    def __ne__(self, other: object) -> bool:
        self.hooks.append("ne")
        return False

    def __eq__(self, other: object) -> bool:
        self.hooks.append("eq")
        return True

    def __hash__(self) -> int:
        self.hooks.append("hash")
        return str.__hash__(self)

    def __str__(self) -> str:
        self.hooks.append("str")
        return str.__str__(self)


class HookBytes(bytes):
    def __new__(cls, value: bytes, hooks: list[str]) -> HookBytes:
        item = super().__new__(cls, value)
        item.hooks = hooks
        return item

    hooks: list[str]

    def __len__(self) -> int:
        self.hooks.append("len")
        return 1

    def __bool__(self) -> bool:
        self.hooks.append("bool")
        return True

    def __bytes__(self) -> bytes:
        self.hooks.append("bytes")
        raise AssertionError("carrier must not be coerced")


class ForgedEnumClass:
    def __init__(self, target: Any, hooks: list[str]) -> None:
        self.target, self.hooks = target, hooks

    @property  # type: ignore[misc]  # Intentional runtime introspection forgery.
    def __class__(self) -> Any:
        self.hooks.append("class")
        return self.target

    def __eq__(self, other: object) -> bool:
        self.hooks.append("eq")
        return True

    def __ne__(self, other: object) -> bool:
        self.hooks.append("ne")
        return False

    def __hash__(self) -> int:
        self.hooks.append("hash")
        return 0


def no_load_or_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unsafe carrier reached public-pin IO or native parser")

    monkeypatch.setattr(module, "_verified_files", denied)
    monkeypatch.setattr(etree, "fromstring", denied)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("operation", "PROTOCOLO_RECEBIMENTO", "UNSUPPORTED_OPERATION"),
        ("catalogue_version", "04.03.00", "UNSUPPORTED_CATALOGUE_VERSION"),
    ],
)
def test_str_subclasses_are_refused_before_comparison_hash_or_parse(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str, reason: str
) -> None:
    hooks: list[str] = []
    no_load_or_parse(monkeypatch)
    result = validate(**{field: HookString(value, hooks)})
    assert result.reasons == (reason,) and result.available is False
    assert result.schema_valid is False and result.xsd_valid is None
    assert hooks == []


@pytest.mark.parametrize("oversized", [False, True])
def test_bytes_subclasses_are_refused_before_len_bool_coercion_or_parse(
    monkeypatch: pytest.MonkeyPatch, oversized: bool
) -> None:
    hooks: list[str] = []
    raw = (
        XML.replace(b"<ans:cabecalho>", b"<!--" + b"X" * (module.MAX_BYTES + 32) + b"--><ans:cabecalho>")
        if oversized
        else XML
    )
    no_load_or_parse(monkeypatch)
    result = validate(payload=HookBytes(raw, hooks))
    assert result.reasons == ("PAYLOAD_SIZE_OR_TYPE_REFUSED",) and result.available is False
    assert result.schema_valid is False and result.xsd_valid is None
    assert hooks == []


@pytest.mark.parametrize(("field", "kind"), [("profile", Profile), ("direction", Direction)])
def test_forged_enum_class_is_refused_without_class_or_comparison_hooks(
    monkeypatch: pytest.MonkeyPatch, field: str, kind: Any
) -> None:
    hooks: list[str] = []
    no_load_or_parse(monkeypatch)
    result = validate(**{field: ForgedEnumClass(kind, hooks)})
    assert result.reasons == ("UNSUPPORTED_PROFILE_OR_DIRECTION",) and result.available is False
    assert hooks == []


@pytest.mark.parametrize(
    ("field", "member"), [("profile", Profile.XML), ("direction", Direction.OPERATOR_TO_PROVIDER)]
)
def test_exact_enum_type_without_canonical_member_identity_is_refused(
    monkeypatch: pytest.MonkeyPatch, field: str, member: Any
) -> None:
    forged = str.__new__(type(member), member.value)
    assert type(forged) is type(member) and forged is not member
    no_load_or_parse(monkeypatch)
    result = validate(**{field: forged})
    assert result.reasons == ("UNSUPPORTED_PROFILE_OR_DIRECTION",) and result.available is False


@pytest.mark.parametrize(("profile", "payload"), [(Profile.XML, XML), (Profile.WS_BODY, WS)])
def test_canonical_profile_direction_str_bytes_remain_genuine_shape_positive(
    profile: Profile, payload: bytes
) -> None:
    result = validate(profile=profile, payload=payload)
    assert result.available and result.schema_valid and result.xsd_valid
    assert result.reasons == () and result.graph_sha256 == module._GRAPHS[profile]


def test_original_transaction_swap_is_still_exact_structural_refusal() -> None:
    result = validate(payload=XML.replace(b"PROTOCOLO_RECEBIMENTO", b"RECURSO_GLOSA"))
    assert result.reasons == ("TRANSACTION_MISMATCH",)
    assert result.available and result.xsd_valid and not result.schema_valid


@pytest.mark.parametrize("extra", [0, 1])
def test_builtin_bytes_original_size_boundary_is_unchanged(extra: int) -> None:
    padding = module.MAX_BYTES - len(XML) - len(b"<!---->") + extra
    payload = XML.replace(b"<ans:cabecalho>", b"<!--" + b"X" * padding + b"--><ans:cabecalho>")
    assert type(payload) is bytes and len(payload) == module.MAX_BYTES + extra
    result = validate(payload=payload)
    if extra:
        assert result.reasons == ("PAYLOAD_SIZE_OR_TYPE_REFUSED",) and result.available is False
    else:
        assert result.available and result.schema_valid and result.xsd_valid and result.reasons == ()
