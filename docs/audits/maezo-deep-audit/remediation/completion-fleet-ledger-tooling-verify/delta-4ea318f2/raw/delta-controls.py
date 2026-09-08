"""Own delta controls: pure parsing and owned temporary git/processes; no network."""
import contextlib
import io
import json
import pathlib
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


def emit(name, **data):
    print(json.dumps({'control': name, **data}, sort_keys=True), flush=True)


def invoke(repo, base):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        rc = gate.main(['--base',base,'--python',sys.executable],repo_root=repo)
    return rc, output.getvalue()


with tempfile.TemporaryDirectory(prefix='ledger-delta-') as temp:
    temp = pathlib.Path(temp)
    for case in ('selected_dirty_lock', 'selected_missing_lock', 'global_forged_source', 'selected_committed_lock', 'unselected_committed_lock'):
        owned = temp / case
        owned.mkdir()
        h = fixture._history_repo(owned)
        base = h.source_commit
        lock = h.root / 'uv.lock'
        if case == 'selected_missing_lock':
            lock.unlink()
            expected = 'current uv.lock is missing'
        elif case == 'global_forged_source':
            base = fixture._git(h.root,'rev-parse','HEAD')
            fixture._replace_and_commit(h.root,h.target_test_sha256,'0'*64)
            expected = 'historical test blob digest mismatch'
        else:
            if case == 'unselected_committed_lock':
                base = fixture._git(h.root,'rev-parse','HEAD')
            lock.write_bytes(lock.read_bytes()+b'\n# delta-control lock change\n')
            expected = 'working-tree uv.lock differs from HEAD'
            if case.endswith('committed_lock'):
                fixture._git(h.root,'add','uv.lock')
                fixture._git(h.root,'commit','-qm','delta lock change')
                expected = 'historical uv.lock differs from HEAD'
        rc, output = invoke(h.root,base)
        if case == 'unselected_committed_lock':
            assert rc == 0 and 'PASS: 0 rows verified' in output
        else:
            assert rc == 1 and expected in output
        emit(case,rc=rc,output=output)

    queries=['host=db.example.invalid','hostaddr=203.0.113.17','port=5439','service=production','servicefile=/tmp/service.conf','%68%6f%73%74=db.example.invalid','HoSt=db.example.invalid','host=%2Ftmp','sslmode=disable&host=db.example.invalid','host=&host=db.example.invalid','service=','hostaddr=127.0.0.1,203.0.113.17']
    invalid=[f'postgresql://127.0.0.1:5432/maezo?{query}' for query in queries]
    invalid += ['postgresql://127.0.0.1:0/maezo','postgresql://127.0.0.1:65536/maezo','postgresql://127.0.0.1:abc/maezo','postgresql://db.example.invalid/maezo','postgresql://%2Fvar%2Frun%2Fpostgresql/maezo','mysql://127.0.0.1/maezo','postgresql://127.0.0.1/maezo#fragment','postgresql://127.0.0.1/maezo?host']
    for value in invalid:
        try:
            gate.recipe_environment(live_coordinates={'MAEZO_TEST_DATABASE_URL':value})
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe DSN accepted: '+value)
    emit('postgres_endpoint_override_matrix', rejected=len(invalid), network_calls=0)
    for value in ['postgresql://127.0.0.1:5546/maezo','postgres://lane:synthetic@localhost:5546/maezo?sslmode=disable','postgresql://[::1]:5546/maezo?application_name=ledger']:
        env=gate.recipe_environment(live_coordinates={'MAEZO_TEST_DATABASE_URL':value})
        parsed=conninfo_to_dict(env['MAEZO_TEST_DATABASE_URL'])
        assert gate._is_loopback_host(parsed['host'])
        emit('allowed_DSN_resolves_loopback', host=parsed['host'],port=parsed['port'])

    fake_python = temp / 'sleeping-python'
    fake_python.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n')
    fake_python.chmod(0o700)
    old = floor._UNIT_TESTS_TIMEOUT_SECONDS
    floor._UNIT_TESTS_TIMEOUT_SECONDS = 0.2
    begin = time.monotonic()
    try:
        counts, rc, raw = floor.measure_unit_tests(temp,str(fake_python))
        passed, violations = floor.evaluate_unit_tests(counts,rc,raw)
    finally:
        floor._UNIT_TESTS_TIMEOUT_SECONDS = old
    assert old == 1800 and rc == -1 and counts == {} and passed is None and violations
    emit('independent_real_deadline', configured_bound=old, temporary_control_bound=0.2, elapsed_s=round(time.monotonic()-begin,3),counts=counts,rc=rc,passed=passed,violations=len(violations),raw=raw)
