"""Engine variable typing declared BY NAME — independent of the value's magnitude.

Why this module exists (owner decision **R-173** of 2026-09-04, `SP-OP-CONTAS-001`,
WP-CONTRATOS-SYNC): *"Remover a pergunta do pacote de finanças e declarar
`total_glosado_candidato_centavos` como `Long` incondicionalmente no contrato e na variável de
engine, fixando a tipagem por teste que falha se o literal voltar a 32 bits."*

Every CIB Seven / Camunda variable mapper in this codebase types a Python ``int`` by its
MAGNITUDE: inside the Java ``int32`` range it emits ``Integer``, outside it emits ``Long``
(ADR-0018 part 2). For a money-in-cents variable that rule is a landmine, not a policy: the SAME
variable is typed ``Integer`` on a small lote and ``Long`` on a large one, so the wire type of a
financial fact depends on the size of the batch that happened to produce it. `identify_glosa`
already sums in Python's arbitrary-precision integers (`contas.py::identify_glosa`), so the only
32-bit surface left is this serialization boundary — and R-21,47M (``2**31 - 1`` cents) is a real
lote size, not a theoretical one.

The declaration below is therefore NOT a magnitude rule and NOT a financial policy: it is the
list of variable names whose engine type is fixed at ``Long`` for every value, small or large.
Adding a name here is a TYPING decision; no value, ceiling or glosa rule is decided by this
module. It is a leaf module with **zero imports** so the three production mappers
(`tools/workers/harness.py`, `tools/workers/dmn_transport.py`,
`tools/mcp_cibseven/transport.py`) can share ONE declaration without any of them taking a
dependency on the others — which is exactly the drift their "duplicated (not imported)"
docstrings were guarding against.
"""

from __future__ import annotations

# Java `int32` (java.lang.Integer) bounds — the single copy in `src/`, which is the scope the
# fence `test_o_literal_da_faixa_int32_vive_num_unico_lugar` enforces. `tests/integration/
# processes/engine_rest.py` carries a fourth, deliberately excluded copy of the same constants:
# it is test infra that imports NOTHING from `maezo` by design, so importing this module there
# would break the very property that makes it usable without the package. That property is true
# today (measured: `engine_rest.py` imports only `asyncio`, `json`, `dataclasses`, `pathlib`,
# `typing` and `httpx`) and is DECLARED in the module docstring of
# `tests/unit/integration_support/test_engine_rest_ausencia_e_proveniencia.py` — declared, not
# fenced: no test asserts it, so nothing stops a future edit from adding a `maezo` import there.
# An integer outside this range MUST be typed `Long` (int64) or the engine rejects the write with
# "Cannot convert value '<n>' of type 'Integer' to java type java.lang.Integer" (ADR-0018 part 2).
_JAVA_INT32_MIN = -(2**31)
_JAVA_INT32_MAX = 2**31 - 1

#: Engine variables typed `Long` UNCONDITIONALLY, whatever the value (owner decision R-173).
#: `total_glosado_candidato_centavos` (SP-OP-CONTAS-001) is money in integer cents: int32 truncates
#: it above R$ 21.470.000,00, and the wire type of a financial fact must not depend on lote size.
LONG_TYPED_ENGINE_VARS: frozenset[str] = frozenset({"total_glosado_candidato_centavos"})


def camunda_int_type(nome: str | None, valor: int) -> str:
    """Camunda wire type for an integer process/DMN variable — `"Integer"` or `"Long"`.

    A name in `LONG_TYPED_ENGINE_VARS` is `Long` for EVERY value (R-173). Every other name keeps
    the historical magnitude rule. `nome` is ``None`` where the caller has no variable name (a
    bare value conversion) — such a value can only be typed by magnitude.
    """
    if nome is not None and nome in LONG_TYPED_ENGINE_VARS:
        return "Long"
    return "Integer" if _JAVA_INT32_MIN <= valor <= _JAVA_INT32_MAX else "Long"


def validate_declared_long_var(name: str | None, value: object) -> None:
    """Enforce R-173 before raw typing OR caller-shaped variable passthrough.

    Only explicitly declared names are governed here. Signed int64 bounds are wire
    representability, not a financial amount/sign policy. Never coerce a bool,
    float, numeric string or integer subclass. Existing metadata on a valid shaped
    Long remains untouched; other names keep their caller's transport contract.
    """
    if name not in LONG_TYPED_ENGINE_VARS:
        return
    if isinstance(value, dict):
        if value.get("type") != "Long":
            raise ValueError("invalid declared Long engine variable")
        value = value.get("value")
    if type(value) is not int or not -(2**63) <= value <= 2**63 - 1:
        raise ValueError("invalid declared Long engine variable")
