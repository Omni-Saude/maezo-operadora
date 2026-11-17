"""E-SF-04 — "zero saúde" wire fences and closed vocabularies (guard tests).

Plan row E-SF-04 (executable, conservative fallback "nada de saúde no comercial"), children:
EP-REG-02a, EP-REG-10a, TB-E-03-a, TB-E-12-b, TB-E-24-a, TB-E-21-c. Each test injects the
violation the spec names and asserts a typed refusal (fence) AND the closed wire refusal of
the real C6-4 codec (`CommercialWire` over the pinned candidate bytes). The pinned
`tests/candidate_artifacts/commercial/contract-c6-4.json` is only read; "successor" mutations
are deep copies in memory. No DPO field-class taxonomy is assumed: the purpose allowlist is
an injected fixture of synthetic tokens (the closed published list is a GAP).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any, get_args

import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.adapters.amh.commercial_fences import (
    KNOWN_FALSE_POSITIVES,
    CommercialFenceError,
    CommercialFenceRefusal,
    contract_health_slots,
    health_marker,
    screen_commercial_input,
    screen_journey_labels,
    validate_intake_descriptor,
)
from maezo.gateway.vendor_submissions import VendorSubmissionError, VendorSubmissionRefusal
from maezo.portal.contracts import commission_policies
from maezo.portal.contracts.vendor_submissions import ChannelSubmissionPayload
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.gateway.test_vendor_submissions import TENANT, command, machine, vendor_membership
from tests.unit.test_commercial_c6 import encode, envelope, journey, parse
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "tests" / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
PHI = CommercialFenceRefusal.PHI_IN_COMMERCIAL_INPUT
#: Synthetic stand-ins for the closed purpose list (região, modalidade, composição, contato
#: — TB-03 (1)); the real tokens are source/DPO-published (GAP), never defaulted in src.
#: Synthetic closed journey label vocabulary (the published DS label set is a GAP).
LABELS = frozenset({"Declaração pendente", "Documento administrativo pendente"})
PURPOSES = frozenset(
    {"SYN-PURPOSE-REGION", "SYN-PURPOSE-MODALITY", "SYN-PURPOSE-COMPOSITION", "SYN-PURPOSE-CONTACT"}
)


def contract() -> dict[str, Any]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def declared(name: str) -> frozenset[str]:
    document = contract()
    ref = document["x-commands"][name]["payload_schema"].rsplit("/", 1)[1]
    return frozenset(document["$defs"][ref]["properties"])


def field(**changes: Any) -> dict[str, Any]:
    return {
        "field_ref": "SYN-FIELD-REGION",
        "label": "Região de atendimento",
        "purpose_ref": "SYN-PURPOSE-REGION",
        "value_type": "choice",
        "required": True,
        "max_length": 120,
        "choices": ["Capital", "Interior"],
    } | changes


def descriptor(stage: str = "prospect", fields: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "descriptor_ref": "SYN-DESCRIPTOR",
        "revision": 1,
        "stage": stage,
        "valid_until": "2099-01-01T00:00:00Z",
        "fields": [field()] if fields is None else fields,
    }


def refused(call: Any, reason: CommercialFenceRefusal = PHI) -> None:
    with pytest.raises(CommercialFenceError) as exc:
        call()
    assert exc.value.reason is reason


def wire_refuses_command(codec: CommercialWire, name: str, payload: dict[str, Any]) -> None:
    with pytest.raises(CommercialRefusalError) as exc:
        codec.parse_command(encode(envelope(name, payload)))
    assert (exc.value.status, exc.value.reason_code) == ("denied", "invalid_request")


def wire_refuses_result(
    codec: CommercialWire, name: str, payload: dict[str, Any], data: dict[str, Any]
) -> None:
    with pytest.raises(CommercialRefusalError) as exc:
        parse(codec, envelope(name, payload), data)
    assert (exc.value.status, exc.value.reason_code) == ("unavailable", "source_unavailable")


# ----------------------------------------------------------------------------- EP-REG-02a

#: KNOWN GAP (not an exception the spec grants): C6-4 $defs contém 6 propriedades `cpt`
#: (PublicCatalog*) fora dos gates :485/:499 citados por EP-REG-02a; aceitação (1) não
#: satisfeita literalmente no contrato pinado; requer decisão do custodiante AMH/DPO.
#: Pinned exactly: any NEW hit fails this guard.
KNOWN_GAP_CPT_SLOTS = (
    "PublicCatalogAssociationProduct.cpt",
    "PublicCatalogAssociationSourceWire/properties/products/items.cpt",
    "PublicCatalogNumericProduct.cpt",
    "PublicCatalogNumericSourceWire/properties/products/items.cpt",
    "PublicCatalogProduct.cpt",
    "PublicCatalogSourceWire/properties/products/items.cpt",
)


def test_commercial_snapshot_has_no_health_derived_field(schema_bytes: bytes) -> None:
    """The fence proof is the direct calls (`contract_health_slots`, `screen_commercial_input`).

    The `wire_refuses_command` part proves the closed-schema invariant (:448,
    `additionalProperties: false`) of the frozen codec, not the fence.
    """
    pinned = contract()
    assert contract_health_slots(pinned) == KNOWN_GAP_CPT_SLOTS
    # Injected violation: a successor adds `ds_risk_score` to ContactRequestPayload.
    successor = copy.deepcopy(pinned)
    successor["$defs"]["ContactRequestPayload"]["properties"]["ds_risk_score"] = {"type": "number"}
    assert "ContactRequestPayload.ds_risk_score" in contract_health_slots(successor)
    # On the frozen wire the slot does not exist: closed schema + typed fence refusal.
    assert pinned["$defs"]["ContactRequestPayload"]["additionalProperties"] is False
    payload = {
        "prospect_ref": "SYN-PROSPECT",
        "contact_ref": "SYN-CONTACT",
        "contact_permission_ref": "SYN-PERMISSION",
        "topic": "product_information",
    }
    CommercialWire(schema_bytes).parse_command(encode(envelope("contact.request", payload)))
    wire_refuses_command(CommercialWire(schema_bytes), "contact.request", payload | {"ds_risk_score": 0.9})
    refused(
        lambda: screen_commercial_input(
            payload | {"ds_risk_score": 0.9}, declared_keys=declared("contact.request")
        )
    )


def test_phi_in_commercial_input_refused_for_ds_proxy(schema_bytes: bytes) -> None:
    """The fence proof is the direct `screen_commercial_input` calls (typed refusal).

    The `wire_refuses_command` part proves the closed-schema invariant (:448) of the frozen
    codec, not the fence.
    """
    quote = {
        "catalog_ref": "SYN-CATALOG",
        "catalog_version": 1,
        "product_ref": "SYN-PRODUCT",
        "profile_ref": "SYN-PROFILE",
    }
    screen_commercial_input(quote, declared_keys=declared("offer.quote"))
    # Injected violation: the DS proxy `cpt_presente=true` rides a commercial command.
    refused(
        lambda: screen_commercial_input(quote | {"cpt_presente": True}, declared_keys=declared("offer.quote"))
    )
    # An UNKNOWN-class key (no health marker at all) collapses into the same refusal.
    refused(
        lambda: screen_commercial_input(
            quote | {"undeclared_slot": True}, declared_keys=declared("offer.quote")
        )
    )
    # Even when a successor DECLARES the key, the health marker still refuses it.
    refused(
        lambda: screen_commercial_input(
            quote | {"cpt_presente": True}, declared_keys=declared("offer.quote") | {"cpt_presente"}
        )
    )
    wire_refuses_command(CommercialWire(schema_bytes), "offer.quote", quote | {"cpt_presente": True})


# ----------------------------------------------------------------------------- EP-REG-10a


def test_intake_descriptor_rejects_health_field(schema_bytes: bytes) -> None:
    validate_intake_descriptor(descriptor(), purpose_allowlist=PURPOSES)
    medication = field(field_ref="toma_medicacao_continua", label="Toma remédio?", choices=["Sim", "Não"])
    # Publication of the descriptor: the canonical validator and the codec's descriptor arm.
    refused(lambda: validate_intake_descriptor(descriptor(fields=[medication]), purpose_allowlist=PURPOSES))
    codec = CommercialWire(schema_bytes, intake_purpose_allowlist=PURPOSES)
    describe = {"stage": "prospect"}
    parse(
        codec,
        envelope("intake.describe", describe),
        {"kind": "intake_descriptor", "descriptor": descriptor()},
    )
    wire_refuses_result(
        codec,
        "intake.describe",
        describe,
        {"kind": "intake_descriptor", "descriptor": descriptor(fields=[medication])},
    )
    # UNKNOWN class (purpose outside the closed set) collapses into the same refusal.
    refused(
        lambda: validate_intake_descriptor(
            descriptor(fields=[field(purpose_ref="SYN-PURPOSE-UNDECLARED")]), purpose_allowlist=PURPOSES
        )
    )
    # Submit: a value for the health field never reaches a source.
    values = {
        "prospect_ref": None,
        "descriptor_ref": "SYN-DESCRIPTOR",
        "descriptor_revision": 1,
        "values": [{"field_ref": "toma_medicacao_continua", "value": "Sim"}],
    }
    refused(lambda: screen_commercial_input(values, declared_keys=declared("prospect.save")))
    wire_refuses_command(codec, "prospect.save", values)


AGE_OR_DISABILITY = re.compile(
    r"(^|_)(idade|etaria|age|ages|birth|nascimento|deficiencia|disability|disabled|pcd)(_|$)"
)


def _identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"[\w.:-]+", node.value)
        ):
            names.add(node.value)
    return {name.casefold() for name in names}


def test_no_age_or_disability_gate() -> None:
    """Lei 9.656 art. 14: no filter, order, hide or refusal by age or disability."""
    document = contract()
    enums: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"filterableFields", "sortableFields"}:
                    enums.extend(item["items"]["enum"])
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    assert enums, "the catalog filter/sort vocabulary moved; re-anchor this guard"
    assert not [value for value in enums if AGE_OR_DISABILITY.search(value.casefold())]
    # The commercial gate path carries no age/disability identifier at all.
    gate_modules = sorted((ROOT / "src" / "maezo" / "gateway").glob("commercial*.py")) + [
        ROOT / "src" / "maezo" / "adapters" / "amh" / "commercial.py",
        ROOT / "src" / "maezo" / "ports" / "commercial.py",
    ]
    offenders = {
        path.name: sorted(name for name in _identifiers(path) if AGE_OR_DISABILITY.search(name))
        for path in gate_modules
    }
    assert not {name: hits for name, hits in offenders.items() if hits}
    # Age prices, never gates: the health fence itself never refuses an age band field.
    age_band = field(
        field_ref="SYN-FIELD-COMPOSITION",
        label="Faixa etária de cada vida",
        purpose_ref="SYN-PURPOSE-COMPOSITION",
        choices=["0 a 18", "19 a 23", "59 ou mais"],
    )
    validate_intake_descriptor(descriptor(fields=[age_band]), purpose_allowlist=PURPOSES)
    assert not health_marker("Faixa etária") and not health_marker("idade")


# ------------------------------------------------------------------------------ TB-E-03-a


def test_prospect_descriptor_rejects_health_label() -> None:
    violation = field(label="Possui alguma doença pré-existente?", value_type="choice")
    refused(
        lambda: validate_intake_descriptor(descriptor("prospect", [violation]), purpose_allowlist=PURPOSES)
    )


def test_prospect_descriptor_rejects_medication_choice() -> None:
    violation = field(choices=["Uso medicação contínua"])
    refused(
        lambda: validate_intake_descriptor(descriptor("prospect", [violation]), purpose_allowlist=PURPOSES)
    )


def test_prefds_descriptor_rejects_purpose_outside_allowlist() -> None:
    contact = field(purpose_ref="SYN-PURPOSE-CONTACT")
    validate_intake_descriptor(descriptor("contact", [contact]), purpose_allowlist=PURPOSES)
    violation = field(purpose_ref="wellness_profile")
    refused(
        lambda: validate_intake_descriptor(descriptor("contact", [violation]), purpose_allowlist=PURPOSES)
    )
    # No allowlist published = no pre-DS descriptor crosses (fail-closed default of the codec).
    refused(lambda: validate_intake_descriptor(descriptor("party", [field()]), purpose_allowlist=frozenset()))


def test_prospect_descriptor_rejects_free_text(schema_bytes: bytes) -> None:
    violation = field(value_type="text", choices=[])
    refused(
        lambda: validate_intake_descriptor(descriptor("prospect", [violation]), purpose_allowlist=PURPOSES),
        CommercialFenceRefusal.PROSPECT_FREE_TEXT,
    )
    validate_intake_descriptor(descriptor("contact", [violation]), purpose_allowlist=PURPOSES)
    wire_refuses_result(
        CommercialWire(schema_bytes, intake_purpose_allowlist=PURPOSES),
        "intake.describe",
        {"stage": "prospect"},
        {"kind": "intake_descriptor", "descriptor": descriptor("prospect", [violation])},
    )


def test_prospect_descriptor_max_five_fields() -> None:
    validate_intake_descriptor(descriptor("prospect", [field()] * 5), purpose_allowlist=PURPOSES)
    refused(
        lambda: validate_intake_descriptor(descriptor("prospect", [field()] * 6), purpose_allowlist=PURPOSES),
        CommercialFenceRefusal.PROSPECT_FIELD_LIMIT,
    )


# ------------------------------------------------------------------------------ TB-E-12-b


@pytest.mark.parametrize(
    "violation", [{"health_answers": [{"q": "SYN", "a": "SYN"}]}, {"ds_status_detail": "SYN"}]
)
def test_journey_snapshot_rejects_health_key(schema_bytes: bytes, violation: dict[str, Any]) -> None:
    """The fence proof is the direct `screen_commercial_input` call (typed refusal).

    The `wire_refuses_command` part proves the closed-schema invariant (:448) of the frozen
    codec, not the fence.
    """
    save = {"quote_ref": "SYN-QUOTE", "source_revision": 1, "terms_digest": "0" * 64}
    CommercialWire(schema_bytes).parse_command(encode(envelope("journey.save", save)))
    # Unknown key (invariant :448) refuses at parse: no command exists, nothing is dispatched
    # or persisted (the router answers 400 before `gateway.execute`).
    wire_refuses_command(CommercialWire(schema_bytes), "journey.save", save | violation)
    refused(lambda: screen_commercial_input(save | violation, declared_keys=declared("journey.save")))


def _snapshot(label: str) -> dict[str, Any]:
    item = journey() | {
        "documents": [],
        "pending_items": [
            {
                "pending_ref": "SYN-PENDING",
                "source_revision": 1,
                "requirement_ref": "SYN-DS-REQUIREMENT",
                "label": label,
                "responsible_party_ref": None,
            }
        ],
    }
    return {"kind": "journey_snapshot", "journeys": [item], "next_cursor": None, "plan_choices": []}


def test_ds_pending_label_closed_vocabulary(schema_bytes: bytes) -> None:
    request = {"journey_ref": None, "cursor": None, "limit": 20}
    codec = CommercialWire(schema_bytes, journey_label_vocabulary=LABELS)
    parse(codec, envelope("journey.get", request), _snapshot("Declaração pendente"))
    screen_journey_labels(_snapshot("Declaração pendente")["journeys"], vocabulary=LABELS)
    # Default codec: no published vocabulary = every label refused, even a closed-set one.
    wire_refuses_result(
        CommercialWire(schema_bytes), "journey.get", request, _snapshot("Declaração pendente")
    )
    for label in (
        "Aguardando entrevista sobre doença cardíaca",
        "Aguardando entrevista sobre hipertensão",
        "Aguardando entrevista sobre diabetes",
        "Tabagista",
        "Etapa adicional do cadastro",  # no health marker at all: outside the closed set
    ):
        violation = _snapshot(label)
        refused(lambda violation=violation: screen_journey_labels(violation["journeys"], vocabulary=LABELS))
        wire_refuses_result(codec, "journey.get", request, violation)


def test_vendor_submission_refuses_health_declaration_class() -> None:
    built, store, audit = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        asyncio.run(
            built.submit(
                TENANT,
                vendor_membership(),
                command(payload=ChannelSubmissionPayload(content_class="health_declaration")),
            )
        )
    assert exc.value.reason is VendorSubmissionRefusal.PHI_IN_COMMERCIAL_INPUT
    assert audit.decisions() == [("DENY", "PHI_IN_COMMERCIAL_INPUT")]
    assert store.count() == 0


# ------------------------------------------------------------------------------ TB-E-24-a


def test_commercial_input_with_clinical_key_refused_typed(schema_bytes: bytes) -> None:
    save = {
        "prospect_ref": None,
        "descriptor_ref": "SYN-DESCRIPTOR",
        "descriptor_revision": 1,
        "values": [{"field_ref": "SYN-FIELD-REGION", "value": "Capital"}],
    }
    admitted = CommercialWire(schema_bytes, intake_purpose_allowlist=PURPOSES)
    admitted.parse_command(encode(envelope("prospect.save", save)))
    screen_commercial_input(
        save, declared_keys=declared("prospect.save"), allowed_field_refs=frozenset({"SYN-FIELD-REGION"})
    )
    violation = save | {"values": [{"field_ref": "known_condition", "value": "SYN"}]}
    refused(lambda: screen_commercial_input(violation, declared_keys=declared("prospect.save")))
    wire_refuses_command(admitted, "prospect.save", violation)
    # UNKNOWN-class field refs (no lexical marker) refuse outside the descriptor's closed set,
    # and the default codec (no published purpose list) refuses every intake value.
    for field_ref in ("possui_diabetes", "usa_insulina", "tabagista"):
        unknown = save | {"values": [{"field_ref": field_ref, "value": "SYN"}]}
        refused(
            lambda unknown=unknown: screen_commercial_input(
                unknown,
                declared_keys=declared("prospect.save"),
                allowed_field_refs=frozenset({"SYN-FIELD-REGION"}),
            )
        )
        wire_refuses_command(CommercialWire(schema_bytes), "prospect.save", unknown)
    submit = {
        "preparation_ref": "SYN-PREPARATION",
        "descriptor_ref": "SYN-DESCRIPTOR",
        "descriptor_revision": 1,
        "values": [{"field_ref": "possui_diabetes", "value": "SYN"}],
    }
    wire_refuses_command(CommercialWire(schema_bytes), "party.submit", submit)
    admitted.parse_command(encode(envelope("party.submit", submit | {"values": []})))


def test_no_dpp_answer_reaches_commercial_snapshot(schema_bytes: bytes) -> None:
    request = {"journey_ref": None, "cursor": None, "limit": 20}
    snapshot = _snapshot("Documento administrativo pendente")
    before = copy.deepcopy(snapshot)
    snapshot["journeys"][0]["health_answers"] = [{"concept": "known_condition", "answer": "SYN"}]
    labelled = CommercialWire(schema_bytes, journey_label_vocabulary=LABELS)
    parse(labelled, envelope("journey.get", request), before)
    wire_refuses_result(labelled, "journey.get", request, snapshot)
    journey_keys = frozenset(contract()["$defs"]["CustomerJourneySnapshot"]["properties"])
    refused(lambda: screen_commercial_input(snapshot["journeys"][0], declared_keys=journey_keys))
    del snapshot["journeys"][0]["health_answers"]
    assert snapshot == before


# ------------------------------------------------------------------------------ TB-E-21-c


def test_commission_structures_have_no_external_operator_dimension() -> None:
    versions = ROOT / "src" / "maezo" / "platform" / "migrations" / "versions"
    migration = (versions / "0029_commission_adjustment_policies.py").read_text(encoding="utf-8")
    check = re.search(r"dimensao text NOT NULL CHECK \(dimensao IN \(([^)]*)\)\)", migration)
    assert check is not None, "the 0029 dimensao CHECK moved; re-anchor this guard"
    sql_dimensions = re.findall(r"'([^']*)'", check.group(1))
    assert sql_dimensions == list(get_args(commission_policies.CommissionDimensao))
    columns = re.findall(r"^\s{4}([a-z_]+) [a-z]", migration, flags=re.MULTILINE)
    fields = {
        name
        for value in vars(commission_policies).values()
        if isinstance(value, type) and hasattr(value, "model_fields")
        for name in value.model_fields
    }
    from maezo.portal.api.commission_policies import commission_policy_router

    routes = [str(getattr(route, "path", "")) for route in commission_policy_router.routes]
    names = [*sql_dimensions, *columns, *sorted(fields), *routes]
    assert len(columns) > 10 and routes
    commission_policies.refuse_commission_benchmark(names)
    # Injected violations: a fourth CHECK dimension or a `competitor_*` field refuses typed.
    for violation in ("operadora_externa", "competitor_rate"):
        with pytest.raises(commission_policies.CommissionBenchmarkForbiddenError) as exc:
            commission_policies.refuse_commission_benchmark([*names, violation])
        assert exc.value.code == "commission_benchmark_forbidden"
    # Runtime: a draft whose target ref names a competitor operator is refused.
    with pytest.raises(ValueError, match="commission_benchmark_forbidden"):
        commission_policies.CommissionAdjustmentDraftSubmission(
            dimensao="canal", dimensao_ref="operadora_externa:SYN", ajuste_tipo="percentual", precedencia=1
        )
    # "registro de corretor exigido pela ANS" copy exists nowhere in the portal surface.
    portal = ROOT / "src" / "maezo" / "portal"
    claim = re.compile(r"registro\s+de\s+corretor[^.\n]{0,80}ANS", re.IGNORECASE)
    assert not [
        path
        for path in portal.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx", ".json", ".md", ".html"}
        and claim.search(path.read_text(encoding="utf-8", errors="replace"))
    ]


# ----------------------------------------------------------------- known false positives


def test_known_false_positives_are_pinned() -> None:
    """Lexical markers over-refuse these non-clinical texts on purpose (conservative fallback).

    Any loosening must edit this pin deliberately.
    """
    assert KNOWN_FALSE_POSITIVES == (
        "Declaração de Saúde pendente",
        "Modalidade: Saúde PME",
        "Forma de tratamento",
        "Clínica credenciada",
        "Tratamento de dados pessoais",
        "Peso do documento",
    )
    assert all(health_marker(text) for text in KNOWN_FALSE_POSITIVES)
