"""PEC-D1: duas fontes autorizadas, pares exatos e recusa de deriva.

Fixtures copiam declarações reais e o helper, mas não executam domínio/serviços.
Só o bootstrap de teste fixa sua raiz privada; pins de produção ficam intactos.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from scripts.ci import pytest_execution_evidence as core
from tests.unit.dev.test_pytest_execution_evidence_repair import BOOTSTRAP, REPO, invoke, prepare

# Alterações literais de 63ca2a84, presentes em a3471496 e ausentes em 14eeb930.
# A transformação só constrói fixtures; produção NÃO normaliza comentários.
HELPER_LINES = [
    [
        "    pool = await sink._ensure_pool()  # noqa: SLF001 — mutation scaffold reaches into the real sink",
        "    pool = await sink._ensure_pool()  # mutation scaffold reaches into the real sink",
    ],
    [
        "        await conn.execute(_ADVISORY_LOCK_SQL, sink._tenant_id)  # noqa: SLF001",
        "        await conn.execute(_ADVISORY_LOCK_SQL, sink._tenant_id)",
    ],
    [
        "        prior_hash: str | None = await conn.fetchval(_DEDUP_LOOKUP_SQL, sink._tenant_id, dedup_key)"
        "  # noqa: SLF001",
        "        prior_hash: str | None = await conn.fetchval(_DEDUP_LOOKUP_SQL, sink._tenant_id, dedup_key)",
    ],
    [
        "        tail = await sink._fetch_tail(conn)  # noqa: SLF001",
        "        tail = await sink._fetch_tail(conn)",
    ],
    [
        "        record.record_hash = record._compute_hash()  # noqa: SLF001",
        "        record.record_hash = record._compute_hash()",
    ],
    ["            record.record_hash,  # noqa: SLF001", "            record.record_hash,"],
    [
        '                f"audit_emit_dedup claim for tenant={sink._tenant_id!r} "  # noqa: SLF001',
        '                f"audit_emit_dedup claim for tenant={sink._tenant_id!r} "',
    ],
    ["    pool2 = await sink._ensure_pool()  # noqa: SLF001", "    pool2 = await sink._ensure_pool()"],
    [
        "        await sink._insert_chain_row(conn2, record)  # noqa: SLF001"
        " — crash-injection lands here in tests",
        "        await sink._insert_chain_row(conn2, record)  # crash-injection lands here in tests",
    ],
]
OLD_HELPER = "8a5b703081314182953a0cf12b844976e2f3908cbf4d5a701b70e3999efa3cc9"
R6_HELPER = "3e84f392e8157d3056036e3f6bcbef94de116199caa3ad55858e33d56aac2d99"
OLD_DECLARATION = "3162a66d5d8d9b7c156926fbafc0cc27c1b39604b3048ea990c6ce28c3f7051b"
R6_DECLARATION = "9b4f076a9dbbe066b22156105aa748aa2ea5172f4c317760587eced421267e21"
NODEID = (
    "tests/integration/chaos/test_sink_down_failclosed.py::"
    "test_c1_down_mutation_check_fail_open_swallow_turns_suite_red"
)


def sources(variant: str) -> tuple[str, str]:
    helper = (REPO / "tests/integration/chaos/mutations.py").read_text()
    assert hashlib.sha256(helper.encode()).hexdigest() in {OLD_HELPER, R6_HELPER}
    for old, new in HELPER_LINES:
        source, target = (new, old) if variant == "baseline" else (old, new)
        helper = helper.replace(source + "\n", target + "\n")
    relative, name = NODEID.split("::")
    text = (REPO / relative).read_text()
    definition = next(
        n for n in ast.parse(text).body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
    )
    body = "".join(
        text.splitlines(keepends=True)[
            min(d.lineno for d in definition.decorator_list) - 1 : definition.end_lineno
        ]
    )
    old = "        await harness._handle(task)  # noqa: SLF001\n"
    new = "        await harness._handle(task)\n"
    body = body.replace(new, old) if variant == "baseline" else body.replace(old, new)
    assert hashlib.sha256(helper.encode()).hexdigest() == (OLD_HELPER if variant == "baseline" else R6_HELPER)
    assert hashlib.sha256(body.encode()).hexdigest() == (
        OLD_DECLARATION if variant == "baseline" else R6_DECLARATION
    )
    return helper, body


@pytest.mark.parametrize("variant", ["baseline", "r6"])
@pytest.mark.parametrize(
    "change",
    [
        "control",
        "hybrid",
        "token",
        "callable",
        "name",
        "inert",
        "body",
        "helper",
        "helper_body",
        "origin",
        "comment",
    ],
)
def test_exact_source_pairs_preserve_c1_and_reject_unreviewed_omissions(
    tmp_path: Path, variant: str, change: str
) -> None:
    helper, body = sources(variant)
    if change == "hybrid":
        _, body = sources("r6" if variant == "baseline" else "baseline")
    elif change == "token":
        body = body.replace('"c1_down"', '"invented"')
    elif change == "callable":
        body = body.replace("broken_emit_once_fail_open_swallow", "broken_start_process_always_start")
    elif change == "name":
        body = body.replace(NODEID.split("::")[1], "test_invented")
    elif change in {"inert", "body"}:
        definition = ast.parse(body).body[0]
        assert isinstance(definition, ast.AsyncFunctionDef)
        if change == "inert":
            definition.body = ast.parse(
                "if False: mutations.broken_emit_once_fail_open_swallow\npytest.skip('unavailable')"
            ).body
        else:
            definition.body.append(ast.Assert(test=ast.Constant(value=False)))
        body = ast.unparse(definition) + "\n"
    elif change == "helper":
        helper += "# unreviewed helper comment\n"
    elif change == "helper_body":
        helper += "\ndef mutation_active(token): return False\n"
    elif change == "comment":
        body += "    # unreviewed declaration comment\n"
    prepare(tmp_path, "def test_control(): assert True\n")
    helper_path = tmp_path / "tests/integration/chaos/mutations.py"
    helper_path.parent.mkdir(parents=True)
    helper_path.write_text(helper)
    relative = NODEID.split("::")[0]
    target = tmp_path / relative
    target.write_text(
        "from __future__ import annotations\nimport pytest, importlib.util\n"
        f"spec=importlib.util.spec_from_file_location('compat_helper', {str(helper_path)!r})\n"
        "mutations=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(mutations)\n" + body
    )
    bootstrap = (
        BOOTSTRAP
        if change == "origin"
        else BOOTSTRAP.replace(
            "plugin = module.EvidencePlugin", "module._COMPANION_ROOT = root\nplugin = module.EvidencePlugin"
        )
    )
    selection = [str(tmp_path / "test_subject.py"), str(target)]
    collect_rc, _, expected = invoke(
        tmp_path, "collect", collect=True, selection=selection, bootstrap=bootstrap
    )
    assert collect_rc == 0
    rc, xml, actual = invoke(tmp_path, "run", selection=selection, bootstrap=bootstrap)
    assert rc == 0
    result = core.validate_execution(xml, actual, expected["collection"], rc)
    if change == "control":
        assert result["return_code"] == 0
        assert result["case_counts"] == {"passed": 1, "inactive_companion": 1}
        assert result["inactive_companions_require_separate_RED"] == 1
    else:
        assert result["return_code"] != 0
        assert result["case_counts"].get("inactive_companion", 0) == 0
