"""Todo item `hard` tem guarda `ERR_*_NOT_HUMAN` no worker E no BPMN (ADR-0071 I-4; R-020 / DEC-11).

AGENTS.md regra 7: os itens `hard` da matriz de autonomia (negativa, decisao clinica, acusacao de
fraude, ...) sao intocaveis. O PEP pode estar em `shadow`; quem garante "so humano" na pratica sao
as guardas `ERR_*_NOT_HUMAN` dos workers, que recusam o efeito adverso sem decisao humana. Nenhuma
cerca amarrava a lista congelada (`spec/policies/autonomy/_hard_frozen.yaml`) a essas guardas: um item
novo podia entrar sem guarda e ninguem perceberia.

Esta cerca exige, para CADA item da lista congelada:
  * um mapeamento declarado aqui (item sem mapeamento reprova — inclusive um item novo);
  * cada codigo mapeado tem a forma `ERR_*_NOT_HUMAN`;
  * o modulo de worker declarado referencia o codigo em CODIGO (constante, nome ou literal de
    mensagem — docstring e comentario nao contam);
  * o BPMN declarado ancora o codigo: `bpmn:error errorCode=...` (forma padrao) ou, para a guarda
    que DEVOLVE registro `blocked_by_guard` em vez de lancar, a propriedade `enforcedBy` do no
    humano que nomeia a guarda.

A segunda ancora existe por UM caso, `authorization_denial`: em SP-OP-AUTH-001 a guarda
`ERR_DENIAL_NOT_HUMAN` devolve registro (`tools/workers/auth.py`, comentario sobre a allowlist de
codigos BPMN: "the BPMN declares no" errorCode para ela, de proposito) e o BPMN a nomeia em
`enforcedBy`. Exigir `errorCode` ali obrigaria a mudar o BPMN de AUTH — fora deste pacote.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final, Literal

import pytest
import yaml

from maezo.platform.xml_safe import parse_xml_without_dtd

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_HARD_FROZEN: Final[Path] = _REPO_ROOT / "spec" / "policies" / "autonomy" / "_hard_frozen.yaml"
_WORKERS: Final[Path] = _REPO_ROOT / "src" / "maezo" / "tools" / "workers"
_BPMN_DIR: Final[Path] = _REPO_ROOT / "spec" / "processes" / "bpmn"
_GUARD_RE: Final[re.Pattern[str]] = re.compile(r"ERR_[A-Z0-9_]+_NOT_HUMAN")
_BPMN_NS: Final[str] = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_CAMUNDA_NS: Final[str] = "{http://camunda.org/schema/1.0/bpmn}"

Anchor = Literal["errorCode", "enforcedBy"]

#: item hard -> ((codigo, modulo do worker, BPMN, ancora), ...). Fonte de cada linha: o docstring do
#: worker e a `bpmn:documentation` HARD GUARDRAIL do no correspondente.
_HARD_ITEM_GUARDS: Final[dict[str, tuple[tuple[str, str, str, Anchor], ...]]] = {
    "clinical_decision": (
        (
            "ERR_PROGRAM_DISCHARGE_NOT_HUMAN",
            "programa.py",
            "SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn",
            "errorCode",
        ),
    ),
    "authorization_denial": (
        ("ERR_DENIAL_NOT_HUMAN", "auth.py", "SP-OP-AUTH-001_Autorizacao_Previa.bpmn", "enforcedBy"),
    ),
    "nip_manter_negativa": (
        ("ERR_NIP_NEGATIVA_NOT_HUMAN", "nip.py", "SP-OP-NIP-001_Resposta_NIP.bpmn", "errorCode"),
    ),
    "fraud_accusation": (
        (
            "ERR_FRAUD_ACCUSATION_NOT_HUMAN",
            "fraude.py",
            "SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn",
            "errorCode",
        ),
    ),
    "contract_termination": (
        (
            "ERR_CANCELLATION_NOT_HUMAN",
            "cancel.py",
            "SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn",
            "errorCode",
        ),
    ),
}


def _hard_actions() -> list[str]:
    data = yaml.safe_load(_HARD_FROZEN.read_text(encoding="utf-8"))
    return [str(item["action"]) for item in data["hard_items"]]


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _code_references(source: str) -> set[str]:
    """Codigos `ERR_*_NOT_HUMAN` que o modulo usa EM CODIGO (nomes e literais fora de docstring)."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            found.update(_GUARD_RE.findall(node.value))
        elif isinstance(node, ast.Name):
            found.update(_GUARD_RE.findall(node.id))
        elif isinstance(node, ast.Attribute):
            found.update(_GUARD_RE.findall(node.attr))
        elif isinstance(node, ast.alias):
            found.update(_GUARD_RE.findall(node.name))
    return found


