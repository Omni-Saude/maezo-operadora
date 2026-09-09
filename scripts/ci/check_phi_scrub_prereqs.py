#!/usr/bin/env python3
"""CI gate: `scrub_only`/`pseudo_keys` cannot be ratified while its prerequisites are unmet
(R-009, owner decision on gap D7-01 — OWNER-DECISIONS-REGISTER row R-009,
`Bold-Decision Review v2 CEO 2026-09-04`).

Build-time contract (R009-EIR01--06, docs/plan.md Wave 3)
-------------------------------------------------------
Validate readable UTF-8 YAML mappings with unique string keys at every depth before
resolving governance. DRAFT permits known staged modes, no ratification or a fully inert
ratification block, and a boolean template marker. RATIFICADO requires complete runtime
ratification and no true template marker. Unknown/malformed/partial states fail CI even
when the unchanged runtime safely stays OFF. Valid ratified OFF passes.

Canonical effective scrub_only/pseudo_keys requires both frozen prerequisite IDs, exact
boolean flags, a concrete nonsecret receipt-reference URI and an aware positive drain
interval in UTC. A reference-only gate does not fetch receipts or secrets, prove overlay
coverage, verify a signature, prove current provisioning, or prove queues were drained.
Required external receipt contents and actual overlay bindings are documented in the
manifest prerequisite block. Never put key values in the manifest. CLI diagnostics omit
receipt values. Additional pseudo_keys prerequisites are outside this two-item gate.

No ratification field is written. Real key provisioning and operational drain execution
remain operator acts. ADR-0006/0035 and DL-0043/0044/0045 remain unchanged.

Usage
-----
    python scripts/ci/check_phi_scrub_prereqs.py                       # CI gate, real manifest
    python scripts/ci/check_phi_scrub_prereqs.py --manifest PATH       # against another manifest
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
import tempfile
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path("spec/policies/privacy/phi-business-key-remediation.yaml")

_BLOCK_KEY = "pre_requisitos_scrub_only"

#: THE TRACKED SET, code-frozen — mirrors `check_deviation_expiry.py::TRACKED_DEVIATIONS`. Which
#: prerequisites this gate enforces is a governance fact the owner ratified (R-009); a manifest
#: that could silently drop one of its own required ids by editing the YAML would be a record
#: that grades its own homework. Adding/removing a tracked id is therefore a code change under
#: `scripts/ci/` review, never a data-only edit.
TRACKED_PREREQUISITE_IDS: tuple[str, ...] = (
    "phi_hmac_key_provisionado",
    "janela_drenagem_cancel_inad",
)

LEVEL_OK: str = "OK"
LEVEL_FAIL: str = "FAIL"


@dataclass(frozen=True, slots=True)
class Finding:
    """One verdict, either about the manifest as a whole or about one tracked prerequisite."""

    level: str
    prereq_id: str
    headline: str
    detail: str = ""

    def render(self) -> str:
        body = f"[{self.level}] {self.prereq_id}: {self.headline}"
        return f"{body}\n{self.detail}" if self.detail else body


def _is_nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_provisioning_reference(value: Any) -> bool:
    """Nonsecret evidence://store/receipt or HTTPS receipt pointer; never dereferenced here."""
    if not isinstance(value, str) or not 16 <= len(value) <= 2048:
        return False
    # Restrict to unambiguous ASCII URI tokens; reject percent encoding, userinfo and metadata.
    if not re.fullmatch(r"[A-Za-z0-9:/._-]+", value):
        return False
    parts = urlsplit(value)
    if parts.scheme not in {"evidence", "https"} or not parts.netloc or not parts.path:
        return False
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", parts.netloc):
        return False
    segments = parts.path[1:].split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        return False
    if len(segments[-1]) < 8 or "..." in value:
        return False
    tokens = re.split(r"[^a-z0-9]+", value.lower())
    return not set(tokens).intersection(
        {"todo", "pendente", "pending", "placeholder", "changeme", "tbd", "null", "none", "example"}
    )


