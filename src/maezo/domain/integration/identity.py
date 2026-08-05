"""Portable identity and tenant value objects for the payer core (ADR-0037 XRD-05, MZO-020).

**The defect this module removes.** Tenant identity in this repository is a bare `str` threaded as a
keyword argument, and TWO spellings are in use for DIFFERENT things: `tenant` (`maezo.a2a.card`,
`maezo.a2a.facts`, `maezo.a2a.delegation`, `maezo.a2a.registry`, `maezo.a2a.idempotency`,
`maezo.gateway.pep`) and `tenant_id` (`maezo.gateway.pseudonymizer`, `maezo.gateway.log_scrubber`,
`maezo.tools.workers.*`). Neither spelling says WHICH of ADR-0037's five separate axes it means, so
nothing stops a source instance identifier, a legal entity and a paying company from being written
into the same slot. `str` is the wrong type for a value whose scope is a safety property.

**The five axes ADR-0037 keeps apart, and where each lives.**

    deployment cell          NOT modelled here — see "Deliberately absent" below (XRD-08/XRD-11)
    company tenant           `CompanyTenantRef`      (pinned envelope field `amh_tenant`)
    legal entity             `LegalEntityRef`        (pinned envelope field `legal_entity`)
    source instance          `SourceProvenanceRef`   (pinned `source_instance`/`source_tenant`)
    CIB process namespace    `WorkflowBusinessRef` + `payer_process_definition_key`
    the subject              `PortableSubjectRef`    (pinned `portable_subject_ref`)

`PortableSubjectRef` is scoped by `LegalEntityRef`, which is scoped by `CompanyTenantRef`, because
XRD-05 states the AMH-minted subject reference is "opaco e estável dentro de `{amh_tenant,
legal_entity}`" — the pair IS the scope, so the type carries it rather than trusting a caller to
remember it. `SourceProvenanceRef` carries the SOURCE's own tenant, which is a third, unrelated
tenant axis: it is provenance and nothing else, and it deliberately offers no scoping surface.

**Opaque, SHAPE-only validation — never parsed for meaning.** Every component is validated for
emptiness, whitespace, control characters, length bounds and a conservative ASCII charset, and for
NOTHING else. No component is split, decoded, prefix-matched for meaning, or read as carrying
information. There is no MPI/source/FHIR parsing logic here, no vendor vocabulary, and no
infrastructure import — the module is standard-library-only, enforced by an AST fence in
`tests/unit/domain/integration/test_identity.py` (the same technique as
`tests/unit/ports/test_ports_purity.py`).

**Four recognisable raw-identifier shapes are refused by name, and the charset bounds the rest.**
Read this section as exactly that claim, and NOT as an absolute non-representability claim — an
earlier revision of this docstring headed it that way and was wrong. `PortableSubjectRef` wraps the
AMH-minted opaque reference and nothing else; only the AMH maps it to a source record, a beneficiary,
a per-tenant MPI or a FHIR identifier (XRD-05).

What this module GUARANTEES is two things. (a) These four RAW identifier shapes are refused by name,
as `ForbiddenRawIdentifierError` and never as a charset complaint, so a caller reading the error
learns WHY the value is unwelcome rather than being nudged to re-encode it:

    1. a bare CPF-like 11-digit run  (also in its `.`/`_`/`-`/`/`/space-punctuated forms)
    2. a bare CNS-like 15-digit run  (likewise)
    3. a FHIR resource reference such as `Patient/<id>`
    4. a `fhir:` URI

(b) `PERMITTED_CHARACTERS` is the real bound on the space of representable values, and it is the
narrow half of this boundary: no `:`, no `/`, no whitespace, no control character, no non-ASCII code
point.

What this module does NOT guarantee, stated plainly because a DPO/Legal reader may quote this
paragraph: the digit-run rules match the EXACT length of the WHOLE compacted value (see
`_RAW_NUMERIC_ID_LABELS`), so an 11-digit run carrying any affix, or a run of another length, IS
representable. `subject-12345678901`, `id_12345678901`, `cpf12345678901`, `012345678901`,
`123456789010`, `Patient_abc123` and `Patient-abc123` all construct successfully today. That
trade-off is deliberate and is the right one: an "any embedded 11-digit run" rule would reject
roughly a fifth of legitimate 32-hex opaque references by chance, and a check that fires on plausible
opaque values is a check that gets switched off within a week. Keeping raw identifiers out of the
payer core is the AMH boundary's job (XRD-04/XRD-05); these four shapes are the FLOOR that catches
what a well-meaning adapter would otherwise pass through silently, not a completeness claim.

Naming `Patient` and `fhir:` in a rejection list is not FHIR vocabulary in the sense ADR-0037
prohibition 2 bans: nothing here can construct, read or interpret a FHIR reference — the names exist
only so the refusal can state its reason.

**Cross-company inequality is a safety property.** Two references with the same local value under
different company tenants are DIFFERENT values: they never compare equal, never hash equal, and a
business key composed for one company never validates as belonging to another. That is why the
scoped types carry their scope as a FIELD (inside `__eq__`/`__hash__`) rather than as context a
caller passes alongside, and why no type defines `__str__`: an f-string of a scoped reference must
never silently collapse to its unscoped local value.

**Serialization follows the repo's one canonical recipe, and invents no wire format.**
`canonical_bytes()` is `json.dumps(mapping, sort_keys=True, separators=(",", ":"))` encoded UTF-8 —
byte-for-byte the recipe already used by `maezo.a2a.card.AgentCard.signing_payload`,
`maezo.a2a.facts.DelegationFact.to_value` and the contract pin's own
`envelope.payload_hash_canonicalization`. Every key of `as_canonical_mapping()` is a name published
by ADR-0037 itself — `company_tenant_ref`, `workflow_type` and `workflow_business_ref` from immutable
prohibition 6; `legal_entity`, `portable_subject_ref`, `source_instance` and `source_tenant` from the
frozen envelope baseline pinned in `config/integrations/amh/contracts.lock.json`. This mapping is a
LOCAL canonical form for round-tripping and hashing; the wire schema is AMH-owned and lives nowhere
in this repository (XRD-04).

**Business keys and process keys (immutable prohibition 6, verbatim).** CIB business keys are opaque,
in the shape `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`, and process-definition
keys begin with `maezo-payer-`. `:` is excluded from the permitted charset, so the composed key has
exactly two separators by construction — a fact the tests pin, and the reason ownership can be
decided by RECOMPOSING and comparing (`WorkflowBusinessRef.owns_business_key`) instead of splitting
a key apart.

**Errors never echo the offending value.** Prohibition 5 forbids PHI and raw source identifiers in
keys, logs, traces, metrics and quarantine metadata — and an exception message becomes all of those.
So every message here names the component, the rule and at most single-character metadata (one code
point, one index, one length); it never contains the rejected value. This is deliberate: an error
that helpfully quoted a rejected CPF would defeat the check that produced it.

**Deliberately absent.** (a) The DEPLOYMENT CELL. The isolated payer cell of XRD-08/XRD-11 is a
deployment fact — engine, database, credentials, worker topics — not a domain value; giving it a
value object would invite it to be used as a scope, which is the ambiguity this module exists to
remove. The tests assert no type here carries a cell-shaped field. (b) `source_vendor`/
`source_product`: their values are a closed vendor vocabulary (`tasy_hospital`,
`tasy_healthcare_plan`), which prohibition 2 keeps out of the payer core. (c) Any accessor that turns
a composed key back into components. (d) Any conversion from a raw identifier — there is no such
constructor, in any form.

**Additive only.** These types are introduced as vocabulary; NO existing call site is migrated in
this change. The dual-read migration ADR-0037 anticipates is a separate, sequenced piece of work.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Final, Self

# --------------------------------------------------------------------------------------------------
# Shape rules. Conservative on purpose: every rule below is about the SHAPE of a string, and none of
# them reads a component as carrying information.
# --------------------------------------------------------------------------------------------------

MIN_COMPONENT_LENGTH: Final[int] = 2
"""Conservative floor. Not a semantic rule — a one-character reference is not *meaningfully* wrong,
it is merely implausible for an AMH-minted value, and a floor that rejects nothing is not a bound."""

MAX_COMPONENT_LENGTH: Final[int] = 128
"""Conservative ceiling. Bounds the composed business key (3 components + 2 separators) too, so no
downstream key store can be handed an unbounded string by a caller of this module."""

PERMITTED_CHARACTERS: Final[frozenset[str]] = frozenset(string.ascii_letters + string.digits + "._-")
"""ASCII alphanumerics plus `.`, `_` and `-`. NOTE what is excluded and why it matters: `:` (so the
prohibition-6 business key has exactly two separators by construction), `/` (so no path- or
FHIR-reference-shaped value is representable), and every non-ASCII code point (so no homoglyph makes
two distinct tenants look identical in a log line)."""

BUSINESS_KEY_SEPARATOR: Final[str] = ":"
"""The separator of immutable prohibition 6's `{company_tenant_ref}:{workflow_type}:
{workflow_business_ref}`. Excluded from `PERMITTED_CHARACTERS`, so it cannot occur inside a
component."""

PAYER_PROCESS_DEFINITION_KEY_PREFIX: Final[str] = "maezo-payer-"
"""Immutable prohibition 6, verbatim: "process-definition keys começam com `maezo-payer-`" — the CIB
process namespace of the payer cell (XRD-08), which is NOT the company tenant and never derived from
one."""

# Recognisable RAW identifier shapes, refused by name. Ordered most-specific first so the reason a
# caller sees is the most informative one available.
_FHIR_URI_SCHEME: Final[re.Pattern[str]] = re.compile(r"\Afhir:", re.IGNORECASE)

# Searched, not anchored, so `https://host/fhir/Patient/123` is caught as well as `Patient/123`.
# Case-insensitive because a sloppy caller writing `patient/123` is making exactly the mistake this
# check exists to name. Every value that matches would also fail the charset rule (`/` is excluded);
# this pattern's only job is to make the REASON accurate.
#
# The lead-in is a negative lookbehind on alphanumerics rather than `(?:\A|[/:])`, so any
# non-alphanumeric lead-in (`.Patient/abc`, `-Patient/abc`, `_Patient/abc`) also gets the accurate
# reason instead of a vaguer charset complaint, while a mid-word occurrence (`xPatient/abc`) still
# does not — `xPatient` is a word, not a resource name. This can only change which REASON a rejection
# carries, never whether it happens: `/` is outside `PERMITTED_CHARACTERS`, so every string this
# pattern can match is refused either way.
_FHIR_RESOURCE_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])(?:Patient|Person|RelatedPerson|Practitioner|Coverage|Encounter)/\S+",
    re.IGNORECASE,
)

