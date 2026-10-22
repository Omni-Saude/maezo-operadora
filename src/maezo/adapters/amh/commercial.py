"""Candidate AMH commercial wire adapter; NOT a published contract pin.

The source-owned executable schema bytes are injected by trusted composition and
matched to the frozen C6-4 successor digest. No schema, endpoint destination, source grant or
credential can come from a request. A matching candidate digest is never live
publication. The separately composed executor must prove publication and source
currentness and atomically enforce the actual AMH command contract.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, cast, get_args
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from maezo.ports.commercial import (
    CommercialCodec,
    CommercialCommand,
    CommercialDeliveryOrigins,
    CommercialOperation,
    CommercialPreparation,
    CommercialPublication,
    CommercialRefusalError,
    CommercialResultAuthority,
    CommercialScope,
    CommercialSource,
    CommercialStatus,
    IssuerDocumentOrigins,
    OperationalResult,
    PaymentChoiceSelection,
    TermsPresentation,
    TermsSelection,
    command_digest,
    scope_digest,
)

SCHEMA_VERSION = "omni-commercial.proposed.v1"
CANDIDATE_SCHEMA_SHA256 = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"
AMH_COMMAND_PATH = "/interop/sales/v1/commercial"
MAX_BODY_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("commercial_wire_invalid")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError("commercial_wire_invalid")


def _json(body: bytes, limit: int) -> dict[str, Any]:
    if type(body) is not bytes or not body or len(body) > limit:
        raise ValueError("commercial_wire_invalid")
    parsed = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if type(parsed) is not dict:
        raise ValueError("commercial_wire_invalid")
    return cast(dict[str, Any], parsed)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _strip_replay_actions(value: Any, depth: int = 0) -> Any:
    if depth > 32:
        raise ValueError
    if isinstance(value, dict):
        return {
            key: None
            if key in {"hosted_action", "hosted_document", "external_handoff"}
            else []
            if key == "plan_choices"
            else {
                "status": "unavailable",
                "scope": dict(item["scope"]),
                "reason": "refresh_required",
                "choices": [],
                "default_card_choice_ref": None,
            }
            if key == "payment_capabilities" and isinstance(item, dict)
            else _strip_replay_actions(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_strip_replay_actions(item, depth + 1) for item in value]
    return value


class CommercialWire(CommercialCodec):
    """Closed runtime validation against source-owned bytes, not an editable local copy."""

    def __init__(
        self,
        schema_bytes: bytes,
        *,
        hosted_origins: dict[str, frozenset[str]] | None = None,
        issuer_document_origins: IssuerDocumentOrigins | None = None,
        delivery_origins: CommercialDeliveryOrigins | None = None,
    ) -> None:
        try:
            if hashlib.sha256(schema_bytes).hexdigest() != CANDIDATE_SCHEMA_SHA256:
                raise ValueError
            document = _json(schema_bytes, 2 * 1024 * 1024)
            Draft202012Validator.check_schema(document)
            self._commands: dict[str, Any] = document["x-commands"]
            if set(self._commands) != set(get_args(CommercialOperation)):
                raise ValueError
            self._request = Draft202012Validator(
                {"$ref": "#/$defs/Command", "$defs": document["$defs"]},
                format_checker=FormatChecker(),
            )
            self._result = Draft202012Validator(
                {"$ref": "#/$defs/OperationalResult", "$defs": document["$defs"]},
                format_checker=FormatChecker(),
            )
            self._provider_methods: dict[str, list[str]] = document["x-provider-methods"]
            self._public = Draft202012Validator(
                {"$ref": "#/$defs/PublicCatalogSourceResponse", "$defs": document["$defs"]},
                format_checker=FormatChecker(),
            )
            if delivery_origins is not None and not isinstance(delivery_origins, CommercialDeliveryOrigins):
                raise ValueError
            self._delivery_origins = delivery_origins
            # Qualified deployment supplies exact official provider HTTPS origins.
            # No default arbitrary hosted redirect is accepted.
            if issuer_document_origins is not None and not isinstance(
                issuer_document_origins, IssuerDocumentOrigins
            ):
                raise ValueError
            self._issuer_document_origins = issuer_document_origins
            self._origins = dict(hosted_origins or {})
            for provider, origins in self._origins.items():
                if provider not in self._provider_methods or type(origins) is not frozenset:
                    raise ValueError
                for origin in origins:
                    parsed = urlsplit(origin)
                    if (
                        parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                        or parsed.path
                        or parsed.query
                        or parsed.fragment
                        or parsed.port not in (None, 443)
                    ):
                        raise ValueError
        except Exception:
            raise ValueError("commercial_candidate_invalid") from None

    @property
    def schema_digest(self) -> str:
        return CANDIDATE_SCHEMA_SHA256

    def parse_command(self, body: bytes) -> CommercialCommand:
        try:
            value = _json(body, MAX_BODY_BYTES)
            if not self._request.is_valid(value):
                raise ValueError
            name = value["command"]
            payload = value["payload"]
            if (
                name == "payment.create"
                and payload["method"] not in self._provider_methods[payload["provider"]]
            ):
                raise ValueError
            if name in {"catalog.draft", "catalog.import"}:
                self._catalog_rows(payload["rows"])
            return CommercialCommand(
                command=cast(CommercialOperation, name),
                command_ref=value["command_ref"],
                expected_revision=value["expected_revision"],
                wire_bytes=_canonical(value),
                terms_selection=TermsSelection(
                    quote_ref=payload["quote_ref"],
                    source_revision=payload["source_revision"],
                    terms_digest=payload["terms_digest"],
                    terms_review_ref=payload.get("terms_review_ref"),
                    decision=payload.get("decision"),
                )
                if name in {"offer.terms", "acceptance.record"}
                else None,
                payment_selection=PaymentChoiceSelection(
                    choice_ref=payload["choice_ref"],
                    scope_kind="receivable" if name == "payment.create" else "contract",
                    scope_ref=payload["receivable_ref"]
                    if name == "payment.create"
                    else payload["contract_ref"],
                    provider=payload["provider"],
                    method=payload["method"] if name == "payment.create" else "card",
                    operation=name,
                    use=("invoice_with_mandate" if payload["mandate_ref"] else "one_time")
                    if name == "payment.create"
                    else "mandate_authorization",
                )
                if name in {"payment.create", "mandate.create"}
                else None,
            )
        except Exception:
            raise CommercialRefusalError("denied", "invalid_request") from None

    @staticmethod
    def _catalog_rows(rows: list[dict[str, Any]]) -> None:
        if not rows or len({row["product_ref"] for row in rows}) != len(rows):
            raise ValueError
        for row in rows:
            associations = row["network_associations"]
            identities = {(a["establishment_ref"], a["unit_ref"], a["service_ref"]) for a in associations}
            if len(identities) != len(associations):
                raise ValueError

    @staticmethod
    def _time(value: str, *, future: bool) -> None:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        if instant.utcoffset() is None or (instant <= now if future else instant > now):
            raise ValueError

    @classmethod
    def _deadlines(cls, value: Any) -> None:
        """Pure final pass after ALL source/origin calls, including nested views."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "valid_until" and "quote_ref" in value and value.get("kind") != "quote":
                    # Saved quote context is history, not a renewed offer/action.
                    continue
                if item is not None and key in {"expires_at", "valid_until", "validUntil"}:
                    cls._time(item, future=True)
                elif item is not None and key in {"verified_at", "verifiedAt", "observed_at"}:
                    cls._time(item, future=False)
                else:
                    cls._deadlines(item)
        elif isinstance(value, list):
            for item in value:
                cls._deadlines(item)

    @staticmethod
    def _capabilities(data: dict[str, Any], kind: str, ref: str, *, recovered: bool) -> None:
        capability = data["payment_capabilities"]
        if capability["scope"] != {"kind": kind, "ref": ref}:
            raise ValueError
        if recovered:
            if capability["status"] != "unavailable" or capability["reason"] != "refresh_required":
                raise ValueError
            return
        if capability["status"] == "unavailable":
            return
        choices = capability["choices"]
        if len({c["choice_ref"] for c in choices}) != len(choices):
            raise ValueError
        default = capability["default_card_choice_ref"]
        if default is not None and not any(
            c["choice_ref"] == default
            and c["provider"] == "stripe"
            and c["method"] == "card"
            and c["operation"] == "payment.create"
            and c["use"] == "one_time"
            for c in choices
        ):
            raise ValueError

    def _delivery(self, value: dict[str, Any], *, kind: str, resource: str, ref: str, revision: int) -> None:
        if self._delivery_origins is None:
            raise ValueError
        origins = self._delivery_origins.current_origins(
            kind=kind, resource_ref=resource, document_ref=ref, source_revision=revision
        )
        if type(origins) is not frozenset:
            raise ValueError
        url = urlsplit(value["url"])
        origin = f"{url.scheme}://{url.netloc}"
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or url.port not in (None, 443)
            or origin not in origins
        ):
            raise ValueError
        for allowed in origins:
            parsed = urlsplit(allowed)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.port not in (None, 443)
            ):
                raise ValueError

    @staticmethod
    def _needs_current(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("kind") in {
                "work_item",
                "work_queue",
                "journey_saved",
                "journey_snapshot",
                "support_handoff",
            }:
                return True
            if (
                isinstance(value.get("payment_capabilities"), dict)
                and value["payment_capabilities"]["status"] == "qualified"
            ):
                return True
            return any(CommercialWire._needs_current(item) for item in value.values())
        return isinstance(value, list) and any(CommercialWire._needs_current(item) for item in value)

    def parse_public_result(self, body: bytes) -> bytes:
        """Pure wire validation; caller must bind actual current public publication."""
        try:
            data = _json(body, MAX_RESULT_BYTES)
            self._public.validate(data)
            if data.get("schemaVersion") != "sales-catalog-read.proposed.v3" or data["status"] != "usable":
                raise ValueError
            if data["source"]["publicationState"] != "published" or data["withdrawal"] != {
                "state": "none_published"
            }:
                raise ValueError
            if len(data["products"]) > 100 or len({p["optionRef"] for p in data["products"]}) != len(
                data["products"]
            ):
                raise ValueError

            def known(value: Any) -> None:
                if isinstance(value, dict):
                    if value.get("dataState") == "unknown" and len(value) != 1:
                        raise ValueError
                    if value.get("dataState") == "known" and len(value) <= 1:
                        raise ValueError
                    for item in value.values():
                        known(item)
                elif isinstance(value, list):
                    for item in value:
                        known(item)

            known(data)
            for product in data["products"]:
                network = product["network"]
                if network["dataState"] == "known" and not {"networkRef", "verifiedAt"} <= network.keys():
                    raise ValueError
                associations = product["networkAssociations"]
                if len({a["publicAssociationRef"] for a in associations}) != len(associations):
                    raise ValueError
                if len({(a["establishmentRef"], a["unitRef"], a["serviceRef"]) for a in associations}) != len(
                    associations
                ):
                    raise ValueError
            self._deadlines(data)
            return _canonical(data)
        except Exception:
            raise CommercialRefusalError("unavailable", "source_unavailable") from None

    def _hosted(self, value: dict[str, Any]) -> None:
        action = value.get("hosted_action")
        if action is None:
            return
        # None is source-declared unknown validity, never a synthesized expiry.
        if action["expires_at"] is not None:
            expires_at = datetime.fromisoformat(action["expires_at"].replace("Z", "+00:00"))
            if expires_at.utcoffset() is None or expires_at <= datetime.now(UTC):
                raise ValueError
        url = urlsplit(action["url"])
        origin = f"{url.scheme}://{url.netloc}"
        # Stripe's source-issued hosted Checkout Session URL has opaque fragment
        # data. Validate only; never decode, rebuild or persist that ephemeral URL.
        stripe_fragment = value["provider"] == "stripe" and origin == "https://checkout.stripe.com"
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or (url.fragment and not stripe_fragment)
            or url.port not in (None, 443)
        ):
            raise ValueError
        if origin not in self._origins.get(value["provider"], frozenset()):
            raise ValueError

    def _document(self, data: dict[str, Any]) -> None:
        document = data["hosted_document"]
        if document is None or self._issuer_document_origins is None:
            raise ValueError
        if document["expires_at"] is not None:
            expiry = datetime.fromisoformat(document["expires_at"].replace("Z", "+00:00"))
            if expiry.utcoffset() is None or expiry <= datetime.now(UTC):
                raise ValueError
        url = urlsplit(document["url"])
        origin = f"{url.scheme}://{url.netloc}"
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or url.port not in (None, 443)
        ):
            raise ValueError
        selection = TermsSelection(
            quote_ref=data["quote_ref"],
            source_revision=data["source_revision"],
            terms_digest=data["terms_digest"],
        )
        origins = self._issuer_document_origins.current_origins(selection, data["terms_review_ref"])
        if type(origins) is not frozenset or origin not in origins:
            raise ValueError
        for allowed in origins:
            parsed = urlsplit(allowed)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.port not in (None, 443)
            ):
                raise ValueError

        # Origin qualification can perform IO. Check the known source expiry
        # only after it returns, with no further authority calls before return.
        if document["expires_at"] is not None:
            expiry = datetime.fromisoformat(document["expires_at"].replace("Z", "+00:00"))
            if expiry.utcoffset() is None or expiry <= datetime.now(UTC):
                raise ValueError

    @staticmethod
    def _journey_continuation(journey: dict[str, Any]) -> None:
        """Match source continuation facts; opaque refs still need result authority."""
        contexts = journey["quote_contexts"]
        for context in contexts:
            quote = context["quote"]
            if quote is not None and any(
                quote[field] != context[field]
                for field in ("quote_ref", "source_revision", "terms_digest", "valid_until")
            ):
                raise ValueError
        linkage = journey["party_linkage"]
        if linkage is not None and any(
            linkage[field] != journey[field]
            for field in ("prospect_ref", "account_ref", "party_ref", "payer_ref")
        ):
            raise ValueError
        acceptance = journey["commercial_acceptance"]
        if acceptance is not None:
            if linkage is None or acceptance["party_linkage_ref"] != linkage["linkage_ref"]:
                raise ValueError
            matches = [
                context
                for context in contexts
                if all(
                    acceptance[field] == context[field]
                    for field in ("quote_ref", "source_revision", "terms_digest")
                )
            ]
            if len(matches) != 1:
                raise ValueError
        # OP03 CustomerAcceptanceRead remains a separate nominal projection. It
        # is never read as a fallback for this source commercial acceptance.
        contract = journey["contract"]
        issued = contract["issued_receipt"] if contract is not None else None
        if issued is not None:
            if journey["payer_ref"] is not None and issued["payer_ref"] != journey["payer_ref"]:
                raise ValueError
            if linkage is not None and issued["party_linkage_ref"] != linkage["linkage_ref"]:
                raise ValueError
            if acceptance is not None and (
                issued["accepted_receipt_ref"] != acceptance["acceptance_receipt_ref"]
                or issued["quote_ref"] != acceptance["quote_ref"]
                or issued["terms_digest"] != acceptance["terms_digest"]
                or acceptance["decision"] != "accepted"
            ):
                raise ValueError

    def _projection(
        self, data: dict[str, Any], name: str, payload: dict[str, Any], *, recovered: bool = False
    ) -> None:
        if data["kind"] != self._commands[name]["result_kind"]:
            raise ValueError
        for ref in (
            "operator_ref",
            "catalog_ref",
            "contract_ref",
            "receivable_ref",
            "mandate_ref",
            "proposal_ref",
            "action_ref",
            "work_ref",
            "journey_ref",
        ):
            if ref in payload and payload[ref] is not None and ref in data and payload[ref] != data[ref]:
                raise ValueError
        if data["kind"] == "catalog":
            self._catalog_rows(data["rows"])
            if (
                name == "catalog.get"
                and payload.get("version") is not None
                and data["version"] != payload["version"]
            ):
                raise ValueError
        if data["kind"] in {"operator", "contract", "receivable"}:
            kind = data["kind"]
            self._capabilities(data, kind, data[kind + "_ref"], recovered=recovered)
        if data["kind"] == "operator_choices":
            for connection in data["payment_connections"]:
                self._capabilities(
                    connection, "connection", connection["connection_ref"], recovered=recovered
                )
        if data["kind"] == "contract":
            issued = data["issued_receipt"]
            if (data["state"] == "issued") != (issued is not None):
                raise ValueError
            if issued is not None and issued["contract_ref"] != data["contract_ref"]:
                raise ValueError
            if name == "contract.issue" and (
                data["state"] != "issued"
                or any(
                    field in payload and issued[field] != payload[field]
                    for field in ("quote_ref", "terms_digest", "party_linkage_ref")
                )
                or (
                    "acceptance_receipt_ref" in payload
                    and issued["accepted_receipt_ref"] != payload["acceptance_receipt_ref"]
                )
            ):
                raise ValueError
        if (
            data["kind"] == "receivable"
            and "billing_obligation_ref" in payload
            and data["billing_obligation_ref"] != payload["billing_obligation_ref"]
        ):
            raise ValueError
        if data["kind"] == "payment":
            payment = data["payment"]
            self._capabilities(payment, "payment", payment["attempt_ref"], recovered=recovered)
            self._hosted(payment)
            if payment["method"] not in self._provider_methods[payment["provider"]]:
                raise ValueError
            for field in ("attempt_ref", "receivable_ref", "method", "provider"):
                if field in payload and payment[field] != payload[field]:
                    raise ValueError
            if payment["state"] in {"paid", "settled"} and payment["paid_receipt_ref"] is None:
                raise ValueError
            if payment["state"] == "settled" and payment["settled_receipt_ref"] is None:
                raise ValueError
            if payment["tasy_ack_state"] == "acknowledged" and payment["tasy_ack_receipt_ref"] is None:
                raise ValueError
        if data["kind"] == "mandate":
            mandate = data.get("mandate", data)
            self._capabilities(mandate, "mandate", mandate["mandate_ref"], recovered=recovered)
            self._hosted(mandate)
            if "mandate_ref" in payload and mandate["mandate_ref"] != payload["mandate_ref"]:
                raise ValueError
            if mandate["state"] in {"active", "revoked"} and mandate["provider_receipt_ref"] is None:
                raise ValueError

        if data["kind"] == "journey_saved":
            for key in ("quote_ref", "source_revision", "terms_digest"):
                if key in payload and data[key] != payload[key]:
                    raise ValueError
        if (
            data["kind"] == "work_item"
            and payload.get("work_ref") is not None
            and data["item"]["work_ref"] != payload["work_ref"]
        ):
            raise ValueError
        if data["kind"] == "work_queue" and len({item["work_ref"] for item in data["items"]}) != len(
            data["items"]
        ):
            raise ValueError
        if data["kind"] == "journey_snapshot":
            choices = data["plan_choices"]
            if len({choice["option_ref"] for choice in choices}) != len(choices) or (recovered and choices):
                raise ValueError
            journeys = data["journeys"]
            if len({j["journey_ref"] for j in journeys}) != len(journeys):
                raise ValueError
            if payload.get("journey_ref") is not None and any(
                j["journey_ref"] != payload["journey_ref"] for j in journeys
            ):
                raise ValueError
            for journey in journeys:
                self._journey_continuation(journey)
                if journey["contract"] is not None:
                    self._projection(journey["contract"], "contract.get", {}, recovered=recovered)
                for document in journey["documents"]:
                    if document["hosted_document"] is not None:
                        if recovered or document["state"] != "available":
                            raise ValueError
                        self._delivery(
                            document["hosted_document"],
                            kind="journey_document",
                            resource=journey["journey_ref"],
                            ref=document["document_ref"],
                            revision=document["source_revision"],
                        )
        if data["kind"] == "support_handoff" and data["external_handoff"] is not None:
            if recovered:
                raise ValueError
            self._delivery(
                data["external_handoff"],
                kind="support_channel",
                resource=data["journey_ref"],
                ref=data["handoff_ref"],
                revision=data["source_revision"],
            )

        if data["kind"] == "terms_presentation":
            for field in ("quote_ref", "source_revision", "terms_digest"):
                if field in payload and data[field] != payload[field]:
                    raise ValueError
            if recovered:
                if data["hosted_document"] is not None:
                    raise ValueError
            else:
                self._document(data)
        if data["kind"] == "acceptance":
            for field in (
                "quote_ref",
                "source_revision",
                "party_linkage_ref",
                "terms_digest",
                "terms_review_ref",
            ):
                if field in payload and data[field] != payload[field]:
                    raise ValueError
            if (
                "decision" in payload
                and data["decision"] != {"accept": "accepted", "decline": "declined"}[payload["decision"]]
            ):
                raise ValueError

    def _recovered(self, data: dict[str, Any], payload: dict[str, Any]) -> None:
        if data["target_command_ref"] != payload["target_command_ref"]:
            raise ValueError
        original = data["original_result"]
        if original is None:
            return
        if data["original_command"] == "operation.get":
            raise ValueError
        # C2 forbids durable/replayed hosted links regardless of claimed expiry.
        # Closed schema validation already rejects extra personal/proof fields.
        original = _strip_replay_actions(original)
        data["original_result"] = original
        if original["status"] == "succeeded":
            self._projection(original["data"], data["original_command"], {}, recovered=True)

    def parse_result(self, body: bytes, command: CommercialCommand) -> OperationalResult:
        try:
            request = _json(self.parse_command(command.wire_bytes).wire_bytes, MAX_BODY_BYTES)
            value = _json(body, MAX_RESULT_BYTES)
            if not self._result.is_valid(value):
                raise ValueError
            if value["status"] == "succeeded":
                data = value["data"]
                name = command.command
                payload = request["payload"]
                self._projection(data, name, payload)
                if name == "operation.get":
                    self._recovered(data, payload)
                    if not self._result.is_valid(value):
                        raise ValueError
            if value["status"] == "succeeded" and command.command != "operation.get":
                self._deadlines(value["data"])
            return OperationalResult(
                status=cast(CommercialStatus, value["status"]),
                operation_ref=value["operation_ref"],
                reason_code=value["reason_code"],
                wire_bytes=_canonical(value),
                terms_presentation=TermsPresentation(
                    terms_review_ref=value["data"]["terms_review_ref"],
                    review_state=value["data"]["review_state"],
                    presentation_receipt_ref=value["data"]["presentation_receipt_ref"],
                )
                if value["status"] == "succeeded" and command.command == "offer.terms"
                else None,
                authorization_command=cast(CommercialOperation, value["data"]["original_command"])
                if value["status"] == "succeeded" and command.command == "operation.get"
                else command.command,
                requires_current_result=value["status"] == "succeeded" and self._needs_current(value["data"]),
            )
        except Exception:
            raise CommercialRefusalError("unavailable", "source_unavailable") from None

    def refusal(
        self,
        status: CommercialStatus,
        reason_code: str,
        operation_ref: str | None = None,
    ) -> OperationalResult:
        value = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "operation_ref": operation_ref,
            "reason_code": reason_code,
            "data": None,
        }
        if not self._result.is_valid(value):
            raise ValueError("commercial_result_invalid")
        return OperationalResult(
            status=status,
            operation_ref=operation_ref,
            reason_code=reason_code,
            wire_bytes=_canonical(value),
        )


