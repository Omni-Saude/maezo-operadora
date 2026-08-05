"""The guarantees `maezo.domain.integration.identity` claims, each as a test that can fail.

Grouped by the property under test:

  1. CONSTRUCTION — the five value objects accept plausible opaque references and are frozen, and
     every MULTI-FIELD type is keyword-only, so the transposition its docstring claims `kw_only`
     prevents cannot be built either statically or at runtime.
  2. SHAPE VALIDATION — emptiness, whitespace, control characters, length bounds, charset, edges;
     and the bare-`str`-as-scope refusal, which is the defect MZO-020 removes.
  3. FORBIDDEN RAW-ID FORMATS — the four recognisable raw identifier shapes, each rejected with its
     OWN reason and as `ForbiddenRawIdentifierError`, never as a charset complaint. Checked on every
     string component of every type, because a hole in one slot is a hole in the boundary — including
     the UNDERSCORE-punctuated CPF/CNS forms, `_` being the only plausible group separator inside the
     permitted charset. Also pins what the rules do NOT refuse, against the module's own docstring:
     the exact-length trade-off is deliberate, so the documented claim must match it.
  4. NO VALUE IN ANY ERROR MESSAGE — an exception message is a log line, a trace and a metric label
     (ADR-0037 immutable prohibition 5), so a rejected raw identifier must not be echoed into one.
     That covers caller-supplied mapping KEYS as well as values: `from_canonical_mapping` takes an
     untrusted `Mapping`, and a CPF-keyed dict is a real shape in Brazilian source systems.
  5. SERIALIZATION ROUND TRIP — the repo's ONE canonical recipe (UTF-8, sorted keys, compact
     separators), the same recipe as `AgentCard.signing_payload`, `DelegationFact.to_value` and the
     contract pin's `envelope.payload_hash_canonicalization`. Keys are checked against the names
     ADR-0037 itself publishes, so no wire format is invented here. Re-validation on the
     deserialization path is asserted for EVERY type and EVERY component slot each one carries —
     a single-type assertion leaves a validation-skipping `from_canonical_mapping` unfenced in the
     other four, and for `WorkflowBusinessRef` that breaks prohibition 6's two-separator arity.
  6. CROSS-COMPANY INEQUALITY — the safety property: same local value + different company tenant is
     a DIFFERENT value, in `==`, in `hash()`, and in sets and dicts.
  7. BUSINESS KEY — immutable prohibition 6's shape composed from the ADR's own template string,
     exactly two separators, and a foreign-tenant key refused.
  8. PROCESS-DEFINITION KEY — the `maezo-payer-` namespace rule, composer and predicate together.
  9. THE FIVE CONCEPTS STAY DISTINCT — no cross-type equality, no substitutability, provenance with
     no scoping surface, and no deployment-cell-shaped field anywhere.
 10. PURITY FENCE — an AST import fence over the whole `maezo.domain` tree, in the same four layers
     and with the same non-vacuity discipline as `tests/unit/ports/test_ports_purity.py`: a named
     infrastructure blocklist, a strictly stronger stdlib allowlist, an intra-repo fence, and a
     PHI-shaped-name probe. Both non-vacuity guards are carried over too — the extractors are proved
     to return real imports, and known-bad sources are judged by the SAME predicates the real scan
     uses. (Stubbing an extractor to `set()` is the failure mode a module count cannot catch.)

Known limit, stated rather than implied: layer 10 is an AST fence, so it sees only STATIC imports. A
dynamic `importlib.import_module(...)` would pass it. The mitigation is that the module is pure
declarations with no executable body to hide such a call in.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

import maezo.domain as domain_pkg
from maezo.domain.integration.identity import (
    BUSINESS_KEY_SEPARATOR,
    MAX_COMPONENT_LENGTH,
    MIN_COMPONENT_LENGTH,
    PAYER_PROCESS_DEFINITION_KEY_PREFIX,
    PERMITTED_CHARACTERS,
    CompanyTenantRef,
    ForbiddenRawIdentifierError,
    IdentityShapeError,
    LegalEntityRef,
    PortableSubjectRef,
    SourceProvenanceRef,
    WorkflowBusinessRef,
    is_payer_process_definition_key,
    payer_process_definition_key,
    require_payer_process_definition_key,
)

# Two DIFFERENT company tenants, reused throughout. The cross-company tests turn on nothing but
# these two being distinct.
COMPANY_A = CompanyTenantRef("operadora-amh")
COMPANY_B = CompanyTenantRef("operadora-rival")

# One local value, deliberately IDENTICAL under both companies — the whole point of section 6.
SHARED_LOCAL = "entidade-0001"


def _legal_entity(company: CompanyTenantRef) -> LegalEntityRef:
    return LegalEntityRef(company_tenant=company, value=SHARED_LOCAL)


def _subject(company: CompanyTenantRef) -> PortableSubjectRef:
    return PortableSubjectRef(legal_entity=_legal_entity(company), value="psr-7f3a9c21e0")


def _workflow(company: CompanyTenantRef) -> WorkflowBusinessRef:
    return WorkflowBusinessRef(company_tenant=company, workflow_type="autorizacao", value="G-2026-0001")


def _provenance() -> SourceProvenanceRef:
    return SourceProvenanceRef(source_instance="inst-prod-01", source_tenant="austa_clinicas")


# Every (label, factory) pair the whole-type sweeps run over, so a new value object cannot be added
# without appearing in the round-trip, PHI-name and distinctness sweeps.
_ALL_FACTORIES: tuple[tuple[str, Callable[[], Any]], ...] = (
    ("CompanyTenantRef", lambda: COMPANY_A),
    ("LegalEntityRef", lambda: _legal_entity(COMPANY_A)),
    ("PortableSubjectRef", lambda: _subject(COMPANY_A)),
    ("WorkflowBusinessRef", lambda: _workflow(COMPANY_A)),
    ("SourceProvenanceRef", lambda: _provenance()),
)

# Every STRING component of every type, as a single-argument constructor. Each entry is one slot a
# raw identifier could be smuggled into; section 3 sweeps all of them.
_COMPONENT_SLOTS: tuple[tuple[str, Callable[[str], object]], ...] = (
    ("CompanyTenantRef.value", CompanyTenantRef),
    ("LegalEntityRef.value", lambda v: LegalEntityRef(company_tenant=COMPANY_A, value=v)),
    (
        "PortableSubjectRef.value",
        lambda v: PortableSubjectRef(legal_entity=_legal_entity(COMPANY_A), value=v),
    ),
    (
        "WorkflowBusinessRef.value",
        lambda v: WorkflowBusinessRef(company_tenant=COMPANY_A, workflow_type="autorizacao", value=v),
    ),
    (
        "WorkflowBusinessRef.workflow_type",
        lambda v: WorkflowBusinessRef(company_tenant=COMPANY_A, workflow_type=v, value="G-1"),
    ),
    (
        "SourceProvenanceRef.source_instance",
        lambda v: SourceProvenanceRef(source_instance=v, source_tenant="austa_clinicas"),
    ),
    (
        "SourceProvenanceRef.source_tenant",
        lambda v: SourceProvenanceRef(source_instance="inst-prod-01", source_tenant=v),
    ),
    ("payer_process_definition_key(workflow_type)", payer_process_definition_key),
)


# ==================================================================================================
# 1. CONSTRUCTION
# ==================================================================================================


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_every_value_object_constructs_from_a_plausible_opaque_reference(
    label: str, factory: Callable[[], Any]
) -> None:
    assert factory() is not None, label


@pytest.mark.parametrize(
    "value",
    [
        "operadora-amh",
        "psr-7f3a9c21e0",
        "0f7a9c21e04b8d3611aa22bb33cc44dd",  # 32-hex opaque ref — must NOT read as a raw id
        "1234567890",  # 10 digits: not a CPF/CNS shape, so not forbidden
        "123456789012",  # 12 digits: likewise
        "a.b_c-d",
        "ab",  # exactly MIN_COMPONENT_LENGTH
        "x" * MAX_COMPONENT_LENGTH,  # exactly MAX_COMPONENT_LENGTH
    ],
)
def test_shape_valid_opaque_values_are_accepted(value: str) -> None:
    """The negative control for every rejection below. A validator that refused everything would
    pass all of section 2 and 3 while making the vocabulary unusable — including for the 32-hex
    reference shape an opaque minter is most likely to emit."""
    assert CompanyTenantRef(value).value == value


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_every_value_object_is_frozen(label: str, factory: Callable[[], Any]) -> None:
    """Immutability is what makes a reference safe to hold across a workflow: a mutable scope could
    be re-pointed at another company after the authorisation that checked it."""
    instance = factory()
    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "mutated")


def test_portable_subject_ref_derives_its_company_tenant_from_its_legal_entity() -> None:
    """XRD-05 scopes the subject reference to the `{amh_tenant, legal_entity}` PAIR. Exposing the
    company as a derived property (not a second field) is what makes the two unable to disagree."""
    subject = _subject(COMPANY_A)
    assert subject.company_tenant == COMPANY_A
    assert subject.company_tenant is subject.legal_entity.company_tenant
    assert "company_tenant" not in {f.name for f in dataclasses.fields(subject)}


# Every type carrying MORE THAN ONE field, derived rather than listed so a new multi-field type is
# fenced the moment it appears. `CompanyTenantRef` is deliberately absent: it is single-field, so it
# takes its value positionally and there is nothing to transpose.
_MULTI_FIELD_FACTORIES: tuple[tuple[str, Callable[[], Any]], ...] = tuple(
    (label, factory) for label, factory in _ALL_FACTORIES if len(dataclasses.fields(factory())) > 1
)


def test_the_multi_field_sweep_is_not_vacuous() -> None:
    """Non-vacuity pin for the two `kw_only` fences below: four of the five types are multi-field, and
    the one that is not is `CompanyTenantRef`."""
    assert len(_MULTI_FIELD_FACTORIES) == 4
    single = {label for label, _ in _ALL_FACTORIES} - {label for label, _ in _MULTI_FIELD_FACTORIES}
    assert single == {"CompanyTenantRef"}
    assert len(dataclasses.fields(COMPANY_A)) == 1


@pytest.mark.parametrize(("label", "factory"), _MULTI_FIELD_FACTORIES)
def test_every_multi_field_type_takes_its_fields_keyword_only(label: str, factory: Callable[[], Any]) -> None:
    """`kw_only=True` is a SAFETY property here, not a style choice, so it needs a fence of its own.

    `CompanyTenantRef`'s docstring already claims it: "transposing a company tenant with a legal
    entity is precisely the silent failure this package exists to prevent". Nothing enforced the
    claim. `mypy --strict` covers `src/maezo` only — the same reason `_require_scope` exists as a
    RUNTIME check — so an adapter decoding untyped wire data gets no protection from the type checker.
    """
    cls = type(factory())
    parameters = list(inspect.signature(cls).parameters.values())
    assert len(parameters) > 1, label
    offenders = [p.name for p in parameters if p.kind is not inspect.Parameter.KEYWORD_ONLY]
    assert not offenders, f"{label} accepts positional field(s) {offenders} — kw_only was dropped"


@pytest.mark.parametrize(("label", "factory"), _MULTI_FIELD_FACTORIES)
def test_a_multi_field_type_refuses_positional_construction_at_runtime(
    label: str, factory: Callable[[], Any]
) -> None:
    """The runtime form of the same fence, with the ARGUMENTS IN THE DECLARED ORDER — so it fails even
    for a mutant where every positional value happens to be shape-valid.

    Without it, `WorkflowBusinessRef(COMPANY_A, "G-2026-0001", "autorizacao")` silently composes the
    TRANSPOSED business key `operadora-amh:G-2026-0001:autorizacao` and every shape rule is satisfied,
    because `workflow_type` and `workflow_business_ref` are both opaque strings.
    """
    instance = factory()
    values = [getattr(instance, field.name) for field in dataclasses.fields(instance)]
    assert len(values) > 1, label
    with pytest.raises(TypeError, match="positional argument"):
        type(instance)(*values)


def test_a_transposed_workflow_business_ref_cannot_be_built_positionally() -> None:
    """The concrete silent failure, spelled out: the transposition that `kw_only` prevents is between
    two slots that are BOTH opaque strings, so no shape rule can catch it afterwards — the composed
    prohibition-6 key is simply wrong, and `owns_business_key` affirms the wrong key."""
    with pytest.raises(TypeError, match="positional argument"):
        WorkflowBusinessRef(COMPANY_A, "G-2026-0001", "autorizacao")
    correct = WorkflowBusinessRef(company_tenant=COMPANY_A, workflow_type="autorizacao", value="G-2026-0001")
    assert correct.business_key == "operadora-amh:autorizacao:G-2026-0001"
    assert not correct.owns_business_key("operadora-amh:G-2026-0001:autorizacao")


# ==================================================================================================
# 2. SHAPE VALIDATION
# ==================================================================================================


@pytest.mark.parametrize(
    ("value", "expected_fragment"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("\t\n", "must not be empty"),
        (" abc", "leading or trailing whitespace"),
        ("abc ", "leading or trailing whitespace"),
        ("ab cd", "must not contain whitespace"),
        ("ab cd", "must not contain whitespace"),  # NBSP
        ("ab\x00cd", "control characters"),
        ("ab\x1fcd", "control characters"),
        ("ab\x7fcd", "control characters"),
        ("ab\x85cd", "control characters"),  # C1 NEL
        ("a", "outside the permitted bounds"),
        ("x" * (MAX_COMPONENT_LENGTH + 1), "outside the permitted bounds"),
        ("ab:cd", "outside the permitted charset"),
        ("ab/cd", "outside the permitted charset"),
        ("ab@cd", "outside the permitted charset"),
        ("operação", "outside the permitted charset"),
        ("ab\U0001f600cd", "outside the permitted charset"),
        ("-abc", "must start and end with an ASCII alphanumeric"),
        ("abc-", "must start and end with an ASCII alphanumeric"),
        (".abc", "must start and end with an ASCII alphanumeric"),
        ("_abc", "must start and end with an ASCII alphanumeric"),
    ],
)
def test_shape_violations_are_refused_with_the_rule_that_caught_them(
    value: str, expected_fragment: str
) -> None:
    with pytest.raises(IdentityShapeError) as excinfo:
        CompanyTenantRef(value)
    assert expected_fragment in str(excinfo.value)
    # A shape violation is NOT a raw-identifier finding; conflating the two would make the
    # dedicated raw-id reason meaningless.
    assert not isinstance(excinfo.value, ForbiddenRawIdentifierError)


def test_the_length_bounds_are_the_declared_constants() -> None:
    """Pins the bounds so a silent widening is a test change, not a quiet policy change."""
    assert (MIN_COMPONENT_LENGTH, MAX_COMPONENT_LENGTH) == (2, 128)
    assert CompanyTenantRef("x" * MIN_COMPONENT_LENGTH).value == "x" * MIN_COMPONENT_LENGTH
    assert CompanyTenantRef("x" * MAX_COMPONENT_LENGTH).value == "x" * MAX_COMPONENT_LENGTH


def test_the_business_key_separator_is_not_a_permitted_component_character() -> None:
    """The structural reason the composed key is unambiguous without ever being parsed."""
    assert BUSINESS_KEY_SEPARATOR not in PERMITTED_CHARACTERS
    assert "/" not in PERMITTED_CHARACTERS
    assert set("abcABC012._-") <= PERMITTED_CHARACTERS


@pytest.mark.parametrize(("label", "slot"), _COMPONENT_SLOTS)
def test_every_component_slot_enforces_the_shape_rules(label: str, slot: Callable[[str], object]) -> None:
    """A validator wired into only some slots is not a boundary. Every string component of every
    type refuses the same shape violation."""
    with pytest.raises(IdentityShapeError):
        slot("bad value with spaces")


@pytest.mark.parametrize(
    ("label", "call"),
    [
        ("LegalEntityRef.company_tenant", lambda v: LegalEntityRef(company_tenant=v, value="ent-01")),
        (
            "PortableSubjectRef.legal_entity",
            lambda v: PortableSubjectRef(legal_entity=v, value="psr-01"),
        ),
        (
            "WorkflowBusinessRef.company_tenant",
            lambda v: WorkflowBusinessRef(company_tenant=v, workflow_type="wf", value="G-1"),
        ),
    ],
)
@pytest.mark.parametrize("bare", ["operadora-amh", 7, None, ("operadora-amh",)])
def test_a_bare_identifier_is_refused_where_a_typed_scope_is_required(
    label: str, call: Callable[[Any], object], bare: object
) -> None:
    """THE defect MZO-020 removes. `mypy --strict` covers `src/maezo`, but an adapter decoding
    untyped wire data does not type-check its inputs — so a bare tenant string must fail loudly at
    construction instead of quietly producing an unscoped reference."""
    with pytest.raises(IdentityShapeError, match="typed value object|must be a"):
        call(bare)


# ==================================================================================================
# 3. FORBIDDEN RAW-ID FORMATS
# ==================================================================================================

# (label, value, the fragment its OWN reason must contain). One entry per recognisable raw shape,
# plus the punctuated/case/embedded variants a real adapter would actually hand over.
_FORBIDDEN_RAW_IDS: tuple[tuple[str, str, str], ...] = (
    ("CPF-like, bare 11-digit run", "12345678901", "CPF-like 11-digit"),
    ("CPF-like, dot/dash punctuated", "123.456.789-01", "CPF-like 11-digit"),
    ("CPF-like, space punctuated", "123 456 789 01", "CPF-like 11-digit"),
    # `_` is the ONLY plausible group separator inside PERMITTED_CHARACTERS, so it is the one a
    # caller can actually get past the charset rule. Leaving it unstripped before the digit-run test
    # made every underscore-punctuated CPF/CNS constructible in all eight component slots.
    ("CPF-like, UNDERSCORE punctuated", "123_456_789_01", "CPF-like 11-digit"),
    ("CPF-like, underscore between every digit", "1_2_3_4_5_6_7_8_9_0_1", "CPF-like 11-digit"),
    ("CPF-like, underscore mixed with dot/dash", "123_456.789-01", "CPF-like 11-digit"),
    ("CNS-like, bare 15-digit run", "123456789012345", "CNS-like 15-digit"),
    ("CNS-like, space punctuated", "123 4567 8901 2345", "CNS-like 15-digit"),
    ("CNS-like, UNDERSCORE punctuated", "123_4567_8901_2345", "CNS-like 15-digit"),
    ("CNS-like, underscore between every digit", "1_2_3_4_5_6_7_8_9_0_1_2_3_4_5", "CNS-like 15-digit"),
    ("FHIR reference, Patient/<id>", "Patient/abc123", "FHIR resource reference"),
    ("FHIR reference, lowercased", "patient/abc123", "FHIR resource reference"),
    ("FHIR reference, embedded in a URL", "https://amh.internal/fhir/Patient/abc123", "FHIR resource"),
    ("FHIR reference, another subject resource", "RelatedPerson/abc123", "FHIR resource reference"),
    ("fhir: URI", "fhir:Patient/abc123", "`fhir:` URI"),
    ("fhir: URI, uppercased scheme", "FHIR:abc123", "`fhir:` URI"),
)


@pytest.mark.parametrize(("label", "value", "expected_reason"), _FORBIDDEN_RAW_IDS)
def test_each_forbidden_raw_id_format_is_refused_with_its_own_dedicated_reason(
    label: str, value: str, expected_reason: str
) -> None:
    """`ForbiddenRawIdentifierError`, and a reason that names the SHAPE — not "invalid characters".

    The distinction is the point: a charset complaint tells a caller to re-encode, which is exactly
    the wrong lesson. The reason must say the value is refused for what it IS.
    """
    with pytest.raises(ForbiddenRawIdentifierError) as excinfo:
        CompanyTenantRef(value)
    message = str(excinfo.value)
    assert expected_reason in message, message
    assert "FORBIDDEN RAW-ID FORMAT" in message
    assert "outside the permitted charset" not in message
    assert "NOT a charset complaint" in message


@pytest.mark.parametrize(("label", "slot"), _COMPONENT_SLOTS)
@pytest.mark.parametrize(("raw_label", "value"), [(lbl, val) for lbl, val, _ in _FORBIDDEN_RAW_IDS])
def test_no_component_slot_of_any_type_accepts_a_forbidden_raw_id(
    label: str, slot: Callable[[str], object], raw_label: str, value: str
) -> None:
    """A raw identifier must be unrepresentable EVERYWHERE, not only in the subject reference: a
    single permissive slot is enough to carry a CPF into the payer domain."""
    with pytest.raises(ForbiddenRawIdentifierError):
        slot(value)


@pytest.mark.parametrize(
    "value",
    [
        "1234567890",  # 10 digits — one short of CPF
        "123456789012",  # 12 digits — one long
        "12345678901234",  # 14 digits — one short of CNS
        "1234567890123456",  # 16 digits — one long
        "0f7a9c21e04b8d3611aa22bb33cc44dd",  # 32-hex opaque ref
        "Patientes",  # no `/`: a word, not a reference
        "fhirstore-01",  # `fhir` without the `:` scheme punctuation
    ],
)
def test_the_raw_id_rule_does_not_swallow_legitimate_opaque_values(value: str) -> None:
    """The precision half of the guarantee. An "any embedded 11-digit run" rule would reject roughly
    a fifth of legitimate 32-hex references by chance; a rule that fires on plausible opaque values
    would be turned off within a week, so pin that it does not."""
    assert CompanyTenantRef(value).value == value


_UNDERSCORE_PUNCTUATED_RAW_IDS: tuple[tuple[str, str], ...] = (
    ("123_456_789_01", "CPF-like 11-digit"),
    ("1_2_3_4_5_6_7_8_9_0_1", "CPF-like 11-digit"),
    ("123_456.789-01", "CPF-like 11-digit"),
    ("123_4567_8901_2345", "CNS-like 15-digit"),
    ("1_2_3_4_5_6_7_8_9_0_1_2_3_4_5", "CNS-like 15-digit"),
)


@pytest.mark.parametrize(("label", "slot"), _COMPONENT_SLOTS)
@pytest.mark.parametrize(("value", "expected_reason"), _UNDERSCORE_PUNCTUATED_RAW_IDS)
def test_an_underscore_punctuated_cpf_or_cns_is_refused_in_every_slot(
    label: str, slot: Callable[[str], object], value: str, expected_reason: str
) -> None:
    """`_` is a PERMITTED component character, which makes it the one group separator a caller can
    write a national identifier with and still pass the charset rule. So it must be stripped before
    the digit-run test exactly as `.`, `-`, `/` and whitespace are — otherwise every underscore-
    punctuated CPF and CNS is representable in every slot of every type, including the first
    component of a prohibition-6 business key."""
    with pytest.raises(ForbiddenRawIdentifierError) as excinfo:
        slot(value)
    assert expected_reason in str(excinfo.value), label


@pytest.mark.parametrize(("value", "expected_reason"), _UNDERSCORE_PUNCTUATED_RAW_IDS)
def test_an_underscore_punctuated_raw_id_cannot_reach_a_composed_business_key(
    value: str, expected_reason: str
) -> None:
    """The consequence that makes the underscore hole a boundary defect rather than a cosmetic one: an
    accepted value becomes the FIRST component of `{company_tenant_ref}:{workflow_type}:
    {workflow_business_ref}`, and `owns_business_key` then affirms the key as this reference's own."""
    # The company-tenant slot: the raw id is refused while the SCOPE is being built, before a
    # `WorkflowBusinessRef` can exist to compose a key from it.
    with pytest.raises(ForbiddenRawIdentifierError) as excinfo:
        CompanyTenantRef(value)
    assert expected_reason in str(excinfo.value)

    # The two opaque string slots of the business key itself.
    for slot in ("workflow_type", "value"):
        kwargs: dict[str, Any] = {
            "company_tenant": COMPANY_A,
            "workflow_type": "autorizacao",
            "value": "G-2026-0001",
        }
        kwargs[slot] = value
        with pytest.raises(ForbiddenRawIdentifierError) as excinfo:
            WorkflowBusinessRef(**kwargs)
        assert expected_reason in str(excinfo.value), slot

    # And no path composes a key containing it.
    composed = _workflow(COMPANY_A).business_key
    assert value not in composed
    assert composed.count(BUSINESS_KEY_SEPARATOR) == 2