# `.`, `_`, `-`, `/` and whitespace are the separators a punctuated national identifier is written
# with (`123.456.789-01`). Stripped before the digit-run test so the punctuated forms are caught too.
# `_` belongs here for one reason: it is a member of `PERMITTED_CHARACTERS`, so it is the only
# plausible group separator a caller can actually get PAST the charset rule. Leaving it unstripped
# let `123_456_789_01` and `123_4567_8901_2345` through every component slot.
#
# ACCEPTED COST, stated rather than discovered later: stripping a separator means any value whose
# compacted form is exactly 11 or 15 digits is refused, even when it was never an identifier —
# `2026_0000001` and `12345_678901` are refused as CPF-like. This is symmetric with the `.`/`-`
# forms, which behaved this way before `_` joined them (`2026.0000001` was already refused), and it
# is the unavoidable price of catching the punctuated identifier. A caller that needs grouped
# digits in a reference must group them to a length that is not 11 or 15, or not group them.
_DIGIT_GROUP_NOISE: Final[re.Pattern[str]] = re.compile(r"[._\-/\s]")

_RAW_NUMERIC_ID_LABELS: Final[dict[int, str]] = {11: "CPF-like", 15: "CNS-like"}
"""Digit-run lengths refused outright. Deliberately EXACT lengths of the WHOLE value rather than a
search for an embedded run: an embedded-run rule would reject roughly a fifth of legitimate 32-hex
opaque references by chance, which would make the check a nuisance instead of a guarantee."""


