# Java fixture cleanup R2 — finite termination

Successor to original reviewer finding JFIX-R1. R1 source freeze `8f91c1436352780084cddd677d70376c1f9c51b21ec07f5fe3cdc9adce10ce80` and its handoff/manifest remain preserved. R2 changes only the existing `RuntimeDefinitionObservationTest.java` fixture; no production Java, POM, scanner, environment, original assertion, skip or census timeout changed.

Cleanup now waits using a finite monotonic five-second budget. Every join receives only the remaining budget. If the worker remains alive, cleanup throws a visible IOException before acquiring the collection lock or closing its channels; active ownership remains intact. Thread interruption is restored. Successful termination still precedes collection release. Regression teardown joins use the same finite helper.

A new deterministic stalled-owner test uses a real temporary FileChannel and a latch. With a 50ms fixture budget, cleanup must fail while the worker remains alive, the channel remains open and the collection remains unchanged. Its finally block releases the latch and performs bounded owned cleanup. A separate four-second worker safety bound prevents even the explicit unbounded-join negative mutation from stranding the test process. The existing interrupted-handoff regression remains, updated to expect timed waiting rather than an unbounded wait.

Final targeted Maven result: **2 PASS, zero failures/errors/skips**, host Java25.0.4 compiling release17. The explicit R1-style unbounded-join mutation fails the stalled-owner regression (**1 FAIL**, zero errors/skips) because no finite-budget exception is raised. Repaired bytes were restored and verified before the final passing run. This proves the fixture lifecycle property, not causal reproduction of the original Linux CI failure.

Only the two named lifecycle methods ran for R2, using ROOT's dedicated cache offline. No whole-class rerun was requested: R1's original59/repaired60 host outcomes remain historical evidence, not a green Linux gate. All original59 cases remain unchanged. Original diagnostician recheck and ROOT exact-head Linux/deployed-profile CI remain required.

`MAEZO-C6-JAVA-FIXTURE-REPAIR-R2-MANIFEST.json` records exact hashes. R1 preimage, repaired bytes, explicit mutation, logs and final XML are private under `docs/delivery/commercial-platform/repair-preimages.local/c6-java-fixture-r2` relative to repository root (0700/0600). R1 evidence remains in sibling `c6-java-fixture`. No Git, real engine/PG, container, port or unrelated fleet changes occurred. The exclusive Maven lane is released.
