# Original reviewer recheck — portal listener fixture

Verdict: **APPROVE for the narrow fixture synchronization correction**. No source finding remains in this review set. The original CI scheduling cause remains **NOT_PROVEN**; final integrated-head CI remains required. This review covers only the last existing `App.test.tsx` case and its qualified private execution evidence, not other runner/startup changes.

## Exact freeze and independent readback

Source, handoff and manifest hashes matched both before and after review:

- `App.test.tsx`: `d4ef473b81699558210df90488eb8c2b6c115a2376bd5081651777f8da9698ca`.
- `MAEZO-C6-PORTAL-LISTENER-FIXTURE-REPAIR.md`: `470672f6429db88c054aabf16ebe0ad7d546db37928f1f4085e16ba54ae7effc`.
- `MAEZO-C6-PORTAL-LISTENER-FIXTURE-REPAIR-MANIFEST.json`: `8aec4acb011e1a9842a383c538434f29a2374ef95144d8d3c8ea93dd2d95db5c`.
- Production `App.tsx` still equals its original diagnosis hash `9ec73770b0fcce826b9637b630ffac0372094063efeb7620962ff77058861b7c`.

Independently verified all 91 frozen portal file hashes/sizes against both current source and the authorized private execution copy; all match. Comparison with the 91-path origin manifest identifies exactly one changed path, `App.test.tsx`. All three explicit BPMN/DMN/contract context dependencies match their recorded hashes in current source and the execution copy. The four preserved original source/diagnosis preimages match their hashes, and both original diagnosis artifacts remain unchanged.

The private package-lock hash matches the final manifest (`6494fe65234b32e1cbf0c94055d185db335eb0c474acb079f3b0ed875e3fce39`). Independently compared all 138 recorded installed package versions with both their actual `package.json` and lock entries; all match. Recomputed all 5,953 qualified installed-dependency file hashes/sizes; all match the preserved dependency inventory. Actual installed Vitest/Vite are 5.0.5/8.3.4. The earlier copied graph's version drift and initial PASS remain explicitly **UNQUALIFIED**, separate from this final evidence.

The preserved original CI log is exactly 92,540 bytes and matches `2fd84b791bd8a0a9cc61e4eb062dfbb360db785249dd95abfe4382e5f6b800cc`. No execution was performed by this reviewer; all checks above were read-only artifact/hash comparisons.

## Source assessment

The complete file prefix before the final test is byte-identical to the original preimage. In the final test, the initial response is explicitly deferred and resolved within awaited `act`. The registration spy delegates to the captured native `addEventListener` before recording readiness and restores itself in `finally`. Readiness and exactly one fetch are asserted before the original visibility event; exactly two fetches immediately afterward establish non-vacuous delivery into session revalidation.

The test retains visible document state, logout focus, the real visibility event, the pending second response, loading without focus, active-session absence during loading, explicit second-response resolution, destination without focus and the exact status announcement. It adds no sleeps, retries, timeout increases, conditional assertions, skips, production changes or new cases. The downstream assertions continue to verify real behavior rather than merely trusting the registration flag.

The private negative experiment differs only by withholding actual `visibilitychange` registration. Its hash matches its experiment receipt, and the preserved log fails at readiness with `false` before the event. This demonstrates that the new precondition is non-vacuous. It does not reproduce or establish the original CI scheduling cause.

## Qualified execution evidence and limits

Read the actual final verify and targeted/negative logs:

- Exact-lock original preimage: **1 selected PASS / 24 filtered cases**. Exact-lock successor: **1 selected PASS / 24 filtered cases**. These filtered runs do not establish a causal before/after failure.
- Controlled withheld-registration experiment: **1 selected FAIL / 24 filtered cases**, at the pre-event readiness assertion.
- Final normal `npm run verify`: generated public/PHI parity **PASS**, TypeScript **PASS**, **670/670 tests PASS in 31/31 files**, no skipped tests reported, Vite production build **PASS** (48 modules). The existing >500 kB chunk advisory is preserved.
- The earlier private-layout run's **666 PASS / 4 ENOENT FAIL** is retained. Its missing files are exactly the three explicit context dependencies subsequently copied and independently qualified above. It is neither omitted nor counted as the original CI failure.

Reviewer-computed log hashes: final verify `371376106383c7bb8210e58a9924340589730d501e07c1cb585b2ea1a41f9d3c`; controlled negative `d94fe3104b5297028bc1e6074bad2f5fb89a4c1eccb4886e89114dda89d3b60c`; qualified preimage `6d931a881a99dda279b5748c866270a9de1354e81c9cf8c81f823e70d272c870`; qualified successor `def07ee446e3a94e2d51eb9dd7e8b009455d326ad0b720817e84bd9d3ee620c9`.

No test, Node installation, Git command, source mutation, port or shared resource was used by this reviewer. Private-copy qualification supports this fixture freeze; it does not replace ROOT's final exact-head CI after integration/current-main reconciliation. Source acceptance, human, operational and production acceptance remain separate.
