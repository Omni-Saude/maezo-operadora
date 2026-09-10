import pytest

from maezo.gateway.external_cases.models import timestamp
from maezo.gateway.human.read_profile import digest
from maezo.gateway.staff_cases.census_plan import compile_plan, protected_read, read_plan, seal
from maezo.gateway.staff_cases.models import StaffCaseError
from tests.unit.gateway.test_staff_census_source import census_fixture, source_read


def prepared(count=1):
    authority, principal, witness, cut, now, signer, _ = census_fixture(count)
    result = compile_plan(
        source_read(authority, cut, now),
        principal=principal,
        witness=witness,
        signer_for=lambda source, purpose: signer,
        authority=authority,
        dependency_digest=digest({}),
        now=now,
        session_until=timestamp(witness.valid_until),
    )
    return result, authority, now


@pytest.mark.parametrize("count", [0, 1, 513])
def test_complete_plan_uses_native_policy_grant_chunks_checkpoint(count):
    plan, _, _ = prepared(count)
    assert plan.publications[0].kind == "policy_head"
    assert plan.publications[-1].payload.total_entries == str(count)
    chunks = [p.payload for p in plan.publications if p.kind == "scope_chunk"]
    assert sum(len(c.entries) for c in chunks) == count
    if count == 513:
        assert len(chunks) >= 3


def test_seal_retains_exact_originals_and_refuses_overwrite(tmp_path):
    tmp_path.chmod(0o700)
    plan, _, _ = prepared()
    path = tmp_path / "plan.json"
    checksum = seal(plan, path, maximum=16777216)
    restored = read_plan(protected_read(path, expected=checksum, maximum=16777216))
    assert restored == plan
    with pytest.raises(FileExistsError):
        seal(plan, path, maximum=16777216)
    assert digest(restored.wire()) == checksum


def test_custody_no_follow_or_relabel(tmp_path):
    tmp_path.chmod(0o700)
    target = tmp_path / "source"
    target.write_bytes(b"private-test")
    target.chmod(0o400)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        protected_read(link, expected=None, maximum=100)
    with pytest.raises(StaffCaseError):
        protected_read(target, expected="0" * 64, maximum=100)
