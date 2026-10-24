# C6 Java descriptor-churn fixture third repair

Status: frozen for original diagnostician recheck and ROOT Linux exact-head CI. This is a fixture lifecycle correction; it does not establish that this race caused the original CI error.

The original diagnosis is preserved in `reviews/maezo-java-ci-diagnosis.{md,json}`. CI run39837960315/job120031149983 failed in `RuntimeDefinitionObservationTest.liveResourceIsExactV4SuccessorWithNoV3Fallback` while the production descriptor identity guard rejected a surviving descriptor. The preceding churn fixture interrupted its daemon but did not join it, permitting late channel publication after collection cleanup. No production guard, deadline, schema pin, scanner, workflow, dependency version or skip was changed.

Only `RuntimeDefinitionObservationTest.java` changed. Fixture cleanup now interrupts and joins its worker without holding the publication lock, then closes retained channels. Caller interruption is restored after custody cleanup. Every newly opened channel either transfers to the collection or closes locally; an interrupted handoff refuses publication. Worker and cleanup errors remain visible instead of being silently lost. Existing cases, assertions, assumption and ten-second census bound are retained; every original method body outside the churn fixture is byte-identical.

One new platform-independent regression in that same class uses real temporary FileChannels and latches. It holds a worker after interrupt but before its pending handoff, proves cleanup waits while retained channels remain open, releases the owner, and checks no live worker/cleanup thread, no open channel, and no late collection publication. The identical regression fails against an explicitly preserved mutation using the old interrupt-and-close-without-join algorithm (cleanup TERMINATED instead of WAITING). That is deterministic lifecycle evidence, not a claimed reproduction of the stochastic Linux failure.

Validation under the ROOT-exclusive targeted Maven lane:

- Final new lifecycle case: **1 PASS**, zero failures/errors/skips; Java25.0.4 compiling release17.
- Old-cleanup mutation: **1 FAIL**, zero errors/skips; preserved as negative evidence.
- Exact original host class: 59 cases, **41 PASS / 2 FAIL / 15 ERROR / 1 SKIP**.
- Repaired host class: 60 cases, **42 PASS / 2 FAIL / 15 ERROR / 1 SKIP**. Every original named case has exactly the same outcome as the host baseline. The existing Linux `/proc` assumption is unchanged. The full class is not reported green.

The initial offline attempt failed dependency resolution before compilation. ROOT then authorized ordinary project dependency resolution in a dedicated private Maven cache; no global configuration or unrelated installation was changed. Subsequent commands used that cache offline with `-Dtest=RuntimeDefinitionObservationTest` or its exact new lifecycle method selector and `-DfailIfNoSpecifiedTests=true`. No real engine, JDBC/PG, container, port or Git operation ran. The deployed-runtime profile and Linux descriptor behavior remain NOT_RUN locally and require ROOT CI.

The manifest records source, original preimage, complete failing CI log, local negative/positive logs and XML hashes. Exact preimages, repaired snapshot, explicit old-cleanup mutation and local reports are retained under ignored private `repair-preimages.local/c6-java-fixture` (directory0700/files0600). The forbidden historical Python test was not accessed. Original diagnosis/review artifacts remain unchanged. Original diagnostician decides repair acceptance; ROOT owns final CI/integration.
