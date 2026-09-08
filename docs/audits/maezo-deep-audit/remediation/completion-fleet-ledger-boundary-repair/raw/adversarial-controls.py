"""Independent, local-only reproductions; no connections or product edits."""
from __future__ import annotations
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path.cwd()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/unit/ci'))
import test_check_evidence_ledger_supersession as fixture
from scripts.ci import check_evidence_ledger_hashes as gate
from scripts.ci import generate_release_floor as floor
from psycopg.conninfo import conninfo_to_dict


def check_main(repo, base):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        rc = gate.main(['--base', base, '--python', sys.executable], repo_root=repo)
    return rc, output.getvalue()


def emit(name, result):
    print(json.dumps({'control': name, **result}, sort_keys=True), flush=True)

with tempfile.TemporaryDirectory(prefix='ledger-verifier-') as temp:
    temp = pathlib.Path(temp)
    for case in ['missing_declaration', 'predated_successor', 'unrelated_lock_update', 'non_python_suffix_escape']:
        owned = temp / case
        owned.mkdir()
        source = None
        if case == 'non_python_suffix_escape':
            outside = owned / 'outside.txt'
            outside.write_text('VALUE = 37\n')
            source = (
                'from importlib.machinery import SourceFileLoader\n'
                'import importlib.util\nimport sys\n'
                'def test_external_python():\n'
                f'    loader = SourceFileLoader("external_text_helper", {str(outside)!r})\n'
                '    spec = importlib.util.spec_from_loader(loader.name, loader)\n'
                '    module = importlib.util.module_from_spec(spec)\n'
                '    sys.modules[loader.name] = module\n'
                '    loader.exec_module(module)\n'
                '    assert module.VALUE == 37\n'
            )
        history = fixture._history_repo(owned, source_test=source)
        base = history.source_commit
        if case == 'missing_declaration':
            bad = history.successor_row.replace('sha256:', 'sha257:')
            fixture._replace_and_commit(history.root, history.successor_row, bad)
        elif case == 'predated_successor':
            bad = history.successor_row.replace(gate.CONVENTION_START_DATE, '2000-01-01')
            fixture._replace_and_commit(history.root, history.successor_row, bad)
        elif case == 'unrelated_lock_update':
            base = fixture._git(history.root, 'rev-parse', 'HEAD')
            lock = history.root / 'uv.lock'
            lock.write_bytes(lock.read_bytes() + b'\n# unrelated dependency update\n')
            fixture._git(history.root, 'add', 'uv.lock')
            fixture._git(history.root, 'commit', '-qm', 'unrelated dependency update')
        rc, output = check_main(history.root, base)
        emit(case, {'gate_rc': rc, 'output': output})

value = 'postgresql://127.0.0.1/maezo?host=db.example.invalid&port=5432'
accepted = gate.recipe_environment(live_coordinates={'MAEZO_TEST_DATABASE_URL': value})
resolved = conninfo_to_dict(accepted['MAEZO_TEST_DATABASE_URL'])
emit('dsn_query_host_override_without_network', {'gate_accepts': True, 'libpq_resolved_host': resolved['host']})

with tempfile.TemporaryDirectory(prefix='floor-verifier-') as temp:
    temp = pathlib.Path(temp)
    fake_python = temp / 'sleeping-python'
    fake_python.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n')
    fake_python.chmod(0o700)
    old = floor._UNIT_TESTS_TIMEOUT_SECONDS
    floor._UNIT_TESTS_TIMEOUT_SECONDS = 0.2
    begin = time.monotonic()
    try:
        counts, rc, raw = floor.measure_unit_tests(temp, str(fake_python))
        passed, violations = floor.evaluate_unit_tests(counts, rc, raw)
    finally:
        floor._UNIT_TESTS_TIMEOUT_SECONDS = old
    emit('real_subprocess_deadline', {'returncode': rc, 'counts': counts, 'passed': passed, 'violations': len(violations), 'elapsed_s': round(time.monotonic()-begin, 3), 'raw': raw})
    assert rc == -1 and passed is None and violations