def _bpmn_anchors(path: Path) -> dict[Anchor, set[str]]:
    root = parse_xml_without_dtd(path.read_bytes())
    error_codes = {el.get("errorCode", "") for el in root.iter(f"{_BPMN_NS}error") if el.get("errorCode")}
    enforced: set[str] = set()
    for prop in root.iter(f"{_CAMUNDA_NS}property"):
        if prop.get("name") == "enforcedBy":
            enforced.update(_GUARD_RE.findall(prop.get("value", "")))
    return {"errorCode": error_codes, "enforcedBy": enforced}


def test_hard_frozen_list_is_non_vacuous() -> None:
    assert len(_hard_actions()) >= 5


def test_every_hard_item_has_a_declared_guard_and_no_stale_mapping() -> None:
    hard = set(_hard_actions())
    assert hard - set(_HARD_ITEM_GUARDS) == set(), (
        f"item hard sem guarda mapeada: {sorted(hard - set(_HARD_ITEM_GUARDS))} — declare a guarda "
        "ERR_*_NOT_HUMAN do worker e do BPMN (ADR-0071 I-4) antes de congelar o item"
    )
    assert set(_HARD_ITEM_GUARDS) - hard == set(), (
        f"mapeamento para item que nao e mais hard: {sorted(set(_HARD_ITEM_GUARDS) - hard)}"
    )


_ROWS: Final[list[tuple[str, str, str, str, Anchor]]] = [
    (item, code, worker, bpmn, anchor)
    for item, guards in sorted(_HARD_ITEM_GUARDS.items())
    for code, worker, bpmn, anchor in guards
]


@pytest.mark.parametrize(("item", "code", "worker", "bpmn", "anchor"), _ROWS)
def test_guard_is_referenced_by_worker_code(
    item: str, code: str, worker: str, bpmn: str, anchor: Anchor
) -> None:
    assert _GUARD_RE.fullmatch(code), code
    path = _WORKERS / worker
    assert path.is_file(), f"{item}: worker {worker} nao existe"
    assert code in _code_references(path.read_text(encoding="utf-8")), (
        f"{item}: {worker} nao referencia {code} em codigo (so docstring/comentario nao e guarda)"
    )


@pytest.mark.parametrize(("item", "code", "worker", "bpmn", "anchor"), _ROWS)
def test_guard_is_anchored_in_the_bpmn(item: str, code: str, worker: str, bpmn: str, anchor: Anchor) -> None:
    path = _BPMN_DIR / bpmn
    assert path.is_file(), f"{item}: BPMN {bpmn} nao existe"
    anchors = _bpmn_anchors(path)
    assert code in anchors[anchor], f"{item}: {bpmn} nao ancora {code} como {anchor}"


def test_enforced_by_anchor_is_used_only_where_the_bpmn_declares_no_error_code() -> None:
    """A ancora `enforcedBy` e a excecao, nao um atalho: so vale para codigo que o BPMN NAO declara
    como `bpmn:error`. Se um dia o BPMN declarar o `errorCode`, a linha deve migrar para a forma
    padrao."""
    for item, code, _worker, bpmn, anchor in _ROWS:
        if anchor != "enforcedBy":
            continue
        assert code not in _bpmn_anchors(_BPMN_DIR / bpmn)["errorCode"], (
            f"{item}: {bpmn} ja declara errorCode {code} — troque a ancora para errorCode"
        )


def test_code_reference_scanner_ignores_docstrings() -> None:
    """Non-vacuidade do scanner: uma guarda citada so em docstring NAO conta."""
    source = '"""Guard: ERR_X_NOT_HUMAN."""\n\ndef f():\n    """ERR_Y_NOT_HUMAN"""\n    return 1\n'
    assert _code_references(source) == set()
    assert _code_references('MSG = "ERR_X_NOT_HUMAN: humano"\n') == {"ERR_X_NOT_HUMAN"}