@pytest.mark.parametrize(
    "value",
    [
        "Patient/abc123",  # start-anchored
        "fhir/Patient/abc123",  # `/` lead-in
        ".Patient/abc123",  # `.` lead-in — a permitted character
        "-Patient/abc123",  # `-` lead-in
        "_Patient/abc123",  # `_` lead-in
        "ref.Coverage/abc123",
    ],
)
def test_a_fhir_reference_after_a_non_alphanumeric_lead_in_still_gets_the_fhir_reason(
    value: str,
) -> None:
    """These were already REFUSED (`/` is outside the charset); the defect was that the refusal blamed
    the charset instead of naming the FHIR shape, which is the less actionable of the two reasons.
    Widening the lead-in to "not preceded by an alphanumeric" cannot make anything acceptable."""
    with pytest.raises(ForbiddenRawIdentifierError) as excinfo:
        CompanyTenantRef(value)
    assert "FHIR resource reference" in str(excinfo.value)


@pytest.mark.parametrize("value", ["xPatient/abc123", "MyPractitioner/abc123"])
def test_a_mid_word_resource_name_is_not_reported_as_a_fhir_reference(value: str) -> None:
    """The precision half: `xPatient` is a word, not a resource name, so the accurate reason for
    refusing it is the charset rule. Still refused either way — `/` is not permitted."""
    with pytest.raises(IdentityShapeError) as excinfo:
        CompanyTenantRef(value)
    assert "outside the permitted charset" in str(excinfo.value)
    assert not isinstance(excinfo.value, ForbiddenRawIdentifierError)