class IdentityShapeError(ValueError):
    """An identity component failed SHAPE validation (ADR-0037 XRD-05).

    A `ValueError`, so a `__post_init__` refusal reads the way callers already expect a bad value to
    read. Messages NEVER contain the rejected value — see the module docstring.
    """


class ForbiddenRawIdentifierError(IdentityShapeError):
    """The value has the shape of a RAW patient/beneficiary/MPI/source-record identifier.

    Distinct from a charset complaint on purpose: the value is refused for what it IS, not for which
    characters it happens to use. No raw identifier enters the payer domain (ADR-0037 XRD-05 and
    immutable prohibition 5) — the payer core holds ONLY the AMH-minted opaque reference.
    """


def _forbidden_raw_identifier_reason(value: str) -> str | None:
    """The reason `value` is a recognisable RAW identifier, or `None` if it is not one."""
    if _FHIR_URI_SCHEME.search(value):
        return "a `fhir:` URI — a raw FHIR identifier, which only the AMH may hold"
    if _FHIR_RESOURCE_REFERENCE.search(value):
        return "a FHIR resource reference in the shape `Patient/<id>`"
    compact = _DIGIT_GROUP_NOISE.sub("", value)
    label = _RAW_NUMERIC_ID_LABELS.get(len(compact))
    if label is not None and compact.isdigit():
        return f"a bare {label} {len(compact)}-digit national identifier"
    return None