class GovernedCommercialExecutor(ABC):
    """Source-qualified internal gateway seam, with no default HTTP/credential binding.

    `publication` verifies actual publication/current grants, not just the C5 hash.
    For terms and acceptance, preparation resolves current issuer presentation
    evidence for the exact quote/terms and scope. The actual acceptance writer
    independently checks review/actor/representation/receipt currentness atomically;
    a Maezo dataclass, M2M credential or preflight never substitutes for that check.
    Missing issuer/presentation provider refuses. Direct completed offer.terms replay
    returns unavailable/terms_presentation_unavailable, never a stored document URL.
    `prepare` resolves authentic current receipts and resource membership; `execute`
    targets only AMH_COMMAND_PATH and atomically rechecks scope, receipts and source
    versions. Recovery additionally checks current original owner/source BEFORE
    lookup, strips hosted links before durable storage and never redispatches the
    original command. It never treats context values or a candidate as authority.
    Unknown Tasy writes remain unavailable. No retries after an ambiguous effect.
    """

    @abstractmethod
    async def publication(self, scope: CommercialScope) -> CommercialPublication: ...

    @abstractmethod
    async def prepare(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
    ) -> CommercialPreparation: ...

    @abstractmethod
    async def execute(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
        preparation: CommercialPreparation,
    ) -> bytes: ...

    async def result_authority(
        self, command: CommercialCommand, scope: CommercialScope, result: OperationalResult
    ) -> CommercialResultAuthority:
        raise CommercialRefusalError("unavailable", "source_unavailable")