# Values that the exact-length digit-run strategy does NOT refuse. The strategy is correct and stays:
# an "any embedded 11-digit run" rule would reject roughly a fifth of legitimate 32-hex opaque
# references by chance. But the module docstring must say so, because a DPO/Legal reader quoting a
# heading of "No raw identifier is representable" would be misled about the guarantee they hold.
_DOCUMENTED_STILL_REPRESENTABLE: tuple[str, ...] = (
    "subject-12345678901",
    "id_12345678901",
    "cpf12345678901",
    "012345678901",
    "123456789010",
    "Patient_abc123",
    "Patient-abc123",
)


@pytest.mark.parametrize("value", _DOCUMENTED_STILL_REPRESENTABLE)
def test_the_docstring_lists_every_value_the_raw_id_rules_still_admit(value: str) -> None:
    """Pins the module's DOCUMENTED guarantee to its ACTUAL one, in both directions.

    An overclaiming docstring is a real defect in a package whose DPO/Legal gate was granted for
    identity semantics specifically: the paragraph is what a reviewer quotes. So each value here must
    (a) really construct — proving the exact-length trade-off is what the code does — and (b) be named
    in the docstring, so the text cannot quietly drift back to an absolute claim the code never made.
    """
    doc = importlib.import_module("maezo.domain.integration.identity").__doc__ or ""
    assert CompanyTenantRef(value).value == value
    assert value in doc, f"{value!r} constructs but the module docstring does not admit it"


