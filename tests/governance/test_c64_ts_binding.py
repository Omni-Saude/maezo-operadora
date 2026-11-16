"""EP-CORE-09c (E-SF-33, caminho B): binding TS gerado do contrato C6-4 + guardas anti-PII nativas.

Spec-fonte: `tests/candidate_artifacts/commercial/contract-c6-4.json` (só leitura) e código Maezo.
Nada aqui deriva de código do omni_sales_platform.

- Pin: o gerador lê os bytes REAIS do contrato e confere o sha256 contra o pin que o repo já usa
  (`config/integrations/amh/commercial-c6-4.source.lock.json`, igual ao codec
  `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`); 1 byte alterado → `SHA_MISMATCH`.
- Regeneração: a saída commitada é idêntica byte a byte à regeneração (`--check`).
- Anti-PII (EXTRACT #10): denylist que falha, com as listas EXISTENTES do Maezo — nenhuma classe
  nova de dado. Nomes: `phi_completeness.PHI_LISTED_NAMES` (= `PHI_FIELDS` ∪ `PHI_PROCESS_VARS`) e
  `PORTABILITY_VERDICT_FIELDS_PROHIBITED`; valores: o `PhiLogRedactor` (padrões CPF/e-mail/telefone).
  Todo campo do binding tem de existir no contrato; nenhum nome da denylist aparece além do que o
  próprio contrato declara.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256
from maezo.gateway.commercial_portability_admission import PORTABILITY_VERDICT_FIELDS_PROHIBITED
from maezo.gateway.log_scrubber import PhiLogRedactor
from maezo.gateway.vendor_suppression import PROHIBITED_FIELD_CLASSES, FieldClass
from maezo.platform.validation.phi_completeness import PHI_LISTED_NAMES

REPO = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO / "src/maezo/portal/web/codegen/c6-4/generate_c64_binding.py"
CONTRACT_PATH = REPO / "tests/candidate_artifacts/commercial/contract-c6-4.json"
GENERATED_PATH = REPO / "src/maezo/portal/web/codegen/c6-4/commercial-c6-4.generated.ts"

#: Nomes da denylist que o PRÓPRIO contrato declara (hoje só `protocolRef`, do suporte). Fixado
#: aqui para que um contrato novo com outro nome da denylist exija revisão humana explícita.
CONTRACT_DECLARED_DENYLIST_HITS = frozenset({"protocolRef"})

_FIELD = re.compile(r'^\s*readonly "((?:[^"\\]|\\.)*)"\??:', re.MULTILINE)
_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("c64_binding_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def _contract_fields() -> frozenset[str]:
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties":
                    names.update(value)
                    for child in value.values():
                        walk(child)
                elif key not in ("const", "enum") and not key.startswith("x-"):
                    walk(value)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(json.loads(CONTRACT_PATH.read_bytes())["$defs"])
    return frozenset(names)


def _screen_binding(text: str) -> set[str]:
    """Denylist que falha: devolve as violações encontradas no texto TS."""
    violations: set[str] = set()
    fields = {json.loads(f'"{raw}"') for raw in _FIELD.findall(text)}
    if fields - _contract_fields():
        violations.add("FIELD_OUTSIDE_CONTRACT")
    if fields & PHI_LISTED_NAMES:
        violations.add("PHI_LISTED_NAME")
    if (fields & PORTABILITY_VERDICT_FIELDS_PROHIBITED) - CONTRACT_DECLARED_DENYLIST_HITS:
        violations.add("PORTABILITY_VERDICT_FIELD")
    redactor = PhiLogRedactor()
    for literal in _LITERAL.findall(text):
        if redactor(None, "info", {"literal": literal})["literal"] != literal:
            violations.add("PHI_SHAPED_LITERAL")
    return violations


# ---------------------------------------------------------------------------------------------
# Pin do contrato
# ---------------------------------------------------------------------------------------------


def test_generator_pin_is_the_repo_pin() -> None:
    pin = gen.load_pin()
    assert pin == CANDIDATE_SCHEMA_SHA256
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == pin


def test_generated_binding_pins_contract_sha(tmp_path: Path) -> None:
    raw = bytearray(CONTRACT_PATH.read_bytes())
    middle = len(raw) // 2
    raw[middle] = (raw[middle] + 1) % 256
    tampered = tmp_path / "contract-c6-4.json"
    tampered.write_bytes(bytes(raw))

    with pytest.raises(gen.ContractPinRefusalError) as refusal:
        gen.render(tampered)
    assert refusal.value.reason is gen.ContractPinRefusal.SHA_MISMATCH
    assert str(refusal.value) == "SHA_MISMATCH"


def test_generator_refuses_lock_without_valid_pin(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"sha256": "d1d796b8"}), encoding="utf-8")
    with pytest.raises(gen.ContractPinRefusalError) as refusal:
        gen.render(CONTRACT_PATH, lock)
    assert refusal.value.reason is gen.ContractPinRefusal.LOCK_INVALID


# ---------------------------------------------------------------------------------------------
# Regeneração determinística
# ---------------------------------------------------------------------------------------------


def test_committed_binding_equals_regeneration() -> None:
    text = gen.render()
    assert text == gen.render()
    assert GENERATED_PATH.read_text(encoding="utf-8") == text
    assert gen.main(["--check"]) == 0


def test_check_mode_refuses_drifted_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    drifted = tmp_path / "commercial-c6-4.generated.ts"
    drifted.write_text(GENERATED_PATH.read_text(encoding="utf-8") + "// editado à mão\n", encoding="utf-8")
    monkeypatch.setattr(gen, "OUTPUT_PATH", drifted)
    assert gen.main(["--check"]) == 1


def test_binding_header_declares_generated_and_contract_sha() -> None:
    head = GENERATED_PATH.read_text(encoding="utf-8").splitlines()[:8]
    assert head[0] == "// GERADO; NÃO EDITAR. Regenere com:"
    assert f"// SHA-256 do contrato: {CANDIDATE_SCHEMA_SHA256}" in head


def test_binding_mirrors_contract_fields_and_commands() -> None:
    text = GENERATED_PATH.read_text(encoding="utf-8")
    fields = {json.loads(f'"{raw}"') for raw in _FIELD.findall(text)}
    assert fields == _contract_fields()
    commands = json.loads(CONTRACT_PATH.read_bytes())["x-commands"]
    for operation in commands:
        assert f"  {json.dumps(operation)}: {{ payloadSchema:" in text


def test_nothing_in_portal_imports_the_binding() -> None:
    web_src = REPO / "src/maezo/portal/web/src"
    hits = [p for p in web_src.rglob("*.ts*") if "commercial-c6-4.generated" in p.read_text(encoding="utf-8")]
    assert hits == []


# ---------------------------------------------------------------------------------------------
# Guardas anti-PII (denylist que falha) sobre o binding gerado
# ---------------------------------------------------------------------------------------------


def test_denylist_premise_tracks_prohibited_classes() -> None:
    # Premissa da denylist: identificativo direto (C1) e clínico-protegido (C3) seguem proibidos
    # no egresso (vendor_suppression §4b). Se o DPO mudar isso, esta guarda precisa ser revista.
    assert {FieldClass.C1_IDENTIFICATIVO, FieldClass.C3_CLINICO_PROTEGIDO} <= PROHIBITED_FIELD_CLASSES


def test_contract_declared_denylist_hits_are_pinned() -> None:
    declared = _contract_fields() & (PHI_LISTED_NAMES | PORTABILITY_VERDICT_FIELDS_PROHIBITED)
    assert declared == CONTRACT_DECLARED_DENYLIST_HITS


def test_generated_binding_passes_anti_pii_screen() -> None:
    assert _screen_binding(GENERATED_PATH.read_text(encoding="utf-8")) == set()


_INJECTION_ANCHOR = 'export type OperationGetPayload = {\n  readonly "target_command_ref": Ref;\n'


@pytest.mark.parametrize(
    ("injected", "expected"),
    [
        ('  readonly "cpf": string;\n', {"FIELD_OUTSIDE_CONTRACT", "PHI_LISTED_NAME"}),
        ('  readonly "diagnostico"?: string;\n', {"FIELD_OUTSIDE_CONTRACT", "PHI_LISTED_NAME"}),
        (
            '  readonly "remainingWaitingDays": number;\n',
            {"FIELD_OUTSIDE_CONTRACT", "PORTABILITY_VERDICT_FIELD"},
        ),
        ('  readonly "kind": "123.456.789-09";\n', {"PHI_SHAPED_LITERAL"}),
        ('  readonly "kind": "titular@example.com";\n', {"PHI_SHAPED_LITERAL"}),
    ],
    ids=["cpf-c1", "diagnostico-clinico", "veredito-portabilidade", "literal-cpf", "literal-email"],
)
def test_anti_pii_screen_refuses_injected_violation(injected: str, expected: set[str]) -> None:
    text = GENERATED_PATH.read_text(encoding="utf-8")
    assert text.count(_INJECTION_ANCHOR) == 1
    violated = text.replace(_INJECTION_ANCHOR, _INJECTION_ANCHOR + injected)
    assert _screen_binding(violated) == expected
