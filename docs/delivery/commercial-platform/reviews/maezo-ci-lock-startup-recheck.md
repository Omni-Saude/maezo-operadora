# Maezo CI lock startup — original diagnostician recheck

**PASS for the bounded fixture repair; exact final HEAD CI remains pending.** No blocking finding within this scope. This is independent recheck of the third author's repair, not approval of the complete PR or runtime/production acceptance.

## Frozen candidate

Baseline HEAD supplied by ROOT: ddb7116223067b32c123afcd24f3cb5a0bdd3c27. All four requested file hashes were independently read and matched:

| File | SHA-256 |
| --- | --- |
| tests/unit/dev/test_engine_integration_runner.py | 20c25346a0167b56d98ef61030d2ae83d46bd48b31cd39eb9db875d8d9f8ae1b |
| scripts/dev/run_engine_integration.py | 5b27e471bdf85443457564634f79cafb7fc28f9294214687e9bd909b44c88281 |
| docs/delivery/commercial-platform/MAEZO-C6-LOCK-STARTUP-FIXTURE-REPAIR.md | e5565e792e199df75175c2afd997d8f3904b936030f33a5d0014ee4ce47b7839 |
| docs/delivery/commercial-platform/MAEZO-C6-LOCK-STARTUP-FIXTURE-REPAIR-MANIFEST.json | b3f2c186dd9a6cd7c7e6bb6d34b4775e64a621d9f5a48bc104fb8c7d3c991378 |

The production runner hash remains identical to the original diagnosis. Bytes outside the target function are identical to the original text independently retained by this reviewer. Removing only the initial PID/token anchor and new readiness block reconstructs the original function byte-for-byte. This verifies preservation of all eight original assertions, CLI arguments, interrupt and cleanup behavior; the final function has twelve assertions. Other functions and parameterization remain unchanged, preserving the 18-case denominator.

## Source-state barrier recheck

The repair anchors the initial PID/token, requires the owner process alive, and bounds polling to ten monotonic seconds. Each read must retain owner.pid and the initial token. Readiness requires checkpoint subprocess_running, pending_pgids exactly [child_pid], and subprocess_quiescent exactly False. The bytes assigned to original_owner are the exact bytes decoded for that successful predicate. Identity change, owner exit and deadline fail; no fallback or skip is introduced.

The existing full raw-byte equality after contention remains intact, as do rc73, exact lock_busy events, owner SIGTERM/rc130, absent child, child log text, removed lock and full owner event sequence. The source ordering established by the original diagnosis makes the atomic subprocess_running publication the appropriate barrier. No periodic heartbeat is claimed; no product lock or cleanup protection changed.

## Evidence custody and readback

The declared private path resolves from the handoff parent, docs/delivery/commercial-platform/, to repair-preimages.local/c6-ci-lock-startup-fixture-r1/. It is present; the initial checkout-root probe was a path-resolution issue, not missing custody.

All 46 content files listed by CUSTODY-MANIFEST.json independently match their hashes. The receipt hash is d40a9cd0b077f62ed1a2263ebac5325b731c47567363b82519f0bc160f6b4149. The sorted compact JSON content manifest independently recomputes to f5ec3362543e9173596930126e68735a953b77b010c457129e0cefc9a680c075. The 46 content files plus receipt are mode 0600; inspected declared directories are 0700. No raw private log is copied into this public report.

The controlled runner copy hashes to c5e95105f2333d8ca8f19694aaaf991071aa241e633a9ba15fd683904aad17ef. Comparing its entire text with the frozen production runner reveals only a one-second private scheduling delay after Popen and before _record_pending. The experiment wrapper retains the real CLI contender call, waits for the same owner's published running state, and compares the original test preimage with the frozen repaired test. Its oldRED/newGREEN result, original full-byte assertion failure and owned-child-absent assertions agree with the original CI cause. The after logs contain exactly lock_busy for the contender and lock_acquired, child_started, interrupted, cleanup_permitted, lock_released for the owner; child.log contains child-started.

The author-recorded natural real CLI target output is 1 passed in 0.21s. These are reviewed historical execution records; this reviewer performed no tests, product imports, subprocesses, Git operations, port actions or live-process inspection.

## Remaining gate

The [original CI RED](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39851012097/job/120076853860), 1 FAIL / 17 PASS, and original diagnosis remain preserved unchanged. Local full 18 is explicitly NOT_RUN under ROOT's narrowed lane. ROOT must obtain exact final HEAD CI for all 18 cases, with actual steps/logs/skips and no reduction of denominator.

This recheck establishes only the bounded fixture repair and integrity of its recorded evidence. Engine integration, source acceptance, human acceptance, operation and production are not established. Any subsequent source/evidence change requires renewal of affected gates.