def test_the_module_docstring_makes_no_absolute_non_representability_claim() -> None:
    """The claim itself. `PERMITTED_CHARACTERS` plus four named shapes is a FLOOR, and the docstring
    must present it as one — the heading a reader quotes has to match what the code delivers."""
    doc = importlib.import_module("maezo.domain.integration.identity").__doc__ or ""
    assert "No raw identifier is representable" not in doc
    assert "not a completeness claim" in doc
    # And the two things it DOES guarantee are still stated.
    assert "refused by name" in doc
    assert "PERMITTED_CHARACTERS" in doc


def test_no_constructor_accepts_a_raw_identifier_under_any_name() -> None:
    """`PortableSubjectRef` wraps the AMH-minted reference and nothing else: there is no alternative
    constructor (`from_cpf`, `from_mpi`, `from_fhir`, ...) that would let one in through the side."""
    forbidden_prefixes = ("from_cpf", "from_cns", "from_mpi", "from_fhir", "from_patient", "from_raw")
    for cls in (CompanyTenantRef, LegalEntityRef, PortableSubjectRef, WorkflowBusinessRef):
        offenders = [name for name in dir(cls) if name.startswith(forbidden_prefixes)]
        assert not offenders, f"{cls.__name__} exposes a raw-identifier constructor: {offenders}"


# ==================================================================================================
# 4. NO VALUE IN ANY ERROR MESSAGE
# ==================================================================================================


@pytest.mark.parametrize(("label", "value", "_reason"), _FORBIDDEN_RAW_IDS)
def test_a_rejected_raw_identifier_never_appears_in_the_error_message(
    label: str, value: str, _reason: str
) -> None:
    """An exception message becomes a log line, a stack trace and often a metric label — all of which
    immutable prohibition 5 forbids raw source identifiers from entering. An error that helpfully
    quoted the rejected CPF would defeat the check that produced it."""
    with pytest.raises(ForbiddenRawIdentifierError) as excinfo:
        CompanyTenantRef(value)
    message = str(excinfo.value)
    assert value not in message
    # Nor the value with its punctuation stripped (the form the check actually computed) — including
    # `_`, which is one of the separators the compaction step removes.
    compact = value
    for separator in (".", "_", "-", " ", "/"):
        compact = compact.replace(separator, "")
    assert compact not in message


@pytest.mark.parametrize("value", ["ab cd", "ab:cd", "operação", "x" * (MAX_COMPONENT_LENGTH + 1)])
def test_a_rejected_shape_violation_never_appears_in_the_error_message(value: str) -> None:
    """Same rule for plain shape failures: at rejection time the validator cannot know whether the
    value it refused was an opaque reference or a raw identifier, so it withholds all of them."""
    with pytest.raises(IdentityShapeError) as excinfo:
        CompanyTenantRef(value)
    assert value not in str(excinfo.value)


def test_the_process_definition_key_refusal_never_echoes_the_rejected_key() -> None:
    with pytest.raises(IdentityShapeError) as excinfo:
        require_payer_process_definition_key("hospital-cell-12345678901")
    assert "12345678901" not in str(excinfo.value)


# CPF-shaped and PHI-shaped MAPPING KEYS. `from_canonical_mapping` is public API over an untrusted
# `Mapping`, and a dict keyed by CPF is a real shape in Brazilian source systems — so a caller-supplied
# KEY is exactly as untrusted as a caller-supplied VALUE, and prohibition 5 covers both.
_DISCLOSURE_PROBE_KEYS: tuple[str, ...] = (
    "12345678901",
    "123.456.789-01",
    "123_456_789_01",
    "cpf_12345678901",
    "Patient/abc123",
    "paciente_nome_completo",
)


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
@pytest.mark.parametrize("probe_key", _DISCLOSURE_PROBE_KEYS)
def test_an_unexpected_canonical_mapping_key_is_never_echoed_into_the_error_message(
    label: str, factory: Callable[[], Any], probe_key: str
) -> None:
    """`_exact_keys` must disclose NOTHING caller-controlled. Naming the EXPECTED keys is fine — they
    are this module's own `CANONICAL_KEYS` constants — but echoing the key set the caller handed over
    puts arbitrary caller text, and therefore possibly a raw CPF, into a message that becomes a log
    line, a stack trace and a metric label (immutable prohibition 5).

    An error that helpfully quoted the offending key would defeat the check that produced it, exactly
    as one that quoted the offending value would.
    """
    cls = type(factory())
    good = factory().as_canonical_mapping()

    # As the SOLE key, and alongside a legitimate one — the message must not carry it either way.
    for mapping in ({probe_key: "operadora-x"}, {**good, probe_key: "operadora-x"}):
        with pytest.raises(IdentityShapeError) as excinfo:
            cls.from_canonical_mapping(mapping)
        message = str(excinfo.value)
        assert probe_key not in message, message
        compact = probe_key
        for separator in (".", "_", "-", " ", "/"):
            compact = compact.replace(separator, "")
        assert compact not in message, message
        # It still tells the caller what IS required — the module's own key names, and how many keys
        # are missing or unexpected. A non-disclosing message is not a useless one.
        assert "do not match the required keys" in message
        for required in cls.CANONICAL_KEYS:
            assert required in message


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_a_missing_canonical_key_is_named_because_the_module_owns_that_name(
    label: str, factory: Callable[[], Any]
) -> None:
    """The other half of MAJOR 2: non-disclosure must not become silence. Which EXPECTED keys are
    missing is derived from `CANONICAL_KEYS`, never from caller input, so naming them discloses
    nothing and is what makes the refusal actionable."""
    cls = type(factory())
    good = factory().as_canonical_mapping()
    for dropped in list(good):
        with pytest.raises(IdentityShapeError) as excinfo:
            cls.from_canonical_mapping({k: v for k, v in good.items() if k != dropped})
        assert dropped in str(excinfo.value), label


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
@pytest.mark.parametrize("not_a_mapping", [None, 7, "company_tenant_ref", ["company_tenant_ref"], object()])
def test_from_canonical_mapping_refuses_a_non_mapping_as_an_identity_shape_error(
    label: str, factory: Callable[[], Any], not_a_mapping: object
) -> None:
    """Fail closed with the module's OWN exception type. These already failed — with a bare `TypeError`
    or `AttributeError` from deep inside `_exact_keys` — so a caller's `except IdentityShapeError`
    around a deserialization boundary missed them, and a `list` whose single element happened to match
    the required key even got past `_exact_keys` before failing on subscript.

    The refusal names the TYPE it got, never the object: `repr()` of a caller-supplied object is
    caller-controlled text (prohibition 5).
    """
    cls = type(factory())
    with pytest.raises(IdentityShapeError, match="must be a Mapping"):
        cls.from_canonical_mapping(not_a_mapping)


# ==================================================================================================
# 5. SERIALIZATION ROUND TRIP
# ==================================================================================================

