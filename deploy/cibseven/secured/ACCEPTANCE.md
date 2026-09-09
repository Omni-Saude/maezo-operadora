# ROOT acceptance: secured opt-in package

All commands below are **planned ROOT execution**, not author PASS results. ROOT owns
Docker/engine serialization and must retain actual stdout/stderr/exit code, exact image
digest, source SHA, test identities and teardown. No daemon/service was operated by the
author. Use a new clean exact candidate checkout after the independent author review.

1. Run Java17 `mvn -o -B -f src/maezo/portal/engine/java/pom.xml package`, the 37 existing
   D5 real PostgreSQL tests, and `-Dtest=WorkloadEngineIT test` in the same Maven package
   with explicit isolated `MAEZO_HUMAN_IT_JDBC_URL`, `MAEZO_HUMAN_IT_DB_USER`,
   `MAEZO_HUMAN_IT_DB_PASSWORD`. Java tests consume environment credentials without
   printing them. WorkloadEngineIT creates/deletes only its uniquely owned schema.
   Its three tests are actual engine/command tests, not Tomcat/mTLS tests.

2. Build `deploy/cibseven/Dockerfile.secured` and record the resulting immutable image
   digest. Inspect its final common JAR (no REST SPI API or provider adapter class), the
   one-class REST SPI classifier in WEB-INF/lib, native/global/human/camunda descriptors,
   plugin classes, resource registry and Java17 ABI. Inspect Tomcat startup with the
   actual mounted configuration. No image existence or compilation claim substitutes
   for this boot. The global descriptor is a deterministic patch of the extracted actual
   Tomcat 10.1.47 file; camunda's legacy descriptor is retained at
   `/camunda/camunda-web.legacy.xml`, while only a closed servlet is deployed there.

3. In a canonical mode-0700 private directory outside the checkout/evidence tree, run:

   ```sh
   PYTHONPATH=src python deploy/cibseven/secured/prepare_fixture.py prepare \
     --checkout <clean-absolute-checkout> --sha <full-candidate-SHA> \
     --base-files <actual-pinned-inspection>/files/camunda \
     --private-root <private-root> --evidence-root <root-evidence-dir> \
     --output <private-root>/<new-fixture-name> \
     --bootstrap-image sha256:9f0ba266d1c3f5712da455560883340451bb59c30bae0d10abc4f2d5f0116c5b \
     --secured-image <actual-new-immutable-image-digest>
   ```

   This command generates disposable fixture keys/certificates, reissues the existing
   synthetic D5 client certs with the **same SPKI** and exact URI SAN/key usage, preserves
   D5 signing-purpose separation, and writes a bootstrap Compose plus unbound policy.
   No private key/certificate provider value belongs in evidence or Git. The `seed` step
   below is a fixture-only control; it does not implement D's production bootstrap.

4. Under one explicit unique Compose project and ROOT's engine lock, start the generated
   `bootstrap-compose.json` postgres and engine. This is the pre-existing isolated legacy
   image used **only** to deploy fixture models and exact grants before secure restart.
   Its ports are loopback-only 18080/18443/15433. Wait for its actual readiness, then run:

   ```sh
   PYTHONPATH=src python deploy/cibseven/secured/prepare_fixture.py seed \
     --fixture <absolute-private-fixture>
   ```

   The separate seed command operates that loopback fixture, deploys two explicitly
   synthetic BPMN models using A's exact keys/topics, captures exact definition IDs and
   versions, grants the finite native permissions and binds real A profile digests.
   It refuses to seed an already-bound fixture. It prints no response bodies/credentials.
   Synthetic models prove the boundary/lifecycle; they do not prove canonical business
   semantics, DMN outcomes or real clinical form activation.

5. Stop the **owned bootstrap engine only**, preserving its PostgreSQL container/data.
   Recreate only engine from `secured-compose.json` under the **same project**. The
   secured Compose exposes only 18443, uses exactly one HTTPS clientAuth-required
   connector, disables autoDeploy and mounts all policy dependencies read-only. Capture
   startup and prove native authenticated capability readiness, not `/version` alone.
   Do not recreate PostgreSQL during this transition: bindings depend on its exact IDs.

