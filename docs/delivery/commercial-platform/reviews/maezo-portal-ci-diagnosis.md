# Portal CI diagnosis — automatic session revalidation fixture

Scope: read-only diagnosis of [CI 39851012097, portal-web job 120076853709](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39851012097/job/120076853709), PR head `ddb7116223067b32c123afcd24f3cb5a0bdd3c27`, base `c1a6a64d54bb1966e5419dd6677775aeeb7e2d83`. CI checked out GitHub's merge candidate of those heads. ROOT reports the two inspected files unchanged from base and base portal job 120035954701 successful; no Git comparison or base refresh was performed by this reviewer.

## Confirmed evidence

The job reached `npm run test` after `check:generated` and `typecheck` completed. Vitest reported **670 tests: 669 PASS / 1 FAIL** across 31 files. The only failure was `App.test.tsx`'s `revalidação automática anuncia o estado sem focar o destino`, at line 801: `findByRole` could not find the heading `Revalidando sua sessão` after 1.049 seconds. Its DOM still contained `Sessão ativa` and `Área de colaboradores`. Build follows tests in an `&&` chain, so this run did not establish a build PASS. Other visible-revalidation cases in `App.test.tsx` passed in that same run.

Inspected source bytes remained unchanged before/after review:

- `App.tsx`: `9ec73770b0fcce826b9637b630ffac0372094063efeb7620962ff77058861b7c`.
- `App.test.tsx`: `4ad011981fe3533db4c90e6c920d5cebdcb9e839c374c5280b0d64a4a905edc2`.

`App.tsx:87-94` installs the document visibility listener in a passive `useEffect` only when `state.kind` is authenticated; its callback checks `document.visibilityState === "visible"` before invoking `revalidate`. The callback immediately schedules loading at lines 46-53, while the second session result in this fixture remains deferred. The effect removes the listener when leaving authenticated state.

The test at lines 784-815 awaits an authenticated logout button, focuses it, makes visibility visible and dispatches the event. It has no explicit passive-effect flush or assertion that the actual listener has registered before that one-shot event. Its existing downstream `findByRole` waits for a loading render but cannot replay an event delivered before subscription. The DOM query's successful resolution is not an explicit listener-registration precondition in this test source.

This confirms an incomplete fixture synchronization precondition and a well-scoped registration-race hypothesis. It **does not prove** that this listener was absent in the failing CI execution. The log has no registration trace, callback invocation or session-fetch count at dispatch. The active DOM is consistent with a lost event but does not exclude another event-delivery or handler issue. Focus/visibility behavior must remain covered, not inferred away.

## Minimal third-party fixture proposal

Change only this test's setup/transition synchronization. Make the initial session response explicitly deferred and resolve it inside an awaited standard `act` boundary, so the authenticated update and its effects settle before focusing the logout button and dispatching visibility. An awaited `act` effect flush immediately before dispatch is the smaller alternative, provided the author supplies non-vacuous registration evidence. Do not change App production code.

For deterministic evidence, use a local spy that delegates to the real `document.addEventListener` and records the actual `visibilitychange` callback registration. Assert that registration before dispatch, and restore the spy in `finally`. Preserve the original event, visible-document condition, pending second response, loading-not-focused assertion, absence of `Sessão ativa`, final-destination-not-focused assertion and exact status announcement. Assert exactly two session fetches across the event to distinguish event delivery from a DOM-only wait. A test-only withheld-registration negative control can demonstrate that the readiness assertion fails rather than permitting a vacuous transition test; label it a controlled fixture experiment, not reproduction of the original CI schedule.

Use standard act/readiness synchronization with existing test budgets. Do not add sleeps, timeout increases, reruns, skips or conditional assertions. The author should preserve the original preimage/log and freeze the minimal successor for this original reviewer's recheck. ROOT owns test execution and the final exact-head portal verify gate. No tests, source edits, Git commands, Node installation, shared resources or production changes were performed by this diagnostician.

Original CI cause remains **NOT_PROVEN**. This diagnosis authorizes neither a whole-portal PASS nor operational acceptance.