# The canonical mapping keys each type must use, and where each NAME comes from. Every one is
# published by ADR-0037 itself — immutable prohibition 6 for the business-key components, the frozen
# envelope baseline (pinned in config/integrations/amh/contracts.lock.json) for the rest — so this
# local canonical form invents no field name of its own.
_EXPECTED_CANONICAL_KEYS: dict[str, tuple[str, ...]] = {
    "CompanyTenantRef": ("company_tenant_ref",),
    "LegalEntityRef": ("company_tenant_ref", "legal_entity"),
    "PortableSubjectRef": ("company_tenant_ref", "legal_entity", "portable_subject_ref"),
    "WorkflowBusinessRef": ("company_tenant_ref", "workflow_type", "workflow_business_ref"),
    "SourceProvenanceRef": ("source_instance", "source_tenant"),
}


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_canonical_mapping_round_trips_to_an_equal_value(label: str, factory: Callable[[], Any]) -> None:
    original = factory()
    restored = type(original).from_canonical_mapping(original.as_canonical_mapping())
    assert restored == original
    assert restored is not original
    assert restored.canonical_bytes() == original.canonical_bytes()


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_canonical_mapping_keys_are_the_names_adr_0037_publishes(
    label: str, factory: Callable[[], Any]
) -> None:
    mapping = factory().as_canonical_mapping()
    assert tuple(sorted(mapping)) == tuple(sorted(_EXPECTED_CANONICAL_KEYS[label])), label


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_canonical_bytes_use_the_repos_one_recipe(label: str, factory: Callable[[], Any]) -> None:
    """UTF-8, sorted keys, compact separators — recomputed here independently rather than read back
    from the module, so a change of recipe fails instead of agreeing with itself. This is the same
    recipe as `AgentCard.signing_payload` / `DelegationFact.to_value` and as the contract pin's own
    `envelope.payload_hash_canonicalization`."""
    instance = factory()
    expected = json.dumps(instance.as_canonical_mapping(), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert instance.canonical_bytes() == expected
    # Deterministic: same value in, same bytes out, every time.
    assert instance.canonical_bytes() == factory().canonical_bytes()


def test_canonical_bytes_are_exactly_these_bytes() -> None:
    """One fully-spelled expectation, so the recipe is pinned in literal form somewhere."""
    assert _subject(COMPANY_A).canonical_bytes() == (
        b'{"company_tenant_ref":"operadora-amh","legal_entity":"entidade-0001",'
        b'"portable_subject_ref":"psr-7f3a9c21e0"}'
    )


@pytest.mark.parametrize(("label", "factory"), _ALL_FACTORIES)
def test_from_canonical_mapping_fails_closed_on_a_key_set_that_is_not_exact(
    label: str, factory: Callable[[], Any]
) -> None:
    """A MISSING key would silently drop a scope; an EXTRA key means the caller holds a different
    shape than it thinks. Neither is a round trip, so both are refused."""
    cls = type(factory())
    mapping = factory().as_canonical_mapping()

    for dropped in list(mapping):
        with pytest.raises(IdentityShapeError, match="do not match the required keys"):
            cls.from_canonical_mapping({k: v for k, v in mapping.items() if k != dropped})

    with pytest.raises(IdentityShapeError, match="do not match the required keys"):
        cls.from_canonical_mapping({**mapping, "amh_mpi_ref": "surprise-01"})


# (label, cls, a known-good canonical mapping, ONE key of it) for EVERY component slot of EVERY type
# — derived from the factories and from `as_canonical_mapping()` itself, so a sixth type or a sixth
# component cannot be added without appearing here.
_CANONICAL_SLOT_CASES: tuple[tuple[str, type[Any], dict[str, str], str], ...] = tuple(
    (f"{label}.{key}", type(factory()), dict(factory().as_canonical_mapping()), key)
    for label, factory in _ALL_FACTORIES
    for key in sorted(factory().as_canonical_mapping())
)

# The raw shapes pushed through every deserialization slot. One per refusal branch, plus the
# underscore-punctuated form, so a slot that skipped re-validation cannot hide behind a shape the
# constructor path happens to catch elsewhere.
_REVALIDATION_PROBES: tuple[str, ...] = (
    "12345678901",  # bare CPF-like run
    "123_456_789_01",  # underscore-punctuated CPF-like run
    "123456789012345",  # bare CNS-like run
    "123_4567_8901_2345",  # underscore-punctuated CNS-like run
    "Patient/abc123",  # FHIR resource reference
    "fhir:abc123",  # `fhir:` URI
)


def test_the_canonical_slot_sweep_covers_every_component_of_every_type() -> None:
    """NON-VACUITY GUARD for the sweep below. The re-validation assertion is only worth what its case
    list covers: asserting it for one type and one slot (as an earlier revision did) leaves a
    validation-skipping `from_canonical_mapping` in any of the other four types entirely unfenced."""
    covered = {(label, key) for label, _cls, _mapping, key in _CANONICAL_SLOT_CASES}
    expected = {(f"{label}.{key}", key) for label, keys in _EXPECTED_CANONICAL_KEYS.items() for key in keys}
    assert covered == expected
    assert len(_CANONICAL_SLOT_CASES) == 11  # 1 + 2 + 3 + 3 + 2 slots across the five types
    assert {cls for _label, cls, _mapping, _key in _CANONICAL_SLOT_CASES} == set(_ALL_TYPES)


@pytest.mark.parametrize(
    ("label", "cls", "good_mapping", "slot"),
    _CANONICAL_SLOT_CASES,
    ids=[case[0] for case in _CANONICAL_SLOT_CASES],
)
def test_from_canonical_mapping_revalidates_every_component_of_every_type(
    label: str, cls: type[Any], good_mapping: dict[str, str], slot: str
) -> None:
    """Deserialization is not a trust-boundary bypass: a raw identifier arriving inside a canonical
    mapping is refused exactly as one arriving through the constructor — in EVERY type and EVERY slot.

    Asserted per-slot rather than once, because `from_canonical_mapping` re-enters validation only by
    going back through the constructors. A version that built its instance any other way would
    round-trip happily and still let a raw identifier — or, for `WorkflowBusinessRef`, a `:` that
    breaks prohibition 6's two-separator arity — straight into the payer domain.
    """
    # Negative control: the untampered mapping really does deserialize, so the raises below are not
    # passing because the whole case is malformed.
    assert cls.from_canonical_mapping(good_mapping) is not None, label

    for probe in _REVALIDATION_PROBES:
        with pytest.raises(ForbiddenRawIdentifierError):
            cls.from_canonical_mapping({**good_mapping, slot: probe})

    # And the plain shape rules too — charset, whitespace and the length ceiling.
    for probe in ("bad value with spaces", "with:colon", "x" * (MAX_COMPONENT_LENGTH + 1), "a", ""):
        with pytest.raises(IdentityShapeError):
            cls.from_canonical_mapping({**good_mapping, slot: probe})


def test_a_deserialized_business_key_cannot_break_prohibition_6s_two_separator_arity() -> None:
    """The concrete consequence of a `WorkflowBusinessRef.from_canonical_mapping` that skips
    re-validation: `:` reaches a component, the composed key grows extra separators, and
    `owns_business_key` still affirms it. Prohibition 6's arity is "by construction" only while the
    deserialization path is part of that construction."""
    with pytest.raises(IdentityShapeError, match="outside the permitted charset"):
        WorkflowBusinessRef.from_canonical_mapping(
            {
                "company_tenant_ref": "a:b",
                "workflow_type": "c:d",
                "workflow_business_ref": "G-1",
            }
        )
    # The legitimate neighbour still composes exactly two separators.
    restored = WorkflowBusinessRef.from_canonical_mapping(_workflow(COMPANY_A).as_canonical_mapping())
    assert restored.business_key.count(BUSINESS_KEY_SEPARATOR) == 2
    assert restored.owns_business_key(_workflow(COMPANY_A).business_key)


def test_no_type_defines_str_that_would_collapse_a_scoped_reference() -> None:
    """`f"{ref}"` must never yield the bare local value: that would drop the company scope silently,
    which is the failure mode section 6 exists to prevent. So no type overrides `__str__`, and the
    dataclass `repr` shows the scope."""
    for cls in (CompanyTenantRef, LegalEntityRef, PortableSubjectRef, WorkflowBusinessRef):
        assert "__str__" not in vars(cls), f"{cls.__name__} overrides __str__"
    assert "operadora-amh" in repr(_subject(COMPANY_A))
    assert "operadora-amh" in f"{_legal_entity(COMPANY_A)}"


# ==================================================================================================
# 6. CROSS-COMPANY INEQUALITY
# ==================================================================================================


@pytest.mark.parametrize(
    ("label", "factory"),
    [
        ("LegalEntityRef", _legal_entity),
        ("PortableSubjectRef", _subject),
        ("WorkflowBusinessRef", _workflow),
    ],
)
def test_the_same_local_value_under_two_companies_is_two_different_values(
    label: str, factory: Callable[[CompanyTenantRef], Any]
) -> None:
    """The safety property, not a nicety: if these compared equal, a cache, a dedup table or an
    idempotency store keyed on the reference would serve one company's data to another."""
    under_a, under_b = factory(COMPANY_A), factory(COMPANY_B)

    assert under_a != under_b
    assert not under_a == under_b  # noqa: SIM201 — asserts `__eq__` itself, not `__ne__`
    assert hash(under_a) != hash(under_b)
    assert len({under_a, under_b}) == 2
    assert {under_a: "a", under_b: "b"}[under_b] == "b"
    assert under_a.as_canonical_mapping() != under_b.as_canonical_mapping()
    assert under_a.canonical_bytes() != under_b.canonical_bytes()

    # And the same company yields the SAME value — otherwise inequality above would be vacuous.
    assert factory(COMPANY_A) == under_a
    assert hash(factory(COMPANY_A)) == hash(under_a)
    assert len({factory(COMPANY_A), under_a}) == 1


def test_the_company_tenant_is_part_of_the_compared_state_of_every_scoped_type() -> None:
    """Structural form of the same property: the scope is a COMPARED dataclass field, so it cannot be
    excluded from equality by a future `field(compare=False)` without this failing."""
    for cls, scope_field in (
        (LegalEntityRef, "company_tenant"),
        (PortableSubjectRef, "legal_entity"),
        (WorkflowBusinessRef, "company_tenant"),
    ):
        compared = {f.name for f in dataclasses.fields(cls) if f.compare}
        assert scope_field in compared, f"{cls.__name__}.{scope_field} is excluded from equality"


def test_a_subject_reference_carries_its_company_through_its_legal_entity() -> None:
    """Same subject value, same legal-entity value, different company: still not equal, and the
    derived company property reports the truth."""
    under_a, under_b = _subject(COMPANY_A), _subject(COMPANY_B)
    assert under_a.value == under_b.value
    assert under_a.legal_entity.value == under_b.legal_entity.value
    assert under_a != under_b
    assert under_a.company_tenant != under_b.company_tenant


# ==================================================================================================
# 7. BUSINESS KEY (immutable prohibition 6)
# ==================================================================================================

# Immutable prohibition 6, verbatim: "Business keys CIB opacas na forma
# `{company_tenant_ref}:{workflow_type}:{workflow_business_ref}`".
_PROHIBITION_6_TEMPLATE = "{company_tenant_ref}:{workflow_type}:{workflow_business_ref}"


def test_the_business_key_is_exactly_prohibition_6s_template() -> None:
    """Composed here from the ADR's own template string, so the assertion is the prohibition rather
    than a paraphrase of it."""
    ref = _workflow(COMPANY_A)
    assert ref.business_key == _PROHIBITION_6_TEMPLATE.format(
        company_tenant_ref="operadora-amh",
        workflow_type="autorizacao",
        workflow_business_ref="G-2026-0001",
    )
    assert ref.business_key == "operadora-amh:autorizacao:G-2026-0001"


def test_the_business_key_has_exactly_two_separators() -> None:
    """Unambiguous by construction: `:` is not a permitted component character, so the composed key
    can never grow a third separator — which is why it never needs to be split."""
    ref = _workflow(COMPANY_A)
    assert ref.business_key.count(BUSINESS_KEY_SEPARATOR) == 2
    assert all(part for part in ref.business_key.split(BUSINESS_KEY_SEPARATOR))
    for slot in ("value", "workflow_type"):
        kwargs: dict[str, Any] = {
            "company_tenant": COMPANY_A,
            "workflow_type": "autorizacao",
            "value": "G-1",
        }
        kwargs[slot] = "with:colon"
        with pytest.raises(IdentityShapeError, match="outside the permitted charset"):
            WorkflowBusinessRef(**kwargs)


def test_a_business_key_built_for_one_company_never_validates_for_another() -> None:
    """The cross-company property applied to the KEY itself. Ownership is decided by recomposing and
    comparing, so a foreign key simply is not equal — nothing is parsed out of it."""
    under_a, under_b = _workflow(COMPANY_A), _workflow(COMPANY_B)

    assert under_a.business_key != under_b.business_key
    assert under_a.owns_business_key(under_a.business_key)
    assert under_b.owns_business_key(under_b.business_key)
    assert not under_b.owns_business_key(under_a.business_key)
    assert not under_a.owns_business_key(under_b.business_key)


@pytest.mark.parametrize(
    "foreign_key",
    [
        "operadora-rival:autorizacao:G-2026-0001",  # another company
        "operadora-amh:reembolso:G-2026-0001",  # another workflow type
        "operadora-amh:autorizacao:G-2026-0002",  # another instance
        "operadora-amh:autorizacao",  # truncated
        "operadora-amh:autorizacao:G-2026-0001:extra",  # extended
        "OPERADORA-AMH:autorizacao:G-2026-0001",  # case-shifted company
        "",
    ],
)
def test_owns_business_key_refuses_every_key_it_did_not_compose(foreign_key: str) -> None:
    assert not _workflow(COMPANY_A).owns_business_key(foreign_key)


def test_the_business_key_is_not_reversible_into_components() -> None:
    """Opaque means opaque: nothing here turns a composed key back into typed parts. Such an accessor
    would be the first step of reading a component for meaning."""
    forbidden = ("parse", "split", "decode", "components", "from_business_key", "local_part")
    offenders = [name for name in dir(WorkflowBusinessRef) if name.startswith(forbidden)]
    assert not offenders, f"WorkflowBusinessRef exposes key-decomposition surface: {offenders}"


# ==================================================================================================
# 8. PROCESS-DEFINITION KEY (immutable prohibition 6, second half)
# ==================================================================================================


def test_the_payer_process_definition_key_prefix_is_verbatim() -> None:
    assert PAYER_PROCESS_DEFINITION_KEY_PREFIX == "maezo-payer-"


def test_payer_process_definition_keys_compose_inside_the_payer_namespace() -> None:
    key = payer_process_definition_key("autorizacao")
    assert key == "maezo-payer-autorizacao"
    assert key.startswith(PAYER_PROCESS_DEFINITION_KEY_PREFIX)
    assert is_payer_process_definition_key(key)
    assert require_payer_process_definition_key(key) == key


@pytest.mark.parametrize("workflow_type", ["autorizacao", "reembolso", "ans.submit", "contas-recurso"])
def test_the_composer_and_the_predicate_cannot_drift(workflow_type: str) -> None:
    """The predicate is built from the SAME component rule the composer uses, so everything the
    composer emits satisfies it — a two-sided agreement, not two independent guesses."""
    assert is_payer_process_definition_key(payer_process_definition_key(workflow_type))


@pytest.mark.parametrize(
    "candidate",
    [
        "maezo-hospital-autorizacao",  # the OTHER cell's namespace (XRD-08)
        "payer-autorizacao",
        "autorizacao",
        "maezo-payer-",  # prefix alone: no workflow type
        "maezo-payer-a",  # workflow type below the length floor
        "MAEZO-PAYER-autorizacao",  # case-shifted prefix
        "maezo-payer-:autorizacao",
        "maezo-payer- autorizacao",
        "maezo-payer--autorizacao",
        "operadora-amh:autorizacao:G-1",  # a business key is not a process-definition key
        "",
    ],
)
def test_a_key_outside_the_payer_namespace_is_refused(candidate: str) -> None:
    assert not is_payer_process_definition_key(candidate)
    with pytest.raises(IdentityShapeError, match="must begin with"):
        require_payer_process_definition_key(candidate)


def test_the_process_namespace_is_not_derived_from_the_company_tenant() -> None:
    """XRD-08 isolates the payer CELL's process keys from the hospital cell's. The namespace is a
    deployment-topology fact and is the same for every company tenant — so a process-definition key
    must never carry a tenant, and a business key must never be mistaken for one."""
    key = payer_process_definition_key("autorizacao")
    assert COMPANY_A.value not in key
    assert COMPANY_B.value not in key
    assert BUSINESS_KEY_SEPARATOR not in key
    assert not is_payer_process_definition_key(_workflow(COMPANY_A).business_key)


# ==================================================================================================
# 9. THE FIVE CONCEPTS STAY DISTINCT
# ==================================================================================================

_ALL_TYPES: tuple[type[Any], ...] = (
    CompanyTenantRef,
    LegalEntityRef,
    PortableSubjectRef,
    WorkflowBusinessRef,
    SourceProvenanceRef,
)


def test_there_are_exactly_five_value_objects() -> None:
    """Non-vacuity pin for every sweep in this file, and the review checkpoint for a sixth concept:
    ADR-0037 separates deployment cell, company tenant, legal entity, source instance and CIB process
    namespace, and collapsing any two of them is the ambiguity MZO-020 removes."""
    module = importlib.import_module("maezo.domain.integration.identity")
    declared = {
        name
        for name, obj in vars(module).items()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj) and obj.__module__ == module.__name__
    }
    assert declared == {cls.__name__ for cls in _ALL_TYPES}
    assert len(declared) == 5