def _first_control_character(value: str) -> tuple[int, str] | None:
    for index, char in enumerate(value):
        code = ord(char)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            return index, char
    return None


def _first_charset_offender(value: str) -> tuple[int, str] | None:
    for index, char in enumerate(value):
        if char not in PERMITTED_CHARACTERS:
            return index, char
    return None


def _validate_component(value: str, *, component: str) -> None:
    """Validate ONE opaque component for SHAPE, and for nothing else.

    Raises `ForbiddenRawIdentifierError` before any charset complaint, so a raw identifier is always
    reported as a raw identifier. Never echoes `value`.
    """
    if not isinstance(value, str):
        raise IdentityShapeError(
            f"{component} must be a str, got {type(value).__name__} — an identity component is a "
            "value object's opaque payload, not an arbitrary object"
        )
    if not value or not value.strip():
        raise IdentityShapeError(f"{component} must not be empty or whitespace-only")

    reason = _forbidden_raw_identifier_reason(value)
    if reason is not None:
        raise ForbiddenRawIdentifierError(
            f"{component} refuses a FORBIDDEN RAW-ID FORMAT: the value is {reason}. ADR-0037 "
            "XRD-05 — no raw patient/beneficiary/MPI/source-record identifier may enter the payer "
            "domain; the payer core holds ONLY the AMH-minted opaque reference. This is NOT a "
            "charset complaint and re-encoding the value will not satisfy it: mint an opaque "
            "reference at the AMH instead. The rejected value is withheld from this message on "
            "purpose (immutable prohibition 5 — no raw source id in logs or traces)."
        )

    # Control characters BEFORE the whitespace rules: several C0/C1 code points (`\t`, `\x1f`,
    # `\x85`, ...) satisfy `str.isspace()`, so a whitespace-first order would report the vaguer rule
    # for the more dangerous input — and a control character smuggled into a business key is a log
    # injection and a key-collision hazard, not a formatting slip.
    control = _first_control_character(value)
    if control is not None:
        index, char = control
        raise IdentityShapeError(
            f"{component} must not contain control characters (first offender U+{ord(char):04X} at "
            f"index {index})"
        )
    if value != value.strip():
        raise IdentityShapeError(f"{component} must not have leading or trailing whitespace")
    if any(char.isspace() for char in value):
        raise IdentityShapeError(f"{component} must not contain whitespace")
    if not MIN_COMPONENT_LENGTH <= len(value) <= MAX_COMPONENT_LENGTH:
        raise IdentityShapeError(
            f"{component} length {len(value)} is outside the permitted bounds "
            f"[{MIN_COMPONENT_LENGTH}, {MAX_COMPONENT_LENGTH}]"
        )
    offender = _first_charset_offender(value)
    if offender is not None:
        index, char = offender
        raise IdentityShapeError(
            f"{component} contains a character outside the permitted charset "
            f"[A-Za-z0-9._-] (first offender U+{ord(char):04X} at index {index})"
        )
    if not (value[0].isascii() and value[0].isalnum() and value[-1].isascii() and value[-1].isalnum()):
        raise IdentityShapeError(
            f"{component} must start and end with an ASCII alphanumeric character (no leading or "
            "trailing `.`, `_` or `-`)"
        )


