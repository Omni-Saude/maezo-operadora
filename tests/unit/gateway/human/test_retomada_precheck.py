"""P5 (16/10/2026): a `devolvido_agente` note the Helena would bar is refused IN THE PORTAL, synchronously.

The cases are NOT typed here: they are extracted from the existing tests of the Helena's resume
fence (`tests/unit/agents/test_helena_retomada.py` and `test_helena_cerca_afirma_autorizacao.py`),
so the portal answers exactly what the Helena would do with the same note. The fence itself is
imported (`retomada_precheck` calls `motivo_de_recusa_da_retomada`), never copied.
"""

import ast
from pathlib import Path

import pytest
from tests.unit.agents import test_helena_retomada as helena_tests
from tests.unit.gateway.human.test_task_completion_gateway import RecordingCompletion, close, harness
from tests.unit.portal.test_task_completion_api import BODY, PATH, assert_safe, client

from maezo.agents.helena.graph import (
    RETOMADA_RECUSADA_PLACEHOLDER,
    compor_mensagem_de_retomada,
    motivo_de_recusa_da_retomada,
)
from maezo.gateway.human.retomada_precheck import ResumptionRefusedError, check_resumption_note
from maezo.portal.contracts.completions import PortalCompletionError

pytestmark = pytest.mark.asyncio

_AGENT_TESTS = Path(helena_tests.__file__).parent


def _parametrized(test, argname: str) -> list[str]:
    """The values of `@pytest.mark.parametrize(argname, [...])` of an existing test function."""
    for mark in test.pytestmark:
        if mark.name == "parametrize" and mark.args[0] == argname:
            return list(mark.args[1])
    raise AssertionError(f"{test.__name__} has no parametrize({argname!r})")


def _string_in(file: str, function: str, *, call: str | None = None, target: str | None = None) -> str:
    """A string literal of an existing test, read from its source (the assignment or the call arg)."""
    tree = ast.parse((_AGENT_TESTS / file).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
    for node in ast.walk(fn):
        if target and isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == target:
            return node.value.value  # type: ignore[attr-defined]
        if call and isinstance(node, ast.Call) and getattr(node.func, "id", None) == call:
            return node.args[0].value  # type: ignore[attr-defined]
    raise AssertionError(f"no literal found in {file}::{function}")


#: Barred by the Helena (the instructions her own test proves never reach the beneficiary).
BARRED = _parametrized(helena_tests.test_texto_humano_proibido_e_barrado_e_nao_sai, "instrucoes")
#: Let through by the Helena (her composition test, the attendant's fact, and her own placeholder).
ALLOWED = [
    _string_in(
        "test_helena_retomada.py",
        "test_template_e_a_redacao_aprovada_opcao_b",
        call="compor_mensagem_de_retomada",
    ),
    _string_in(
        "test_helena_cerca_afirma_autorizacao.py",
        "test_a_retomada_escrita_por_atendente_nao_e_barrada",
        target="texto",
    ),
    RETOMADA_RECUSADA_PLACEHOLDER,
]


def test_the_cases_were_extracted_and_agree_with_the_helena() -> None:
    assert len(BARRED) == 3 and len(ALLOWED) == 3
    assert all(motivo_de_recusa_da_retomada(compor_mensagem_de_retomada(t)) is not None for t in BARRED)
    assert all(motivo_de_recusa_da_retomada(compor_mensagem_de_retomada(t)) is None for t in ALLOWED)


@pytest.mark.parametrize("note", BARRED)
async def test_a_barred_note_is_refused_with_the_helena_group_before_intent_and_engine(note: str) -> None:
    group, _ = motivo_de_recusa_da_retomada(compor_mensagem_de_retomada(note))  # type: ignore[misc]
    completion = RecordingCompletion()
    g, _, audit = await harness(completion=completion)
    with pytest.raises(ResumptionRefusedError) as refusal:
        await close(g, resultado="devolvido_agente", notas_resolucao=note)
    assert refusal.value.group == group
    assert completion.calls == [] and audit.entries == []  # no intent link, no engine call


@pytest.mark.parametrize("note", BARRED)
@pytest.mark.parametrize("resultado", ["resolvido_humano", "emergencia_acionada"])
async def test_only_devolvido_agente_is_checked(note: str, resultado: str) -> None:
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion)
    await close(g, resultado=resultado, notas_resolucao=note)  # the note never reaches the beneficiary
    assert len(completion.calls) == 1


@pytest.mark.parametrize("note", ALLOWED)
async def test_a_note_the_helena_would_send_completes(note: str) -> None:
    completion = RecordingCompletion()
    g, _, _ = await harness(completion=completion)
    await close(g, resultado="devolvido_agente", notas_resolucao=note)
    assert len(completion.calls) == 1


@pytest.mark.parametrize("note", BARRED)
async def test_the_route_answers_422_with_the_group_never_the_pattern_nor_the_text(note: str) -> None:
    group, pattern = motivo_de_recusa_da_retomada(compor_mensagem_de_retomada(note))  # type: ignore[misc]
    completion = RecordingCompletion()
    connection, g, _ = await client(completion=completion)
    async with connection:
        response = await connection.post(
            PATH, json={**BODY, "resultado": "devolvido_agente", "notas_resolucao": note}
        )
    assert response.status_code == 422
    assert response.json() == {
        "schema": "portal-completion-error.v1",
        "code": "resumption_refused",
        "refusal_group": group,
    }
    assert pattern not in response.text and note not in response.text
    assert completion.calls == [] and g._completion_audit.entries == []


async def test_other_errors_carry_no_refusal_group() -> None:
    connection, _, _ = await client(enabled=False)
    async with connection:
        response = await connection.post(PATH, json=BODY)
    assert_safe(response, 501, "completion_unavailable")  # exact body: no `refusal_group` key


@pytest.mark.parametrize(
    ("code", "group"),
    [("resumption_refused", None), ("invalid_completion", "negativa_clinica")],
)
def test_the_group_goes_with_its_code_and_only_with_it(code: str, group: str | None) -> None:
    with pytest.raises(ValueError):
        PortalCompletionError(code=code, refusal_group=group)  # type: ignore[arg-type]


def test_a_group_the_portal_does_not_name_yet_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import maezo.gateway.human.retomada_precheck as precheck

    monkeypatch.setattr(precheck, "motivo_de_recusa_da_retomada", lambda texto: ("grupo_novo", "x"))
    with pytest.raises(ResumptionRefusedError) as refusal:
        check_resumption_note("devolvido_agente", "qualquer")
    assert refusal.value.group == "outra_regra"