def test_no_two_of_the_five_types_ever_compare_equal() -> None:
    """Same underlying value, different concept: still not the same value. Equality across concepts
    would let a source instance be accepted where a company tenant was required."""
    same_value = "operadora-amh"
    instances = [
        CompanyTenantRef(same_value),
        LegalEntityRef(company_tenant=CompanyTenantRef(same_value), value=same_value),
        PortableSubjectRef(
            legal_entity=LegalEntityRef(company_tenant=CompanyTenantRef(same_value), value=same_value),
            value=same_value,
        ),
        WorkflowBusinessRef(
            company_tenant=CompanyTenantRef(same_value), workflow_type=same_value, value=same_value
        ),
        SourceProvenanceRef(source_instance=same_value, source_tenant=same_value),
    ]
    # Non-vacuity: the sweep must cover ALL five concepts. Omitting one (PortableSubjectRef was
    # omitted) leaves the concept whose confusion matters most entirely outside the assertion.
    assert {type(i) for i in instances} == set(_ALL_TYPES)
    assert len(instances) == 5
    for left in instances:
        for right in instances:
            if type(left) is not type(right):
                assert left != right, f"{type(left).__name__} == {type(right).__name__}"
    assert len(set(instances)) == len(instances)


def test_source_provenance_offers_no_scoping_surface() -> None:
    """ "Provenance only" as a structural property. A provenance record that could scope a subject or
    compose a business key would be an identity under another name — and `source_tenant` is the
    SOURCE system's tenant (XRD-01/XRD-03), a third axis that must never be read as the paying
    company."""
    provenance = _provenance()
    for forbidden in ("company_tenant", "legal_entity", "business_key", "owns_business_key"):
        assert not hasattr(provenance, forbidden), f"SourceProvenanceRef exposes {forbidden}"

    types_by_field = {f.name: f.type for f in dataclasses.fields(provenance)}
    assert set(types_by_field) == {"source_instance", "source_tenant"}
    for field_name, annotation in types_by_field.items():
        assert "CompanyTenantRef" not in str(annotation), (
            f"SourceProvenanceRef.{field_name} is typed as a company tenant — the source tenant and "
            "the paying company are different axes (ADR-0037 XRD-01/XRD-03)"
        )
    assert provenance != CompanyTenantRef(provenance.source_tenant)


def test_no_type_carries_a_deployment_cell_shaped_field() -> None:
    """The deployment cell (XRD-08/XRD-11) is deliberately NOT a domain value: engine, database,
    credentials and worker topics are deployment facts, and a value object for them would invite
    their use as a scope — the exact conflation this package removes."""
    cell_fragments = ("cell", "cluster", "deployment", "region", "namespace", "environment", "eks")
    offenders: dict[str, list[str]] = {}
    for cls in _ALL_TYPES:
        bad = [
            f.name for f in dataclasses.fields(cls) if any(frag in f.name.lower() for frag in cell_fragments)
        ]
        if bad:
            offenders[cls.__name__] = bad
    assert not offenders, f"deployment-cell-shaped field name(s): {offenders}"