class AmhCommercialAdapter(CommercialSource):
    """No executor or source publication is inferred from successful schema validation."""

    def __init__(self, *, wire: CommercialWire, executor: GovernedCommercialExecutor) -> None:
        if not isinstance(wire, CommercialWire) or not isinstance(executor, GovernedCommercialExecutor):
            raise ValueError("commercial_source_unqualified")
        self.wire = wire
        self.executor = executor

    async def admit(self, scope: CommercialScope) -> CommercialPublication:
        proof = await self.executor.publication(scope)
        if (
            not isinstance(proof, CommercialPublication)
            or proof.shared_contract_digest != CANDIDATE_SCHEMA_SHA256
        ):
            raise CommercialRefusalError("unavailable", "binding_unqualified")
        return proof

    async def prepare(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
    ) -> CommercialPreparation:
        if self.wire.parse_command(command.wire_bytes) != command:
            raise CommercialRefusalError("denied", "invalid_request")
        await self.admit(scope)
        return await self.executor.prepare(command, scope)

    async def execute(
        self,
        command: CommercialCommand,
        scope: CommercialScope,
        preparation: CommercialPreparation,
    ) -> OperationalResult:
        if (
            self.wire.parse_command(command.wire_bytes) != command
            or not isinstance(preparation, CommercialPreparation)
            or preparation.command_digest != command_digest(command)
            or preparation.scope_digest != scope_digest(scope)
            or preparation.valid_until.utcoffset() is None
            or preparation.valid_until <= datetime.now(UTC)
            or await self.admit(scope) != preparation.publication
        ):
            raise CommercialRefusalError("denied", "source_stale")
        return self.wire.parse_result(await self.executor.execute(command, scope, preparation), command)

    async def result_authority(
        self, command: CommercialCommand, scope: CommercialScope, result: OperationalResult
    ) -> CommercialResultAuthority:
        return await self.executor.result_authority(command, scope, result)
