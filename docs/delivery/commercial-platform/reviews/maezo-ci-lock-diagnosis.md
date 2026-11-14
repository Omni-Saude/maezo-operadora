# Maezo CI lock startup diagnosis

Status: **DIAGNOSED; TEST-ONLY REPAIR PROPOSED; NOT EXECUTED**. Date: 2026-10-22.

The original CI failure is a proven startup readiness race in the test. No product ownership/cleanup bug is established by this evidence. The inspected probe path has no periodic heartbeat loop; describing this as a timestamp-only heartbeat would be inaccurate.

## Frozen scope and original RED custody

- Repository: Omni-Saude/maezo-operadora.
- Supplied candidate HEAD: ddb7116223067b32c123afcd24f3cb5a0bdd3c27. Base: c1a6a64d54bb1966e5419dd6677775aeeb7e2d83.
- [Original CI run 39851012097, job 120076853860](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39851012097/job/120076853860), unit (dev-runner), tested synthetic merge af06701e2e7e2b6aab0f129a3927a4735e2eb2cf. Result: 1 failed, 17 passed, 240.22 seconds.
- Failure: tests/unit/dev/test_engine_integration_runner.py::test_lock_contention_never_grants_cleanup_and_owner_releases_on_interrupt, line 308.

The original RED run remains authoritative historical evidence. This additive diagnosis does not replace or amend it. No raw CI log was persisted. Local source/test execution, product imports, subprocesses and Git mutations by this diagnostician: **NOT_RUN**.

| Explicit inspected file | SHA-256 |
| --- | --- |
| tests/unit/dev/test_engine_integration_runner.py | dcec1a521698f6d09900d25bff20e7fd55515c203892da6a910a3d1e3aab987d |
| scripts/dev/run_engine_integration.py | 5b27e471bdf85443457564634f79cafb7fc28f9294214687e9bd909b44c88281 |

Hashes identify the exact local files read; candidate HEAD/base metadata was supplied by ROOT. No broad repository/history scan was performed.

## Evidence and causal ordering

The CI full diff at log timestamps 2026-10-22T20:47:17.7152772Z through 2026-10-22T20:47:17.7160626Z shows the following **SYNTHETIC unit-probe metadata**:

| Field | Before contender | After contender |
| --- | --- | --- |
| checkpoint | subprocess_spawning | subprocess_running |
| pending_pgids | absent | [2908] |
| updated_at | 2026-10-22T20:43:23.493474+00:00 | 2026-10-22T20:43:23.494518+00:00 |
| subprocess_quiescent | false | false |
| owner pid | 2907 | 2907 |
| owner token | 97a29ca3698a44a1ac4449731e35d99c | 97a29ca3698a44a1ac4449731e35d99c |

Creation time remained 2026-10-22T20:43:23.491542+00:00. Token, PID and other owner identity fields did not change. The token is synthetic private probe control state, not an authorization/credential for a real subject.

The test waits for owner.json, child.pid and child.log path existence, then snapshots owner bytes (test lines 289–293). These waits do not establish the owner has finished recording the child:

1. child_probe emits child_started before entering _run (runner lines 2155–2165). That event is not a post-spawn readiness barrier.
2. _run creates an empty child.log before spawning (419–421), then publishes subprocess_spawning (433–436).
3. The real spawned child immediately writes child.pid (2160–2162). That write may happen before Popen returns to the owner.
4. Only after Popen returns does the owner call _record_pending (446); that function publishes subprocess_running, subprocess_quiescent=false and pending_pgids (365–370). EngineLock.update writes the new checkpoint/timestamp atomically (1219–1229).
5. The original snapshot therefore may contain subprocess_spawning; during contender execution the authorized owner finishes its own startup publication. The full byte comparison correctly observes a difference but misattributes an unsynchronized owner transition to contention.

The CI state change precisely matches this source ordering. It is not merely a generic flaky-test assertion.

EngineLock.owns requires acquired plus matching current PID and token (1208–1217). lock_probe returns BUSY_EXIT immediately after failed acquisition, emitting only lock_busy before entering any cleanup block (2124–2144). Release retains ownership, pending-group and subprocess-quiescence guards (1232–1247). Nothing in this diagnosis warrants changing those guards.

## Minimal test-only repair proposal

Write set: **tests/unit/dev/test_engine_integration_runner.py only**. No production lock change.

Before capturing original_owner, introduce a bounded source-state barrier:

1. Anchor the first published owner PID/token and require PID == owner.pid.
2. After reading child_pid, poll owner.json with a monotonic deadline (for example 10 seconds), requiring the owner remains live. Decode each raw read and accept only a publication with the same PID/token, checkpoint == subprocess_running, pending_pgids == [child_pid], and subprocess_quiescent is False.
3. Fail on owner identity change, owner exit or deadline. Retain the exact raw bytes from that successful read as original_owner; do not take an unrelated later snapshot.
4. Run the existing real CLI contender and preserve the full byte-for-byte owner.json comparison.

This uses the runner's existing atomic startup publication as the barrier. In this inspected path the owner then waits in communicate while the real child sleeps; no further owner write occurs before timeout, child exit or interrupt. No ignored timestamps, lax identity subset, altered ownership rule, mocked engine, skip, or cleanup privilege is proposed.

Preserve every existing behavioral assertion: contender rc73; exact contender events [lock_busy]; full raw owner bytes unchanged across contention; owner SIGTERM rc130; child absent; child-started in log; lock directory absent; exact owner events lock_acquired, child_started, interrupted, cleanup_permitted, lock_released. Both child-probe and lock-probe must remain the actual runner CLI, with a real subprocess.

## Handoff and qualification limits

ROOT assigns a distinct third fixture author, freezes the repaired exact paths/dependencies, owns the execution lane and obtains final exact-HEAD CI. The original diagnostician independently rechecks the repair. No test execution is claimed by this report.

Unit subprocess CLI qualification remains separate from engine integration, source acceptance, human acceptance, operation and production. Original CI RED evidence remains preserved alongside any successor result.