# ==================================================================================================
# 10. PURITY FENCE — mirrors tests/unit/ports/test_ports_purity.py
# ==================================================================================================

_SRC_ROOT: Path = Path(domain_pkg.__file__).resolve().parent.parent.parent

# The EXACT module set of the `maezo.domain` tree, as paths relative to `src/maezo/domain/`. A new
# module must be added here deliberately — that is the review checkpoint.
_EXPECTED_MODULES: frozenset[str] = frozenset(
    {
        "__init__.py",  # maezo.domain — docstring + empty __all__
        "integration/__init__.py",  # public surface (re-exports only)
        "integration/identity.py",  # the five identity/tenant value objects (XRD-05)
    }
)

# (i) Named blocklist — brokers, codecs, registries, cloud SDKs, HTTP/SQL clients, clinical-record
# libraries. Kept deliberately in the same shape as the ports fence so a reviewer comparing the two
# sees one rule, not two. `fhir`/`hapi` matter especially here: this module NAMES FHIR shapes in a
# rejection list and must never acquire the ability to actually read one.
_FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "aiokafka",
        "kafka",
        "confluent_kafka",
        "fastavro",
        "avro",
        "boto3",
        "botocore",
        "awscrt",
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "urllib3",
        "socket",
        "websockets",
        "hapi",
        "fhirclient",
        "fhir",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "sqlalchemy",
        "alembic",
        "fastapi",
        "pydantic",
        "langgraph",
        "anthropic",
        "mcp",
        "structlog",
        "opentelemetry",
        "prometheus_client",
        "tenacity",
        "lxml",
        "yaml",
    }
)

# (iii) `maezo` subpackages the domain may NEVER import. It is a leaf alongside `maezo.ports`;
# everything else in the repository is a consumer of it. `ports` is on the list too: the ports hold
# opaque `str` references by design (their own docstrings say identity semantics live HERE), so a
# dependency in either direction would couple two boundaries that must be able to move separately.
_FORBIDDEN_MAEZO_SUBPACKAGES: frozenset[str] = frozenset(
    {"tools", "runtime", "platform", "agents", "a2a", "gateway", "ports"}
)

_ALLOWED_NON_STDLIB_ROOTS: frozenset[str] = frozenset({"maezo"})

# (iv) Patient-identifying name fragments, matched as a substring of a lowercased field name. Same
# list as the ports fence, and the same deliberate omissions: `subject` is absent because
# `portable_subject_ref` is the pinned contract's own name for an OPAQUE reference.
_PHI_NAME_FRAGMENTS: frozenset[str] = frozenset(
    {
        "cpf",
        "cns",
        "mrn",
        "ssn",
        "patient",
        "paciente",
        "beneficiario",
        "beneficiary",
        "nome",
        "full_name",
        "given_name",
        "family_name",
        "birth",
        "nasc",
        "phone",
        "telefone",
        "email",
        "address",
        "endereco",
        "gender",
        "sexo",
    }
)

_ESCAPED_RELATIVE_IMPORT = "<relative-import-above-maezo>"


def _domain_modules() -> dict[str, Path]:
    pkg_dir = Path(domain_pkg.__file__).resolve().parent
    return {str(p.relative_to(pkg_dir).as_posix()): p for p in sorted(pkg_dir.rglob("*.py"))}


def _package_parts(path: Path) -> tuple[str, ...]:
    """The dotted package a relative import inside `path` resolves against.

    For `maezo/domain/integration/identity.py` that is `("maezo","domain","integration")`; for
    `maezo/domain/__init__.py` it is `("maezo","domain")` — an `__init__` IS its own package.
    """
    return path.resolve().relative_to(_SRC_ROOT).with_suffix("").parts[:-1]