def _utc_instant(value: Any) -> datetime | None:
    """Require explicit ISO-8601 seconds and zone; never infer a timezone or duration."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        return None
    try:
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() is None:
            return None
        # fromisoformat normalizes out-of-range offset minutes, so validate their syntax too.
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            return None
        return instant.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _valid_drain_interval(start: Any, end: Any) -> bool:
    start_utc, end_utc = _utc_instant(start), _utc_instant(end)
    return start_utc is not None and end_utc is not None and end_utc > start_utc


def _evaluate_item(item: dict[str, Any], prereq_id: str) -> Finding:
    """Evaluate one already-located `pre_requisitos_scrub_only` list entry. Never raises.

    `item` is guaranteed to be a `dict` by the caller (`evaluate`'s block-parsing loop reports a
    non-mapping entry as its own "(bloco)" finding and never hands it to this function) — there is
    deliberately no second `isinstance` guard here for that shape: a check nothing can ever drive
    to its FAIL branch cannot be proven by a red mutation, and an unprovable check is exactly the
    kind of decoration BRIEF-COMMON's "no fabricated success" rule exists to catch.
    """
    atendido = item.get("atendido")
    if not isinstance(atendido, bool):
        return Finding(
            level=LEVEL_FAIL,
            prereq_id=prereq_id,
            headline=f"campo `atendido` desconhecido/malformado: {atendido!r} (esperado true/false booleano)",
            detail=(
                '      Um `atendido` que não é um bool exato (string "true", 1, ausente, ...) '
                "não pode ser lido como satisfeito — a cerca falha fechada."
            ),
        )

    if prereq_id == "phi_hmac_key_provisionado":
        evidencia = item.get("evidencia_provisionamento")
        if not atendido:
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline="PHI_HMAC_KEY ainda não provisionado nos overlays reais",
                detail=(
                    f"      onde: {item.get('onde', '(ausente)')}\n"
                    f"      efeito se ratificado sem isto: "
                    f"{item.get('efeito_se_ratificado_sem_isto', '(ausente)')}"
                ),
            )
        if not _valid_provisioning_reference(evidencia):
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline=(
                    "`atendido: true` mas `evidencia_provisionamento` não é referência válida — "
                    "satisfeito sem prova não conta"
                ),
            )
        return Finding(
            level=LEVEL_OK,
            prereq_id=prereq_id,
            headline="referência registrada; provisionamento real não verificado",
        )

    if prereq_id == "janela_drenagem_cancel_inad":
        janela = item.get("janela_drenagem")
        if not isinstance(janela, dict):
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline="`janela_drenagem` ausente ou não é um mapeamento",
            )
        inicio, fim = janela.get("inicio"), janela.get("fim")
        if not atendido:
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline="janela de drenagem CANCEL/INAD ainda não registrada",
                detail=(
                    f"      onde: {item.get('onde', '(ausente)')}\n"
                    f"      efeito se ratificado sem isto: "
                    f"{item.get('efeito_se_ratificado_sem_isto', '(ausente)')}"
                ),
            )
        if not _valid_drain_interval(inicio, fim):
            return Finding(
                level=LEVEL_FAIL,
                prereq_id=prereq_id,
                headline=("`janela_drenagem` exige instantes com timezone e fim posterior ao início em UTC"),
            )
        return Finding(level=LEVEL_OK, prereq_id=prereq_id, headline=f"janela registrada: {inicio} -> {fim}")

    # Unreachable given TRACKED_PREREQUISITE_IDS is the only source of prereq_id values passed
    # here, but fail closed rather than silently pass an id this function does not know how to
    # grade.
    return Finding(
        level=LEVEL_FAIL,
        prereq_id=prereq_id,
        headline="id rastreado sem regra de avaliação (defeito da cerca)",
    )


def evaluate(data: dict[str, Any]) -> list[Finding]:
    """The whole comparator, as a pure function over an already-parsed manifest mapping.

    Args:
        data: the manifest's top-level YAML mapping (`yaml.safe_load` output). No I/O, no
            environment — every finding is derived from this dict alone.
    """
    modo = _canonical_mode(data.get("modo"))
    if modo is None:
        return [_failure("modo inválido")]
    if modo == "off":
        return [Finding(LEVEL_OK, "(manifesto)", "modo efetivo off")]

    block = data.get(_BLOCK_KEY)
    if not isinstance(block, list):
        return [
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=(
                    f"`{_BLOCK_KEY}` ausente ou não é uma lista — `modo: {modo}` ratificado sem "
                    "como provar os pré-requisitos"
                ),
            )
        ]

    by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    malformed_entries: list[Any] = []
    for entry in block:
        if not isinstance(entry, dict):
            malformed_entries.append(entry)
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            malformed_entries.append(entry)
            continue
        if entry_id in by_id:
            duplicate_ids.append(entry_id)
        by_id[entry_id] = entry

    findings: list[Finding] = []
    for entry in malformed_entries:
        findings.append(
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=(
                    f"entrada malformada em `{_BLOCK_KEY}` — não é um mapeamento com `id` "
                    f"string não-vazio: {entry!r}"
                ),
            )
        )
    if duplicate_ids:
        findings.append(
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=f"id(s) duplicado(s) em `{_BLOCK_KEY}`: {sorted(set(duplicate_ids))}",
            )
        )

    unknown_ids = sorted(set(by_id) - set(TRACKED_PREREQUISITE_IDS))
    if unknown_ids:
        findings.append(
            Finding(
                level=LEVEL_FAIL,
                prereq_id="(bloco)",
                headline=(
                    f"id(s) não rastreado(s) em `{_BLOCK_KEY}`: {unknown_ids} — adicionar um "
                    "pré-requisito novo exige atualizar TRACKED_PREREQUISITE_IDS neste script, em "
                    "PR revisado (scripts/ci/ é CODEOWNED), não apenas editar o YAML"
                ),
            )
        )

    for prereq_id in TRACKED_PREREQUISITE_IDS:
        if prereq_id not in by_id:
            findings.append(
                Finding(
                    level=LEVEL_FAIL,
                    prereq_id=prereq_id,
                    headline=f"pré-requisito ausente do bloco `{_BLOCK_KEY}`",
                )
            )
            continue
        findings.append(_evaluate_item(by_id[prereq_id], prereq_id))

    return findings


def exit_code_for(findings: list[Finding]) -> int:
    """1 iff anything failed."""
    return 1 if any(f.level == LEVEL_FAIL for f in findings) else 0


# ---------------------------------------------------------------------------------------------
# Non-vacuity self-check — prove the comparator can go red AND green before trusting it.
# ---------------------------------------------------------------------------------------------

SELF_CHECK_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("draft", LEVEL_OK),
    ("normalized_active_missing", LEVEL_FAIL),
    ("partial_promotion", LEVEL_FAIL),
    ("placeholder_reference", LEVEL_FAIL),
    ("zero_utc_window", LEVEL_FAIL),
    ("malformed_draft", LEVEL_FAIL),
    ("ratificado_modo_off", LEVEL_OK),
    ("ratificado_scrub_only_tudo_atendido", LEVEL_OK),
    ("ratificado_scrub_only_hmac_nao_atendido", LEVEL_FAIL),
    ("ratificado_scrub_only_janela_nao_atendida", LEVEL_FAIL),
    ("ratificado_scrub_only_atendido_sem_evidencia", LEVEL_FAIL),
    ("ratificado_scrub_only_bloco_ausente", LEVEL_FAIL),
    ("ratificado_scrub_only_atendido_malformado", LEVEL_FAIL),
    ("ratificado_pseudo_keys_herda_prerequisitos", LEVEL_FAIL),
)

#: `ratificacao` block shared by every "fully ratified" self-check fixture — the three
#: accountability fields `_is_ratified` (in `phi_key_policy.py`) requires, synthetic but complete.
_RATIFICACAO_COMPLETA: dict[str, Any] = {
    "ratificado": True,
    "revisor": "self-check",
    "ratificado_em": "2026-09-06",
}

_MET_HMAC_ITEM: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": True,
    "evidencia_provisionamento": "evidence://synthetic-fixture/deployment-check-20260906",
}
_MET_JANELA_ITEM: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": True,
    "janela_drenagem": {"inicio": "2026-10-01T02:00:00-03:00", "fim": "2026-10-01T04:00:00-03:00"},
}
_UNMET_HMAC_ITEM: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": False,
    "evidencia_provisionamento": "",
}
_UNMET_JANELA_ITEM: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": False,
    "janela_drenagem": {"inicio": None, "fim": None},
}


def build_self_check_cases() -> tuple[tuple[str, dict[str, Any]], ...]:
    """Materialize `SELF_CHECK_SCENARIOS` into `(name, manifest dict)`. Raises on drift.

    Every "ratificado_*" fixture carries a COMPLETE `ratificacao` block: `run_gate` resolves
    ratification through the real `load_phi_key_policy`, which — correctly — treats an
    incomplete `ratificacao` exactly like `DRAFT` (nothing in force). A self-check fixture with
    `status: RATIFICADO` and no `ratificacao` block would therefore silently exercise the SAME
    "not ratified" branch as the `draft` scenario, teaching this self-check nothing about the
    RATIFICADO branch it is named for.
    """
    manifests: dict[str, dict[str, Any]] = {
        "draft": {"status": "DRAFT", "modo": "scrub_only"},
        "ratificado_modo_off": {"status": "RATIFICADO", "modo": "off", "ratificacao": _RATIFICACAO_COMPLETA},
        "ratificado_scrub_only_tudo_atendido": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_MET_HMAC_ITEM, _MET_JANELA_ITEM],
        },
        "ratificado_scrub_only_hmac_nao_atendido": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_UNMET_HMAC_ITEM, _MET_JANELA_ITEM],
        },
        "ratificado_scrub_only_janela_nao_atendida": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_MET_HMAC_ITEM, _UNMET_JANELA_ITEM],
        },
        "ratificado_scrub_only_atendido_sem_evidencia": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [
                {"id": "phi_hmac_key_provisionado", "atendido": True, "evidencia_provisionamento": "   "},
                _MET_JANELA_ITEM,
            ],
        },
        "ratificado_scrub_only_bloco_ausente": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
        },
        "ratificado_scrub_only_atendido_malformado": {
            "status": "RATIFICADO",
            "modo": "scrub_only",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [
                {"id": "phi_hmac_key_provisionado", "atendido": "true", "evidencia_provisionamento": "x"},
                _MET_JANELA_ITEM,
            ],
        },
        "ratificado_pseudo_keys_herda_prerequisitos": {
            "status": "RATIFICADO",
            "modo": "pseudo_keys",
            "ratificacao": _RATIFICACAO_COMPLETA,
            _BLOCK_KEY: [_UNMET_HMAC_ITEM, _MET_JANELA_ITEM],
        },
    }
    complete = manifests["ratificado_scrub_only_tudo_atendido"]
    normalized = copy.deepcopy(complete)
    normalized["modo"] = " SCRUB_ONLY "
    del normalized[_BLOCK_KEY]
    partial = copy.deepcopy(complete)
    partial["ratificacao"]["ratificado"] = False
    placeholder = copy.deepcopy(complete)
    placeholder[_BLOCK_KEY][0]["evidencia_provisionamento"] = "TODO"
    zero = copy.deepcopy(complete)
    zero[_BLOCK_KEY][1]["janela_drenagem"] = {
        "inicio": "2026-10-01T01:00:00-03:00",
        "fim": "2026-10-01T04:00:00Z",
    }
    manifests.update(
        normalized_active_missing=normalized,
        partial_promotion=partial,
        placeholder_reference=placeholder,
        zero_utc_window=zero,
        malformed_draft={"status": "DRAFT", "modo": "unknown"},
    )
    declared = [name for name, _ in SELF_CHECK_SCENARIOS]
    if sorted(manifests) != sorted(declared):
        raise RuntimeError(
            f"self-check scenarios drifted: SELF_CHECK_SCENARIOS declares {sorted(declared)} "
            f"but build_self_check_cases builds {sorted(manifests)}"
        )
    return tuple((name, manifests[name]) for name, _ in SELF_CHECK_SCENARIOS)


def self_check() -> list[str]:
    """Drive `run_gate` over synthetic manifests written to temp files. Empty == healthy.

    Goes through `run_gate` (loader + evaluate together), not `evaluate` alone, so the self-check
    proves the SAME pipeline `main()` runs, including the real ratification gate — never a
    shortcut that could pass while the wired pipeline is broken.
    """
    problems: list[str] = []
    expected_by_name = dict(SELF_CHECK_SCENARIOS)
    with tempfile.TemporaryDirectory(prefix="phi-scrub-prereqs-self-check-") as tmp_dir:
        for name, manifest in build_self_check_cases():
            tmp_path = Path(tmp_dir) / f"{name}.yaml"
            tmp_path.write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
            findings = run_gate(tmp_path)
            actual = LEVEL_FAIL if any(f.level == LEVEL_FAIL for f in findings) else LEVEL_OK
            expected = expected_by_name[name]
            if actual != expected:
                problems.append(
                    f"self-check '{name}': esperava {expected}, veio {actual} "
                    f"({[f.render() for f in findings]})"
                )
        for name, payload in (
            ("missing", None),
            ("invalid_utf8", b"\xff"),
            ("non_mapping", b"[]"),
            ("malformed_yaml", b"status: ["),
            ("nested_duplicate", b"status: DRAFT\nmodo: off\nextra: {atendido: false, atendido: true}\n"),
        ):
            tmp_path = Path(tmp_dir) / f"{name}.yaml"
            if payload is not None:
                tmp_path.write_bytes(payload)
            if exit_code_for(run_gate(tmp_path)) != 1:
                problems.append(f"self-check '{name}': expected ingestion failure")
    return problems


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


class _UniqueMappingLoader(yaml.SafeLoader):
    """Reject duplicate/non-string keys, including nested mappings and merge overrides."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        self.flatten_mapping(node)
        result: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise yaml.constructor.ConstructorError(
                    None, None, "invalid or duplicate mapping key", node.start_mark
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _load_manifest(manifest_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Strict build-time ingestion; errors are bounded and do not echo manifest contents."""
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        data = yaml.load(raw_text, Loader=_UniqueMappingLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError, ValueError):
        return None, "manifesto ilegível ou YAML UTF-8 inválido/duplicado"
    if not isinstance(data, dict):
        return None, "raiz deve ser mapeamento"
    return data, None


def _failure(message: str) -> Finding:
    return Finding(LEVEL_FAIL, "(manifesto)", message)


def _canonical_mode(raw: Any) -> str | None:
    # Runtime's canonical parser also handles the YAML 1.1 bare-off -> False spelling.
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from maezo.platform.privacy.phi_key_policy import _parse_mode

    parsed = _parse_mode(raw)
    return parsed.value if parsed is not None else None


def _validate_state(data: dict[str, Any]) -> str | None:
    """Build schema, deliberately stricter than runtime OFF-on-invalid recovery."""
    status = data.get("status")
    if not isinstance(status, str) or status.strip().upper() not in {"DRAFT", "RATIFICADO"}:
        return "status inválido"
    if _canonical_mode(data.get("modo")) is None:
        return "modo inválido"
    if "version" in data and (type(data["version"]) is not int or data["version"] != 1):
        return "version inválida"
    if "unratified" in data and type(data["unratified"]) is not bool:
        return "unratified deve ser booleano"
    rat = data.get("ratificacao")
    if status.strip().upper() == "DRAFT":
        if "ratificacao" not in data:
            return None
        if not isinstance(rat, dict) or rat.get("ratificado") is not False:
            return "DRAFT com ratificação parcial/contraditória"
        if any(field not in rat or rat[field] is not None for field in ("revisor", "ratificado_em")):
            return "DRAFT exige assinaturas nulas"
        return None
    if data.get("unratified") is True:
        return "RATIFICADO contradiz unratified"
    if not isinstance(rat, dict) or rat.get("ratificado") is not True:
        return "RATIFICADO exige ratificação completa"
    if any(not _is_nonblank_str(rat.get(field)) for field in ("revisor", "ratificado_em")):
        return "RATIFICADO exige revisor e data"
    return None


def _validate_inert_block(data: dict[str, Any]) -> list[Finding]:
    """Absent block is inert; a present block must be well formed even when inactive."""
    if _BLOCK_KEY not in data:
        return []
    block = data[_BLOCK_KEY]
    if not isinstance(block, list):
        return [_failure("bloco de pré-requisitos inválido")]
    seen: set[str] = set()
    for item in block:
        if not isinstance(item, dict):
            return [_failure("entrada de pré-requisito inválida")]
        ident = item.get("id")
        if not isinstance(ident, str) or ident not in TRACKED_PREREQUISITE_IDS or ident in seen:
            return [_failure("id de pré-requisito inválido/duplicado")]
        seen.add(ident)
        if type(item.get("atendido")) is not bool:
            return [_failure("atendido deve ser booleano")]
        if item["atendido"]:
            finding = _evaluate_item(item, ident)
            if finding.level == LEVEL_FAIL:
                return [finding]
        elif ident == "phi_hmac_key_provisionado":
            value = item.get("evidencia_provisionamento")
            if value not in (None, "") and not _valid_provisioning_reference(value):
                return [_failure("referência de pré-requisito inválida")]
        else:
            window = item.get("janela_drenagem")
            if not isinstance(window, dict) or set(window) != {"inicio", "fim"}:
                return [_failure("janela de pré-requisito inválida")]
            start, end = window["inicio"], window["fim"]
            if (start is not None or end is not None) and not _valid_drain_interval(start, end):
                return [_failure("intervalo de pré-requisito inválido")]
    if seen != set(TRACKED_PREREQUISITE_IDS):
        return [_failure("bloco de pré-requisitos incompleto")]
    return []


def run_gate(manifest_path: Path) -> list[Finding]:
    """Strict artifact validation -> canonical runtime state -> prerequisite evaluation."""
    data, error = _load_manifest(manifest_path)
    if error is not None or data is None:
        return [_failure(error or "manifesto inválido")]
    state_error = _validate_state(data)
    if state_error:
        return [_failure(state_error)]
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from maezo.platform.privacy.phi_key_policy import load_phi_key_policy

    # Read the immutable bytes we validated, not a second mutable read of the caller path.
    with tempfile.TemporaryDirectory(prefix="phi-scrub-validated-") as tmp:
        validated_path = Path(tmp) / "manifest.yaml"
        validated_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        policy = load_phi_key_policy(validated_path)
    if data["status"].strip().upper() == "RATIFICADO" and not policy.ratificado:
        return [_failure("ratificação recusada pelo carregador canônico")]
    if not policy.scrubbing_enabled:
        errors = _validate_inert_block(data)
        return errors or [Finding(LEVEL_OK, "(manifesto)", "manifesto válido e inerte; modo efetivo off")]
    return evaluate({**data, "modo": policy.modo.value})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help=f"manifesto a verificar (default: {DEFAULT_MANIFEST})",
    )
    args = parser.parse_args(argv)

    problems = self_check()
    if problems:
        print("[phi-scrub-prereqs] FAIL: o auto-teste de não-vacuidade não passou —", file=sys.stderr)
        print("  um comparador que não se prova capaz de reprovar não vale como prova.", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path

    # Strict build validation precedes the unchanged canonical runtime resolution.
    try:
        findings = run_gate(manifest_path)
    except Exception as exc:  # An unimportable loader is itself a fail-closed finding.
        print(f"[phi-scrub-prereqs] FAIL: não consegui avaliar o manifesto ({exc})", file=sys.stderr)
        return 1

    code = exit_code_for(findings)
    stream = sys.stderr if code else sys.stdout
    print(f"[phi-scrub-prereqs] {manifest_path}", file=stream)
    for finding in findings:
        print(f"  {finding.render()}", file=stream)

    if code:
        print(
            "\n[phi-scrub-prereqs] FAIL: pré-requisito(s) de `scrub_only`/`pseudo_keys` não "
            "atendido(s) (R-009, owner decision D7-01). Preencher o item em "
            "`pre_requisitos_scrub_only` com evidência real e `atendido: true` é um ato de DADOS, "
            "no MESMO PR de ratificação — esta cerca nunca assina `status`/`ratificacao.*` por "
            "ninguém.",
            file=sys.stderr,
        )
    else:
        print("[phi-scrub-prereqs] PASS")
    return code


if __name__ == "__main__":
    sys.exit(main())
