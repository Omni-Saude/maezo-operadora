"""E-SF-12 — cerca de não-contato (EP-REG-20a) + pernas do lado store do GT-4 (ADD-A9-CONSENT §6).

Escada de reuso: das cinco guardas de EP-REG-20a, três já estão pinadas, com a mesma violação e a
mesma recusa, em `tests/unit/test_commercial_admission_a6.py` (referenciadas, não duplicadas —
`test_e_sf_12_reused_guards_are_still_present` falha se alguma sumir ou mudar de nome). Este
arquivo só traz o que ninguém pina: a recusa `PROCESS_KEY_NAO_PERMITIDA` pelo PEP inteiro, o
registro que nunca existe sem deadline (independente do calendário de dias úteis), o seam que o
daemon vivo não serve, e as pernas do GT-4 que dependem do store: oposição `registered` ainda não
honrada já suprime, e `beneficiario` não tem suporte em SP-OP-SUPP-001 (UNKNOWN, nunca zero).
A perna de entrega do GT-4 (OutboundGate) não existe neste head e não é testada aqui.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import typing
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.gateway import action_execution
from maezo.gateway.effect_pep import AgentCapabilities, DecisionContext, EffectLayer, decide_effect
from maezo.gateway.vendor_suppression import (
    EscalationClock,
    PostgresSuppressionStore,
    SuppressionCommand,
    SuppressionError,
    SuppressionKey,
    SuppressionRecord,
    SuppressionRefusalReason,
    build_contact_list,
    suppression_ref_of,
)
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.suppression import ERR_SUPP_SUBJECT_UNRESOLVED, make_verify_subject_handler

_REPO = Path(__file__).resolve().parents[3]
_T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_pep(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mesmo isolamento de `test_effect_pep.py`: nenhum manifesto em cache nem env do ambiente."""
    monkeypatch.delenv(action_execution.MANIFEST_PATH_ENV, raising=False)
    monkeypatch.delenv(action_execution.OVERRIDE_ENFORCEMENT_ENV, raising=False)
    for name in action_execution.RUNTIME_MODE_ENVS:
        monkeypatch.delenv(name, raising=False)
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


# ---------------------------------------------------------------------------- fakes de engine


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, engine: _RowsEngine) -> None:
        self._engine = engine

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: object, params: object) -> _Result:
        del params
        self._engine.executed.append(str(statement))
        return _Result(self._engine.rows)


class _RowsEngine:
    """AsyncEngine mínimo: devolve as linhas dadas e guarda o SQL executado (só leitura)."""

    dialect = SimpleNamespace(name="postgresql")
    echo = False

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[str] = []

    def connect(self) -> _Conn:
        return _Conn(self)

    def begin(self) -> _Conn:
        raise AssertionError("I/O de escrita alcançado — a recusa devia vir antes")


def _record_values(**changes: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "tenant": "t-1",
        "suppression_ref": suppression_ref_of("subj-1", "chan-A"),
        "subject_ref": "subj-1",
        "contact_channel": "chan-A",
        "canal": "whatsapp",
        "categoria_sujeito": "lead",
        "t_recorded": _T0,
        "t_lembrete": _T0 + timedelta(days=9),
        "t_escalonado": _T0 + timedelta(days=16),
        "t_deadline": _T0 + timedelta(days=21),
        "status": "pendente",
        "motivo": "oposicao",
    }
    values.update(changes)
    return values


# ------------------------------------------------------------- EP-REG-20a (c): key do processo


def test_supp_key_not_agent_startable() -> None:
    # Violação: agente declara a ferramenta de início e a key SP-OP-SUPP-001 e tenta iniciar.
    caps = AgentCapabilities.of(
        principal="rafael",
        tools=("mcp-cibseven.start_process",),
        process_keys=("SP-OP-SUPP-001",),
    )
    decision = decide_effect(
        tenant="amh",
        principal="rafael",
        operation="cibseven.start_process",
        process_key="SP-OP-SUPP-001",
        ctx=DecisionContext(capabilities=caps),
    )
    assert decision.allow is False
    assert decision.reason == "PROCESS_KEY_NAO_PERMITIDA"
    assert decision.layer == EffectLayer.CAPACIDADE.value


# ------------------------------------------------------------- EP-REG-20a (d): sem deadline


@pytest.mark.parametrize(
    ("changes", "naive"),
    [
        ({"t_deadline": datetime(2026, 11, 1, 10, 0)}, True),
        ({"t_deadline": None}, False),
    ],
)
def test_record_without_deadline_refused(changes: dict[str, object], naive: bool) -> None:
    SuppressionRecord.model_validate(_record_values())  # controle positivo
    if naive:
        with pytest.raises(SuppressionError) as exc:
            SuppressionRecord.model_validate(_record_values(**changes))
        assert exc.value.reason is SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE
    else:
        with pytest.raises(ValidationError):
            SuppressionRecord.model_validate(_record_values(**changes))
    missing = _record_values()
    del missing["t_deadline"]
    with pytest.raises(ValidationError):
        SuppressionRecord.model_validate(missing)


def test_store_never_writes_a_record_whose_clock_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: object, **kwargs: object) -> EscalationClock:
        raise SuppressionError(SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE)

    monkeypatch.setattr(EscalationClock, "compute", _refuse)
    store = PostgresSuppressionStore(_RowsEngine([]))  # type: ignore[arg-type]
    command = SuppressionCommand(
        tenant="t-1",
        subject_ref="subj-1",
        contact_channel="chan-A",
        canal="whatsapp",
        categoria_sujeito="lead",
        motivo="oposicao",
    )
    with pytest.raises(SuppressionError) as exc:
        asyncio.run(store.record(command))
    assert exc.value.reason is SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE


# ------------------------------------------------------------- portão: seam não servido no vivo


def test_live_composition_never_threads_the_suppression_seam() -> None:
    calls: list[tuple[str, int, list[str | None]]] = []
    for path in sorted((_REPO / "src" / "maezo").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "register_default_workers":
                calls.append((str(path.relative_to(_REPO)), node.lineno, [k.arg for k in node.keywords]))
    assert calls, "nenhuma chamada de register_default_workers encontrada — a cerca ficaria vazia"
    for where, line, keywords in calls:
        assert None not in keywords, f"{where}:{line} usa **splat — o seam poderia entrar escondido"
        served = {"suppression_store", "suppression_admission"} & set(keywords)
        assert not served, f"{where}:{line} serve {sorted(served)} sem ato OWNER-DEPLOY (registro A6)"


# ------------------------------------------------------------- GT-4, pernas do store


def test_gt4_registered_unhonored_opposition_is_suppressed_in_list_build() -> None:
    engine = _RowsEngine([{"subject_ref": "subj-opp", "contact_channel": "chan-A"}])
    store = PostgresSuppressionStore(engine)  # type: ignore[arg-type]
    candidates = [SuppressionKey("subj-opp", "chan-A"), SuppressionKey("subj-ok", "chan-A")]
    kept = asyncio.run(build_contact_list("t-1", candidates, store=store))
    assert kept == (SuppressionKey("subj-ok", "chan-A"),)
    # A consulta de vigentes não filtra por status: `registered` (pendente) já suprime.
    assert len(engine.executed) == 1
    assert "status" not in engine.executed[0].lower()
    # Oposição prevalece sobre consentimento: não há entrada por onde uma permissão a anule.
    assert set(inspect.signature(build_contact_list).parameters) == {
        "tenant",
        "candidate_keys",
        "store",
        "telemetry",
    }


def test_gt4_beneficiario_has_no_suppression_support() -> None:
    for model in (SuppressionCommand, SuppressionRecord):
        annotation = model.model_fields["categoria_sujeito"].annotation
        assert typing.get_args(annotation) == ("vendedor", "lead")
    with pytest.raises(ValidationError):
        SuppressionCommand.model_validate(
            {
                "tenant": "t-1",
                "subject_ref": "subj-1",
                "contact_channel": "chan-A",
                "canal": "whatsapp",
                "categoria_sujeito": "beneficiario",
                "motivo": "oposicao",
            }
        )


def test_gt4_beneficiario_row_resolves_unknown_never_a_record() -> None:
    lead = _record_values()
    lead_store = PostgresSuppressionStore(_RowsEngine([lead]))  # type: ignore[arg-type]
    resolved = asyncio.run(lead_store.resolve("t-1", lead["suppression_ref"]))
    assert resolved is not None and resolved.categoria_sujeito == "lead"  # controle positivo

    row = _record_values(categoria_sujeito="beneficiario")
    store = PostgresSuppressionStore(_RowsEngine([row]))  # type: ignore[arg-type]
    with pytest.raises(SuppressionError) as exc:
        asyncio.run(store.resolve("t-1", row["suppression_ref"]))
    assert exc.value.reason is SuppressionRefusalReason.SOURCE_UNAVAILABLE

    handler = make_verify_subject_handler(store)
    task = SimpleNamespace(
        variables={"tenant_id": "t-1", "subject_ref": "subj-1", "contact_channel": "chan-A"}
    )
    with pytest.raises(WorkerBpmnError) as bpmn:
        asyncio.run(handler(task))  # type: ignore[arg-type]
    assert bpmn.value.error_code == ERR_SUPP_SUBJECT_UNRESOLVED


# ------------------------------------------------------------- índice de reuso


_REUSED: dict[str, frozenset[str]] = {
    "tests/unit/test_commercial_admission_a6.py": frozenset(
        {
            "test_honrado_without_evidence_refuses_before_any_io",
            "test_honrado_with_a_non_model_evidence_object_refuses_before_any_io",
            "test_store_without_the_registered_admission_refuses_loud_at_boot",
            "test_record_without_the_dpo_role_never_parses",
            "test_the_pep_universe_drops_the_suppression_key_even_when_declared",
            "test_both_process_allowlists_stay_fifteen_without_the_key",
            "test_default_composition_never_threads_the_store",
            "test_naive_clock_refuses_deadline_uncomputable_before_any_io",
        }
    ),
    "tests/unit/gateway/test_vendor_suppression.py": frozenset(
        {
            "test_store_ausente_recusa_e_nunca_trata_como_conjunto_vazio",
            "test_store_ilegivel_recusa_com_a_mesma_forma",
        }
    ),
    "tests/unit/tools/workers/test_suppression.py": frozenset(
        {"test_verify_subject_sem_store_levanta_o_erro_modelado"}
    ),
}


def test_e_sf_12_reused_guards_are_still_present() -> None:
    for relative, names in _REUSED.items():
        tree = ast.parse((_REPO / relative).read_text(encoding="utf-8"))
        defined = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert names <= defined, f"{relative}: guardas reusadas sumiram: {sorted(names - defined)}"