def _require_scope(scope: object, *, component: str, expected: type[object]) -> None:
    """Refuse a bare `str` (or anything else) where a typed scope is required.

    This is the runtime half of the fix: passing a bare tenant string is exactly the defect MZO-020
    removes, and `mypy --strict` only covers `src/maezo`. An adapter decoding untyped wire data must
    fail loudly here rather than quietly build an unscoped reference.
    """
    if not isinstance(scope, expected):
        raise IdentityShapeError(
            f"{component} must be a {expected.__name__}, got {type(scope).__name__} — a scope is a "
            "typed value object, never a bare identifier string"
        )


def _canonical_bytes(mapping: Mapping[str, str]) -> bytes:
    """The repo's ONE canonical recipe: UTF-8, sorted keys, compact separators."""
    return json.dumps(dict(mapping), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _exact_keys(mapping: Mapping[str, str], expected: tuple[str, ...]) -> None:
    """Fail closed on a canonical mapping whose key set is not EXACTLY `expected`.

    A missing key would silently drop a scope; an unexpected key means the caller is holding a
    different shape than it thinks. Neither is a round trip.

    The refusal message names only THIS MODULE'S OWN key constants and counts. It never echoes an
    unexpected key NAME: `from_canonical_mapping` is public API over an untrusted `Mapping`,
    CPF-keyed dicts are a real shape in Brazilian source systems, and under immutable prohibition 5
    an exception message is a log line, a trace and a metric label. The same reasoning that keeps a
    rejected VALUE out of a message keeps a caller-supplied KEY out of it.

    A non-`Mapping` is refused here too, as `IdentityShapeError` rather than as a bare `TypeError`
    escaping from `set()` or `[]`, so a caller guarding a deserialization boundary with
    `except IdentityShapeError` actually catches it.
    """
    if not isinstance(mapping, Mapping):
        raise IdentityShapeError(
            f"canonical mapping must be a Mapping, got {type(mapping).__name__} — a canonical form "
            "is a keyed mapping, not an arbitrary object"
        )
    present = set(mapping)
    required = set(expected)
    if present == required:
        return
    missing = sorted(required - present)
    unexpected = len(present - required)
    raise IdentityShapeError(
        f"canonical mapping keys do not match the required keys {sorted(required)}: "
        f"{len(missing)} missing ({missing}) and {unexpected} unexpected. The unexpected key NAMES "
        "are withheld from this message on purpose — a canonical mapping arrives from an untrusted "
        "caller, so an unexpected key is caller-controlled text and may itself be a raw identifier "
        "(immutable prohibition 5 — no raw source id in keys, logs, traces or metrics)."
    )


@dataclass(frozen=True, slots=True)
class CompanyTenantRef:
    """The paying company/operator tenant — the pinned envelope's `amh_tenant`.

    The ROOT of every scope in this module and the first component of a prohibition-6 business key.
    Single-field, so it takes its value positionally; the multi-field types below are keyword-only
    because transposing a company tenant with a legal entity is precisely the silent failure this
    package exists to prevent.
    """

    value: str

    CANONICAL_KEYS: ClassVar[tuple[str, ...]] = ("company_tenant_ref",)

    def __post_init__(self) -> None:
        _validate_component(self.value, component="company_tenant_ref")

    def as_canonical_mapping(self) -> dict[str, str]:
        return {"company_tenant_ref": self.value}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_canonical_mapping())

    @classmethod
    def from_canonical_mapping(cls, mapping: Mapping[str, str]) -> Self:
        _exact_keys(mapping, cls.CANONICAL_KEYS)
        return cls(mapping["company_tenant_ref"])


