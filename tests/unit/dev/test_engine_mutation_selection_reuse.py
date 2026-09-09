"""Uniao MUI/privacidade: uma tentativa nova nao reutiliza selecao anterior."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_engine_integration as runner

from tests.unit.dev.test_engine_mutation_interface import NODEID, _fixture


@pytest.mark.parametrize("fault", ["nodeid", "inactive", "source-during", "discovery-source", "run-source"])
def test_new_attempt_invalidates_only_owned_mutation_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    _fixture(tmp_path, monkeypatch, NODEID, "assert True")
    out = tmp_path / "out"
    out.mkdir()
    log = out / "collect-mutation.log"
    runner._collect(tmp_path, [NODEID], log, mutation_nodeid=NODEID)
    active = runner._collected_items[log]
    selection = out / "mutation-selection.json"
    runner._json_write(selection, {"nodeid": NODEID, "active_items": active, "previous_attempt": True})
    previous_log = log.read_bytes()
    previous_evidence = log.with_suffix(".execution.json").read_bytes()
    pending = selection.with_name(f".{selection.name}.{os.getpid()}.tmp")
    pending.write_text("previous interrupted publication")
    foreign = out / "unrelated-evidence.json"
    foreign.write_text("preserve unrelated evidence")
    foreign_temp = out / ".mutation-selection.json.foreign-pid.tmp"
    foreign_temp.write_text("preserve foreign temporary")
    inactive: list[dict[str, Any]] = [
        {
            **active[0],
            "inactive_companion": {
                "mutation": "b1a",
                "obligation": "separate_opt_in_RED_required",
            },
        }
    ]
    if fault in {"source-during", "discovery-source"}:
        source = tmp_path / NODEID.split("::")[0]
        source.write_text(source.read_text() + "\n# owned synthetic source drift\n")
    if fault == "run-source":
        args = argparse.Namespace(
            checkout=str(tmp_path), sha="0" * 40, results_dir=str(out), lock_timeout=0, nodeid=NODEID
        )
        assert runner.run_mutation(args) == 1
        assert json.loads((out / "run-state.json").read_text())["state"] == "failed"
    else:
        with pytest.raises(runner.RunnerError):
            if fault == "discovery-source":
                runner.discover(tmp_path, out, imported_module="owned synthetic fixture")
            else:
                runner._collect_mutation(
                    tmp_path,
                    "not-canonical" if fault == "nodeid" else NODEID,
                    [] if fault == "inactive" else inactive,
                    out,
                )
    assert not selection.exists() and not pending.exists()
    assert log not in runner._collected_items
    if fault == "source-during":
        # A falha nova tem recibos seguros; nao sao os outputs anteriores.
        assert log.read_bytes() != previous_log
        assert log.with_suffix(".execution.json").read_bytes() != previous_evidence
        assert json.loads(log.read_text())["collection_validated"] is False
        assert json.loads(log.with_suffix(".execution.json").read_text())["finished"] is False
    else:
        assert not log.exists() and not log.with_suffix(".execution.json").exists()
    assert foreign.read_text() == "preserve unrelated evidence"
    assert foreign_temp.read_text() == "preserve foreign temporary"