def _absolute_import_targets(tree: ast.AST, package_parts: tuple[str, ...]) -> set[str]:
    """Absolute dotted module path named by EVERY import, with relative imports RESOLVED.

    An unresolved relative import is invisible to every fence built on this function, which is
    exactly how a real cross-package dependency can sit inside a leaf module with a green suite.
    `from .. import x` yields no `node.module`, only aliases, so the aliases are resolved as
    submodules of the resolved package — the only sound reading, since naming `x` there is a
    dependency either way.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    targets.add(node.module)
                continue
            # max(0, ...): a level walking past the root must clamp to the empty prefix and be
            # reported as escaped. An unclamped index goes NEGATIVE and Python's slice wraps it, so
            # a deep `from .... import x` would resolve back to a whitelisted prefix and pass unseen.
            base = package_parts[: max(0, len(package_parts) - (node.level - 1))]
            if not base:
                targets.add(_ESCAPED_RELATIVE_IMPORT)
                continue
            prefix = ".".join(base)
            if node.module:
                targets.add(f"{prefix}.{node.module}")
            else:
                targets.update(f"{prefix}.{alias.name}" for alias in node.names)
    return targets


def _imported_roots(tree: ast.AST, package_parts: tuple[str, ...]) -> set[str]:
    return {target.split(".")[0] for target in _absolute_import_targets(tree, package_parts)}


def _infrastructure_offenders(tree: ast.AST, package_parts: tuple[str, ...]) -> set[str]:
    """Fence (i): imported roots on the named infrastructure blocklist."""
    return _imported_roots(tree, package_parts) & _FORBIDDEN_IMPORT_ROOTS


def _non_stdlib_offenders(tree: ast.AST, package_parts: tuple[str, ...]) -> set[str]:
    """Fence (ii): imported roots that are neither stdlib nor `maezo`. Strictly stronger than (i),
    which by construction can only ban what someone thought of."""
    return {
        root
        for root in _imported_roots(tree, package_parts)
        if root not in sys.stdlib_module_names and root not in _ALLOWED_NON_STDLIB_ROOTS
    }


def _foreign_maezo_offenders(tree: ast.AST, package_parts: tuple[str, ...]) -> set[str]:
    """Fence (iii): `maezo.*` imports that are not `maezo.domain.*`."""
    return {
        dotted
        for dotted in _absolute_import_targets(tree, package_parts)
        if dotted.split(".")[0] == "maezo" and not dotted.startswith("maezo.domain")
    }


def _parsed(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_domain_modules_discovered() -> None:
    """Non-vacuity guard: the fences below must be scanning the REAL package, not an empty glob."""
    modules = _domain_modules()
    assert set(modules) == _EXPECTED_MODULES, (
        f"maezo.domain module set drifted: found {sorted(modules)}, expected {sorted(_EXPECTED_MODULES)}"
    )
    assert len(modules) == 3


def test_import_extraction_is_not_vacuous() -> None:
    """NON-VACUITY FOR THE EXTRACTORS THEMSELVES — the guard a module count cannot give.

    Every fence is `<extractor>(module) & <forbidden>`, so an extractor returning an empty set would
    make all of them pass unconditionally while proving nothing, with the modules still found and
    still opened. So assert the positive: parsing the real module yields its known import roots.
    """
    modules = _domain_modules()
    identity = modules["integration/identity.py"]
    parts = _package_parts(identity)
    assert parts == ("maezo", "domain", "integration"), parts

    roots = _imported_roots(_parsed(identity), parts)
    assert {"__future__", "collections", "dataclasses", "json", "re", "string", "typing"} <= roots, (
        f"_imported_roots did not extract identity.py's known import roots: {sorted(roots)}"
    )
    paths = _absolute_import_targets(_parsed(identity), parts)
    assert {"collections.abc", "dataclasses", "typing"} <= paths, sorted(paths)

    surface = modules["integration/__init__.py"]
    assert "maezo.domain.integration.identity" in _absolute_import_targets(
        _parsed(surface), _package_parts(surface)
    )


# (label, source, expected offenders from each of the three predicates). Resolved against
# `maezo.domain.integration`, the deepest package in the tree.
_SYNTHETIC_PARTS: tuple[str, ...] = ("maezo", "domain", "integration")

_SYNTHETIC_VIOLATIONS: tuple[tuple[str, str, set[str], set[str], set[str]], ...] = (
    ("blocklisted infrastructure", "import aiokafka\n", {"aiokafka"}, {"aiokafka"}, set()),
    ("a clinical-record library", "import fhirclient\n", {"fhirclient"}, {"fhirclient"}, set()),
    (
        "unlisted third party (the failure mode a blocklist alone always has)",
        "import some_unlisted_vendor_sdk\n",
        set(),
        {"some_unlisted_vendor_sdk"},
        set(),
    ),
    (
        "absolute cross-package import",
        "from maezo.gateway.pep import load_matrix\n",
        set(),
        set(),
        {"maezo.gateway.pep"},
    ),
    (
        "the ports, which hold opaque refs and must stay independently movable",
        "from maezo.ports.envelope import CanonicalEnvelope\n",
        set(),
        set(),
        {"maezo.ports.envelope"},
    ),
    (
        "RELATIVE cross-package import, bare `from ... import X` form",
        "from ... import a2a\n",
        set(),
        set(),
        {"maezo.a2a"},
    ),
    (
        "RELATIVE cross-package import, `from ...pkg import X` form",
        "from ...gateway import pep\n",
        set(),
        set(),
        {"maezo.gateway"},
    ),
    (
        "relative import escaping above maezo entirely",
        "from .... import anything\n",
        set(),
        {_ESCAPED_RELATIVE_IMPORT},
        set(),
    ),
    (
        # Level 5 walks TWO levels past the root. An unclamped index is negative, and a negative
        # slice wraps to a SHORTER-BUT-NON-EMPTY prefix — so `from ..... import domain` would read
        # back as the whitelisted `maezo.domain` and pass unseen.
        "relative import escaping TWO levels above maezo, aliasing the whitelisted package name",
        "from ..... import domain\n",
        set(),
        {_ESCAPED_RELATIVE_IMPORT},
        set(),
    ),
)

_SYNTHETIC_CLEAN: tuple[tuple[str, str], ...] = (
    ("stdlib absolute", "from dataclasses import dataclass\n"),
    ("stdlib dotted", "from collections.abc import Mapping\n"),
    ("sibling domain module, absolute", "from maezo.domain.integration.identity import CompanyTenantRef\n"),
    ("sibling domain module, relative", "from . import identity\n"),
    ("sibling domain module, relative from-module", "from .identity import CompanyTenantRef\n"),
    ("parent domain package, relative", "from .. import integration\n"),
)


@pytest.mark.parametrize(("label", "source", "infra", "non_stdlib", "foreign"), _SYNTHETIC_VIOLATIONS)
def test_synthetic_violations_are_detected_by_the_real_predicates(
    label: str, source: str, infra: set[str], non_stdlib: set[str], foreign: set[str]
) -> None:
    """The other half of the non-vacuity proof: known-bad sources put through the SAME predicate
    functions the real scan calls, pinning WHICH fence catches each shape. A synthetic test against a
    re-spelled copy of the rule would prove only that the copy works."""
    tree = ast.parse(source)
    assert _infrastructure_offenders(tree, _SYNTHETIC_PARTS) == infra, label
    assert _non_stdlib_offenders(tree, _SYNTHETIC_PARTS) == non_stdlib, label
    assert _foreign_maezo_offenders(tree, _SYNTHETIC_PARTS) == foreign, label


@pytest.mark.parametrize(("label", "source"), _SYNTHETIC_CLEAN)
def test_legitimate_imports_are_not_flagged(label: str, source: str) -> None:
    """The negative control. A fence that flagged everything would pass every test above and make the
    package unmaintainable rather than pure."""
    tree = ast.parse(source)
    assert _infrastructure_offenders(tree, _SYNTHETIC_PARTS) == set(), label
    assert _non_stdlib_offenders(tree, _SYNTHETIC_PARTS) == set(), label
    assert _foreign_maezo_offenders(tree, _SYNTHETIC_PARTS) == set(), label


def test_no_domain_module_imports_infrastructure() -> None:
    """Fence (i): a broker, codec, cloud, HTTP, SQL or clinical-record client in a domain module
    would make the payer core depend on the infrastructure ADR-0037 isolates it from."""
    offenders = {
        name: bad
        for name, path in _domain_modules().items()
        if (bad := _infrastructure_offenders(_parsed(path), _package_parts(path)))
    }
    assert not offenders, (
        f"ADR-0037 boundary violation — maezo.domain module(s) import infrastructure: {offenders}"
    )


def test_domain_modules_import_only_stdlib_and_themselves() -> None:
    """Fence (ii): the POSITIVE form — stdlib + `maezo` and nothing else."""
    offenders = {
        name: bad
        for name, path in _domain_modules().items()
        if (bad := _non_stdlib_offenders(_parsed(path), _package_parts(path)))
    }
    assert not offenders, (
        f"maezo.domain is leaf-domain (stdlib + typing only). Non-stdlib import(s) found: {offenders}"
    )


def test_no_domain_module_imports_another_maezo_package() -> None:
    """Fence (iii): the domain is a leaf. Every other `maezo` package is a consumer of it."""
    offenders = {
        name: bad
        for name, path in _domain_modules().items()
        if (bad := _foreign_maezo_offenders(_parsed(path), _package_parts(path)))
    }
    assert not offenders, f"dependency inversion — maezo.domain imports another maezo package: {offenders}"

    forbidden_prefixes = {f"maezo.{sub}" for sub in _FORBIDDEN_MAEZO_SUBPACKAGES}
    for name, path in _domain_modules().items():
        for dotted in _absolute_import_targets(_parsed(path), _package_parts(path)):
            assert not any(dotted == p or dotted.startswith(f"{p}.") for p in forbidden_prefixes), (
                f"{name} imports {dotted} — the domain must not depend on any application package"
            )


def test_the_identity_module_pulls_in_no_infrastructure_at_import_time() -> None:
    """The dynamic complement to the AST fence, which by construction sees only STATIC imports:
    import the module in a fresh interpreter with the blocklist unimportable, and require it to load.
    Catches the `importlib.import_module("aiokafka")` shape the AST layers are blind to."""
    module = importlib.import_module("maezo.domain.integration.identity")
    resolved = {
        name.split(".")[0]
        for name, mod in sys.modules.items()
        if mod is not None and name.startswith("maezo.domain")
    }
    assert resolved == {"maezo"}, resolved
    # And the module's own globals hold no imported module object outside stdlib + maezo.
    for name, obj in vars(module).items():
        if isinstance(obj, type(sys)):
            root = obj.__name__.split(".")[0]
            assert root in sys.stdlib_module_names or root == "maezo", f"{name} -> {obj.__name__}"


def test_no_domain_value_type_has_a_phi_shaped_field() -> None:
    """Fence (iv): the value objects are OPAQUE (they parse nothing), so the only structural
    guarantee against PHI crossing the boundary is that no PHI-shaped SLOT exists at all
    (ADR-0037 immutable prohibition 5)."""
    assert len(_ALL_TYPES) == 5
    offenders: dict[str, list[str]] = {}
    for cls in _ALL_TYPES:
        bad = [
            f.name
            for f in dataclasses.fields(cls)
            if any(fragment in f.name.lower() for fragment in _PHI_NAME_FRAGMENTS)
        ]
        if bad:
            offenders[cls.__name__] = bad
    assert not offenders, f"PHI-shaped field name(s) in maezo.domain value types: {offenders}"


def test_no_public_callable_has_a_phi_shaped_parameter() -> None:
    """Same probe over the PARAMETERS of every public callable in the module — a PHI-shaped argument
    would let a caller push identifying data in even though no value type holds it."""
    module = importlib.import_module("maezo.domain.integration.identity")
    offenders: dict[str, list[str]] = {}
    for name, obj in vars(module).items():
        if name.startswith("_") or getattr(obj, "__module__", None) != module.__name__:
            continue
        candidates: list[tuple[str, Any]] = []
        if inspect.isfunction(obj):
            candidates.append((name, obj))
        elif isinstance(obj, type):
            candidates.extend(
                (f"{name}.{member}", value)
                for member, value in vars(obj).items()
                if not member.startswith("_") and callable(value)
            )
        for qualname, func in candidates:
            try:
                params = inspect.signature(func).parameters
            except (TypeError, ValueError):  # pragma: no cover — defensive
                continue
            bad = [p for p in params if any(frag in p.lower() for frag in _PHI_NAME_FRAGMENTS)]
            if bad:
                offenders[qualname] = bad
    assert not offenders, f"PHI-shaped parameter name(s) on maezo.domain public callable(s): {offenders}"


def test_the_public_surface_re_exports_exactly_the_identity_module() -> None:
    """`maezo.domain.integration` is a re-export surface only, mirroring `maezo.ports.__init__`: a
    symbol reaching the public shape without being declared in `identity.py` would be a second,
    unfenced home for identity semantics."""
    surface = importlib.import_module("maezo.domain.integration")
    identity = importlib.import_module("maezo.domain.integration.identity")
    assert isinstance(surface.__all__, list)
    assert sorted(surface.__all__) == sorted(identity.__all__)
    for name in surface.__all__:
        assert getattr(surface, name) is getattr(identity, name), name
    assert importlib.import_module("maezo.domain").__all__ == []


def test_the_public_surface_matches_what_the_module_declares() -> None:
    """Every name in `__all__` exists, and every public value object / callable is in `__all__`."""
    identity = importlib.import_module("maezo.domain.integration.identity")
    for name in identity.__all__:
        assert hasattr(identity, name), name
    declared_locally = {
        name
        for name, obj in vars(identity).items()
        if not name.startswith("_")
        and getattr(obj, "__module__", None) == identity.__name__
        and (isinstance(obj, type) or callable(obj))
    }
    assert declared_locally <= set(identity.__all__), declared_locally - set(identity.__all__)


def test_the_mapping_type_is_only_used_for_reading() -> None:
    """`from_canonical_mapping` accepts any read-only `Mapping`, so a caller need not hand over a
    mutable dict — and the value object never mutates what it was given."""
    source: Mapping[str, str] = {"company_tenant_ref": "operadora-amh"}
    restored = CompanyTenantRef.from_canonical_mapping(source)
    assert restored == COMPANY_A
    assert source == {"company_tenant_ref": "operadora-amh"}