@dataclass(frozen=True, slots=True, kw_only=True)
class LegalEntityRef:
    """A legal entity WITHIN one company tenant — the pinned envelope's `legal_entity`.

    Carries its `company_tenant` as a FIELD, so two legal entities with the same local value under
    different companies are different values in `==`, in `hash()`, and in every set or dict built
    from them.
    """

    company_tenant: CompanyTenantRef
    value: str

    CANONICAL_KEYS: ClassVar[tuple[str, ...]] = ("company_tenant_ref", "legal_entity")

    def __post_init__(self) -> None:
        _require_scope(self.company_tenant, component="company_tenant", expected=CompanyTenantRef)
        _validate_component(self.value, component="legal_entity")

    def as_canonical_mapping(self) -> dict[str, str]:
        return {"company_tenant_ref": self.company_tenant.value, "legal_entity": self.value}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_canonical_mapping())

    @classmethod
    def from_canonical_mapping(cls, mapping: Mapping[str, str]) -> Self:
        _exact_keys(mapping, cls.CANONICAL_KEYS)
        return cls(
            company_tenant=CompanyTenantRef(mapping["company_tenant_ref"]),
            value=mapping["legal_entity"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PortableSubjectRef:
    """The AMH-minted opaque subject reference — THE subject identity in the payer core (XRD-05).

    Scoped by `LegalEntityRef` because XRD-05 says the reference is stable "dentro de `{amh_tenant,
    legal_entity}`": the pair is the scope, so the type holds it instead of trusting each call site
    to carry it. `company_tenant` is exposed as a derived property, never as a second field, so the
    two can never disagree.

    Wraps the AMH-minted reference and NOTHING else. There is no constructor, classmethod or helper
    anywhere in this module that accepts a raw patient, beneficiary, MPI or source-record identifier,
    and the four recognisable raw shapes are refused outright.
    """

    legal_entity: LegalEntityRef
    value: str

    CANONICAL_KEYS: ClassVar[tuple[str, ...]] = (
        "company_tenant_ref",
        "legal_entity",
        "portable_subject_ref",
    )

    def __post_init__(self) -> None:
        _require_scope(self.legal_entity, component="legal_entity", expected=LegalEntityRef)
        _validate_component(self.value, component="portable_subject_ref")

    @property
    def company_tenant(self) -> CompanyTenantRef:
        """The company tenant this subject reference is scoped to, via its legal entity."""
        return self.legal_entity.company_tenant

    def as_canonical_mapping(self) -> dict[str, str]:
        return {
            "company_tenant_ref": self.legal_entity.company_tenant.value,
            "legal_entity": self.legal_entity.value,
            "portable_subject_ref": self.value,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_canonical_mapping())

    @classmethod
    def from_canonical_mapping(cls, mapping: Mapping[str, str]) -> Self:
        _exact_keys(mapping, cls.CANONICAL_KEYS)
        return cls(
            legal_entity=LegalEntityRef(
                company_tenant=CompanyTenantRef(mapping["company_tenant_ref"]),
                value=mapping["legal_entity"],
            ),
            value=mapping["portable_subject_ref"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkflowBusinessRef:
    """The per-workflow business reference — the instance-scoping token of one CIB workflow.

    `business_key` composes immutable prohibition 6's shape exactly:
    `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`. Because `:` is not a permitted
    component character, the composed key has exactly two separators by construction.

    Ownership is decided by RECOMPOSING and comparing (`owns_business_key`) — never by splitting a
    key into parts. That keeps the key opaque, and it is what makes a foreign-tenant key fail: a key
    minted under company A cannot equal the key this ref composes under company B.
    """

    company_tenant: CompanyTenantRef
    workflow_type: str
    value: str

    CANONICAL_KEYS: ClassVar[tuple[str, ...]] = (
        "company_tenant_ref",
        "workflow_type",
        "workflow_business_ref",
    )

    def __post_init__(self) -> None:
        _require_scope(self.company_tenant, component="company_tenant", expected=CompanyTenantRef)
        _validate_component(self.workflow_type, component="workflow_type")
        _validate_component(self.value, component="workflow_business_ref")

    @property
    def business_key(self) -> str:
        """`{company_tenant_ref}:{workflow_type}:{workflow_business_ref}` (prohibition 6)."""
        return BUSINESS_KEY_SEPARATOR.join((self.company_tenant.value, self.workflow_type, self.value))

    def owns_business_key(self, candidate: str) -> bool:
        """True only if `candidate` is EXACTLY the key this reference composes.

        A comparison, not a parse: nothing is split, and a key belonging to another company tenant,
        another workflow type or another instance simply is not equal to this one.
        """
        return candidate == self.business_key

    def as_canonical_mapping(self) -> dict[str, str]:
        return {
            "company_tenant_ref": self.company_tenant.value,
            "workflow_type": self.workflow_type,
            "workflow_business_ref": self.value,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_canonical_mapping())

    @classmethod
    def from_canonical_mapping(cls, mapping: Mapping[str, str]) -> Self:
        _exact_keys(mapping, cls.CANONICAL_KEYS)
        return cls(
            company_tenant=CompanyTenantRef(mapping["company_tenant_ref"]),
            workflow_type=mapping["workflow_type"],
            value=mapping["workflow_business_ref"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceProvenanceRef:
    """Where a fact came from — PROVENANCE ONLY, never identity and never a scope.

    Holds the pinned envelope's `source_instance` and `source_tenant`. `source_tenant` is the SOURCE
    system's own tenant: a third tenant axis, unrelated to `CompanyTenantRef`, and the conflation
    ADR-0037 §XRD-01/XRD-03 exists to prevent. It is deliberately NOT a `CompanyTenantRef`, and this
    type deliberately offers no `company_tenant`, no `business_key` and no way to scope a subject —
    a provenance record that could scope anything would be an identity by another name.

    `source_vendor` and `source_product` are absent on purpose: their values are a closed vendor
    vocabulary (`tasy_hospital`, `tasy_healthcare_plan`), which immutable prohibition 2 keeps out of
    the payer core.
    """

    source_instance: str
    source_tenant: str

    CANONICAL_KEYS: ClassVar[tuple[str, ...]] = ("source_instance", "source_tenant")

    def __post_init__(self) -> None:
        _validate_component(self.source_instance, component="source_instance")
        _validate_component(self.source_tenant, component="source_tenant")

    def as_canonical_mapping(self) -> dict[str, str]:
        return {"source_instance": self.source_instance, "source_tenant": self.source_tenant}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.as_canonical_mapping())

    @classmethod
    def from_canonical_mapping(cls, mapping: Mapping[str, str]) -> Self:
        _exact_keys(mapping, cls.CANONICAL_KEYS)
        return cls(
            source_instance=mapping["source_instance"],
            source_tenant=mapping["source_tenant"],
        )


def payer_process_definition_key(workflow_type: str) -> str:
    """Compose a CIB process-definition key in the payer namespace (immutable prohibition 6).

    The prefix is a NAMESPACE, not a tenant: XRD-08 isolates the payer cell's process keys from the
    hospital cell's, and the namespace is the same for every company tenant.
    """
    _validate_component(workflow_type, component="workflow_type")
    return f"{PAYER_PROCESS_DEFINITION_KEY_PREFIX}{workflow_type}"


def is_payer_process_definition_key(candidate: str) -> bool:
    """True if `candidate` is a well-formed payer process-definition key.

    Built from the SAME component rule the composer uses, so predicate and composer cannot drift:
    every key `payer_process_definition_key` produces satisfies this, and nothing else does.

    A non-`str` candidate is refused as `IdentityShapeError`, not returned as `False`: this is the
    same posture `_validate_component` takes, and it keeps one exception type across the module's
    whole surface so a caller's `except IdentityShapeError` cannot miss a malformed input. Returning
    `False` would be indistinguishable from "a well-formed string that is not a payer key".
    """
    if not isinstance(candidate, str):
        raise IdentityShapeError(f"process-definition key must be a str; got {type(candidate).__name__}")
    if not candidate.startswith(PAYER_PROCESS_DEFINITION_KEY_PREFIX):
        return False
    try:
        _validate_component(candidate[len(PAYER_PROCESS_DEFINITION_KEY_PREFIX) :], component="workflow_type")
    except IdentityShapeError:
        return False
    return True


def require_payer_process_definition_key(candidate: str) -> str:
    """Return `candidate` if it is a payer process-definition key; otherwise refuse, fail-closed."""
    if not is_payer_process_definition_key(candidate):
        raise IdentityShapeError(
            "process-definition key must begin with "
            f"{PAYER_PROCESS_DEFINITION_KEY_PREFIX!r} followed by a well-formed workflow type "
            "(ADR-0037 immutable prohibition 6); the rejected key is withheld from this message"
        )
    return candidate


__all__ = [
    "BUSINESS_KEY_SEPARATOR",
    "MAX_COMPONENT_LENGTH",
    "MIN_COMPONENT_LENGTH",
    "PAYER_PROCESS_DEFINITION_KEY_PREFIX",
    "PERMITTED_CHARACTERS",
    "CompanyTenantRef",
    "ForbiddenRawIdentifierError",
    "IdentityShapeError",
    "LegalEntityRef",
    "PortableSubjectRef",
    "SourceProvenanceRef",
    "WorkflowBusinessRef",
    "is_payer_process_definition_key",
    "payer_process_definition_key",
    "require_payer_process_definition_key",
]