6. Run:

   ```sh
   MAEZO_D7_PACKAGE_FIXTURE=<absolute-private-fixture> PYTHONPATH=src \
     python -m pytest -q tests/integration/test_portal_engine_d7_package.py
   ```

   The 37 collected cases use real HTTP/TLS and read-only PostgreSQL snapshots of native
   task/execution/external-task/variable/bytearray/deployment/definition/receipt/op-log
   rows. Snapshots retain only counts/hashes. All refused mutations must leave them
   unchanged. The missing-cert test reuses the independently repaired typed native JSSE
   oracle. Secure plaintext coverage requires connection refusal with the TLS capability
   control passing; it does **not** pretend to execute the old HTTP 403 assertion.

7. Keep the existing five `test_portal_engine_package.py` assertions literally unchanged
   and run them against their original `Dockerfile.human`/two-connector fixture as a
   **separate preservation suite**. The two-connector fixture is not accepted by secured
   startup. Do not modify its assertion to hide that intentional package distinction.

## Additional real controls needed before any cutover

The executable suites are a starting acceptance surface; the following all remain
required real ROOT cases, independently reviewed, including relevant C/D/E caller tests.
None is a source-level or mocked integration PASS:

| Area | Required real case / invariant |
| --- | --- |
| Target/lock | Wrong tenant, process definition/version, topic, task ID and worker ID; foreign lock owner, expired lease, concurrent complete/unlock/extend/timer; current check after the last blocking SQL and at COMMITTING; all rows/receipts unchanged on refusal |
| Grants | Remove native CREATE/READ/READ_HISTORY/UPDATE_INSTANCE individually; readiness and operation deny; restore exact grant and resume pending identity; no absence result for unauthorized history |
| Human | Command, authority and receipt mTLS purposes separate; worker/agent/observer/bridge/bootstrap/deployment certs cannot borrow a human envelope; malformed/replayed/conflicting receipts preserve D5/D6 enlisted SQL, optimistic revision, authority and outbox semantics |
| Route methods | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS/TRACE on each reachable native alias, native engine-name aliases, identity verification/enumeration, camunda/Tasklist/Cockpit/Admin, manager/host-manager, request/include/forward/error/async dispatch |
| Body | Duplicate keys at all nested objects, top-level array, invalid UTF-8/surrogate/overflow/NaN/depth/size, content-type variants, multipart/form/binary/valueInfo/Java object/local variables/startInstructions/restart/modification; no fallback into RESTEasy mutations |
| Rotation | Actual old/new cert overlap under one exact identity/capability set; restart with new policy digest; remove old leaf, prove old established TLS sessions and new connections fail; only correct new cert resumes |
| Mount loss | Truncate/change the actual bound policy file in place and show readiness/effect refusal; restore identical bytes and prove same pending identity can retry. For missing file/CA/provider/plugin/auth/listener tests recreate only owned engine with missing mount or altered descriptor: fail startup/readiness, no HTTP fallback. A removed host path alone may not remove an already-bound container inode |
| Restart/lost reply | Restart actual secured image with intact policy/SQL and pending external tasks; preserve exact worker ownership and dedup identity. Drop a response after effect and reconcile against engine state / D5 receipt / D6 outbox, never manufacture success or automatically create a duplicate start |
| Source proof | PAGTO handoffs and consent correlation must match current exact source definition and source variables, completed source human task + **decision** receipt (claim/release receipt insufficient), and the actual historical variable update. Test forged actor IDs and wrong source case; require unchanged engine/tenant audit on refusal |
| Canonical flows | Real scoped start/correlation/DMN and all required worker topics under reviewed A extensions; current 47 rows have no DMN schema and only two external topics. Do not grant unknown rows or fabricate completed source human receipts to count a positive |
| C/D/E | Every CALLS propagation site and all 157 legacy human fixture dispositions; source lock identity before durable start claim, auth outage/recovery, native version authority, one-shot bootstrap and artifact deployment restrictions |
| Audit | In the actual D6 tenant database, compare `audit_chain`, `human_command_outbox`, `human_command_delivery` and engine receipts around each refusal/race/retry. The simple B image fixture does not create fake tenant audit tables and does not claim this separate proof |

Finally run exact integrated-SHA CI, independent bypass assurance, D5/D6/image preservation
and the separately reviewed operational migration. Current six real form bindings and the
production portal factory remain closed. Existing Tasklist/Cockpit history is preserved;
the deployed human UI is not changed by authoring this opt-in package. A rollback to an
anonymous engine is not a valid secured operational rollback.
