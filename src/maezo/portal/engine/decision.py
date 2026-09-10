"""D3/D5 immutable classified wire adapter; the shape grants no engine qualification.

Canonical bytes are the gateway carrier's exact bytes and digest. Clinical basis stays
in PHI custody, separately named; this adapter never resolves it into General fields.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .profile import _DECIMAL, _DIGEST, _REF, ProfileError, canonicalize, strict_loads

_CLASSIFIED_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}")

_TARGET = frozenset(
    {
        "schema_version",
        "command_id",
        "task_id",
        "process_definition_key",
        "process_definition_version",
        "process_definition_id",
        "process_definition_digest",
        "task_definition_key",
        "form_key",
        "form_version",
        "form_digest",
        "expected_task_revision",
        "expected_evidence_revision",
        "expected_evidence_digest",
        "expected_membership_revision",
        "expected_authority_revision",
    }
)
_BINDINGS = {
    ("SP-OP-AUTH-001", "UT_AnaliseMedicoAuditor"): "auth_decisao",
    ("SP-OP-AUTH-001", "UT_CoordenacaoAssume"): "auth_decisao",
    ("SP-OP-AUTH-001", "UT_RegistrarParecerJunta"): "auth_junta",
    ("SP-OP-ESCALATION-001", "UT_TratarEscalonamento"): "escalation",
    ("SP-OP-ESCALATION-001", "UT_SupervisorAssume"): "escalation",
    ("SP-OP-PAGTO-001", "UT_AnaliseAdmissibilidade"): "pagto_admissibilidade",
}
_OUTCOMES = {
    "auth_decisao": ("decisao_auditor", {"APROVAR", "NEGAR", "SOLICITAR_INFO", "JUNTA_MEDICA"}),
    "auth_junta": ("decisao_auditor", {"APROVAR", "NEGAR"}),
    "escalation": ("resultado", {"resolvido_humano", "devolvido_agente", "emergencia_acionada"}),
    "pagto_admissibilidade": ("decisao_admissibilidade", {"PROSSEGUIR", "DEVOLVER"}),
}


def _keys(value: Any, keys: set[str] | frozenset[str]) -> None:
    if type(value) is not dict or value.keys() != keys:
        raise ProfileError("invalid classified decision")


def _text(value: Any, pattern: Any = _REF) -> None:
    if type(value) is not str or not pattern.fullmatch(value):
        raise ProfileError("invalid classified decision")


@dataclass(frozen=True, slots=True)
class HumanDecisionCommand:
    """Validated, byte-preserving adapter to dedicated outbox/signing mechanics."""

    raw: bytes

    def __post_init__(self) -> None:
        if type(self.raw) is not bytes:
            raise ProfileError("invalid classified decision")
        c = strict_loads(self.raw)
        _keys(
            c,
            {
                "schema",
                "scope",
                "principal_ref",
                "principal_issuer",
                "principal_subject",
                "target",
                "binding_digest",
                "evidence_ref",
                "request_digest",
                "audit_intent_ref",
                "outcome",
                "human_basis",
            },
        )
        if c["schema"] != "human-classified-decision.v1" or canonicalize(c) != self.raw:
            raise ProfileError("invalid classified decision")
        _keys(c["scope"], {"tenant", "environment", "workload_ref"})
        for value in c["scope"].values():
            _text(value)
        for name in ("principal_ref", "principal_subject", "evidence_ref", "audit_intent_ref"):
            _text(c[name])
        issuer = c["principal_issuer"]
        if type(issuer) is not str or not issuer or any(ord(x) < 33 or ord(x) == 127 for x in issuer):
            raise ProfileError("invalid classified decision")
        if c["principal_ref"] == c["scope"]["workload_ref"]:
            raise ProfileError("invalid classified decision")
        _text(c["evidence_ref"], _CLASSIFIED_REF)
        for name in ("binding_digest", "request_digest"):
            _text(c[name], _DIGEST)
        t = c["target"]
        _keys(t, _TARGET)
        for name, value in t.items():
            pattern = (
                _DIGEST
                if name.endswith("digest")
                else (_DECIMAL if name.endswith(("revision", "version")) else _REF)
            )
            _text(value, pattern)
        if t["schema_version"] != "1" or t["form_version"] == "0" or t["process_definition_version"] == "0":
            raise ProfileError("invalid classified decision")
        kind = _BINDINGS.get((t["process_definition_key"], t["task_definition_key"]))
        if kind is None or kind != t["form_key"]:
            raise ProfileError("invalid classified decision")
        field, outcomes = _OUTCOMES[kind]
        _keys(c["outcome"], {"kind", field})
        _text(c["outcome"][field])
        if c["outcome"]["kind"] != kind or c["outcome"][field] not in outcomes:
            raise ProfileError("invalid classified decision")
        _keys(c["human_basis"], {"custody_ref", "content_digest"})
        _text(c["human_basis"]["custody_ref"], _CLASSIFIED_REF)
        _text(c["human_basis"]["content_digest"], _DIGEST)
        intent = hashlib.sha256(canonicalize([c["scope"], t["task_id"], t["command_id"]])).hexdigest()
        if c["audit_intent_ref"] != intent:
            raise ProfileError("invalid classified decision")

    @property
    def canonical(self) -> bytes:
        return self.raw

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    def __getattr__(self, name: str) -> Any:
        c = strict_loads(self.raw)
        if name == "operation":
            return "decision"
        if name == "assignee_ref":
            return c["principal_ref"]
        if name in ("tenant", "environment", "workload_ref"):
            return c["scope"][name]
        if name in c:
            return c[name]
        if name in c["target"]:
            return c["target"][name]
        if name in (
            "task_revision",
            "evidence_revision",
            "evidence_digest",
            "membership_revision",
            "authority_revision",
        ):
            return c["target"]["expected_" + name]
        raise AttributeError(name)
