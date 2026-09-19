# Serial candidate package qualification

Contracts: ADR-0049 D5/D7, DL-0048 and the read-owner contract in
`src/maezo/portal/engine/README.md`. This runner provisions no production service.
ROOT retains both canonical engine locks and verifies no competing workload for
the whole sequence, including teardown. The runner does not acquire those locks.

## Candidate inputs and lifecycle

Build `Dockerfile.human` and `Dockerfile.secured` from the same clean candidate
checkout. Preserve their actual immutable image IDs and independently hash their
candidate build-output `human-command-engine-1.0.0.jar` artifacts. ROOT records
these five fields in an image receipt (no credential values):

```json
{
  "source_sha": "FULL_CANDIDATE_SHA",
  "bootstrap_image": "sha256:ACTUAL_HUMAN_IMAGE_ID",
  "secured_image": "sha256:ACTUAL_SECURED_IMAGE_ID",
  "bootstrap_jar_sha256": "ACTUAL_CANDIDATE_HUMAN_JAR_SHA256",
  "secured_jar_sha256": "ACTUAL_CANDIDATE_SECURED_JAR_SHA256"
}
```

The receipt is evidence from the authorized build, not independently sufficient
authority. `start-human` rechecks the clean checkout SHA, both Docker image IDs
and the JAR bytes extracted from stopped containers against that receipt. The
obsolete historical bootstrap image pin is no longer accepted as candidate proof.
The Dockerfiles retain their pinned CIB Seven 2.1.0 and Maven base images.

Extract the original `/camunda/conf/server.xml` into `BASE_FILES/conf/server.xml`
from the candidate human image. Use a fresh canonical mode-0700 daemon-visible
private root outside checkout and evidence; never use a shared existing database.
All commands run from the candidate checkout using its locked Python environment.
The shared integration conftest also requires a real baseline CIB engine at
`ENGINE_REST_URL` (as the ordinary CI integration stack provides). Keep that
separate engine reachable through secured cutover: the package's plaintext port
is deliberately removed, and its mTLS readiness does not replace the baseline
`/version` probe. Give the baseline its own database/project and loopback port;
verify its identity and ownership before running, and retain its cleanup evidence.
The import preflight below must succeed before fixture creation/seeding:


```sh
umask 077
export PYTHONPATH="$PWD:$PWD/src"
.venv/bin/python -c 'import tests.support.human_relay_live'
: "${ENGINE_REST_URL:?set the separate real integration engine URL}"
curl --fail --silent "$ENGINE_REST_URL/version"
.venv/bin/python deploy/cibseven/secured/prepare_fixture.py prepare \
  --checkout "$PWD" --sha "$SHA" --base-files "$BASE_FILES" \
  --private-root "$PRIVATE_ROOT" --evidence-root "$EVIDENCE" --output "$PRIVATE" \
  --bootstrap-image "$HUMAN_IMAGE_ID" --secured-image "$SECURED_IMAGE_ID" \
  --image-receipt "$IMAGE_RECEIPT"
.venv/bin/python deploy/cibseven/package-test/lifecycle.py init --fixture "$PRIVATE" --project "$PROJECT"
.venv/bin/python deploy/cibseven/package-test/lifecycle.py start-human --fixture "$PRIVATE"
.venv/bin/python deploy/cibseven/package-test/lifecycle.py preflight --fixture "$PRIVATE"
.venv/bin/python deploy/cibseven/package-test/lifecycle.py test-human --fixture "$PRIVATE"
.venv/bin/python deploy/cibseven/package-test/lifecycle.py start-d7 --fixture "$PRIVATE"
.venv/bin/python deploy/cibseven/package-test/lifecycle.py preflight --fixture "$PRIVATE"
```

`PROJECT` must match `d7-[a-z0-9-]{8,64}` and have no existing owned Docker
resources. Loopback ports 15433, 18080 and 18443 must be available. Start performs
byte-exact positive bind visibility and missing-source refusal checks, then waits
for PostgreSQL 16 and actual mTLS plugin admission. `test-human` executes all five
unchanged tests and requires five passing JUnit cases. Its JUnit stays private
until ROOT reviews it for publication.

`start-d7` is allowed only after that successful human run. It uses the existing
real relay seed (command transaction and receipt), verifies bootstrap admission,
replaces only the engine and keeps its PostgreSQL service/data. It removes the
plaintext listener. The final preflight verifies native capability readiness,
actual policy digest, tenant/definition/resource bindings and persisted receipt.
No direct fabricated receipt rows are introduced.

Consume the private `d7-test-env.json` through a subprocess environment to run
the original D7 module. Never print or shell-source this JSON. The original human
environment is `test-env.json`. Preserve selectors and account each collected
nodeid exactly once: human runs before cutover; D7 follows. Remaining global
integration cases, native-v2 and Q2 remain separate mandatory selections.

In a ROOT-owned `finally`, including interrupted preparation/admission/tests:

```sh
.venv/bin/python deploy/cibseven/package-test/lifecycle.py stop --fixture "$PRIVATE"
```

Teardown removes only this project and verifies its containers, volumes and
networks are absent. It preserves private files for custody. A failed seed is
not retried by rewriting admission or deleting receipts; retain the failed
fixture, tear down its project and create a fresh uniquely named fixture.

`stop` first captures the current owned engine's actual Docker stdout/stderr,
including stopped containers, before removing resources. The human-to-D7
cutover also captures its bootstrap engine before replacement. Each attempt
creates a private mode-0700 `engine-logs-*` directory with mode-0600 streams and
`capture.json`. Capture requires the exact owner nonce, full container ID and
matching Compose project/service labels; the summary contains hashes, byte
counts and return codes, never raw logs or full environment-bearing inspection.
Raw logs remain private and require review before any publication.

An absent engine is recorded as absent, never as captured. If a running-phase
engine disappeared before stop, missing custody fails the stop result even when
the remaining owned resources were removed. A logging error or interruption
still leads to ownership-checked cleanup, and remains a failure afterward.
Ownership refusal permits neither foreign capture nor foreign teardown. Each
attempt has a unique directory; a later successful cleanup does not erase a
previous capture failure.

Python outer runners should execute the complete authorized sequence through
`runner.run_with_cleanup(sequence)`. It always invokes stop, preserves original
test failures or interruptions alongside capture/cleanup failures, and writes a
sanitized `operation-stop-*.json` phase record. Put the whole human/D7 sequence
inside this boundary, since it stops resources on success too. This wrapper is
explicit; it does not automatically stop between individual lifecycle steps.
CLI runners must likewise retain the original command result and the separate
stop result. A failing `finally` must not replace the original test failure.

## Inventory and remaining Q2 prerequisite

`lifecycle.py inventory --nodeids collected.json --output inventory.json` accepts
the actual pytest-collected JSON list. It rejects duplicate/invalid identities,
partitions all four families, retains every other nodeid, and reports actual
counts separately from historical 5/1119/3/10 anchors. It does not collect tests
or modify markers, assertions or CI selectors.

Q2 cannot currently be built from configuration alone. The source supplies
`PortalReadTrust.Providers` but no concrete installed implementation or service
registration. The independently reviewed next package must provide:

- One installed `META-INF/services/br.com.maezo.human.PortalReadTrust$Providers`
  registration, bound to the candidate image and actual current D admission.
- Live `acquire` verification of scope, database incarnation, deployment/config
  digests and purpose; bounded validity, revocation and statement timeouts.
- Native continuity key custody and authenticated committed source qualification
  for publications, catalog, identity policy and classification. Never copy the
  anonymous Java test provider or echo supplied facts as authority.
- Genuine read schema installation, separately admitted reader/READ_HISTORY
  grants and published catalog/task continuity; signed gateway envelopes with a
  real wrong-purpose negative; HTTPS BFF with a ROOT-issued browser session.

`preflight-q2 --checkout CHECKOUT --environment PRIVATE_Q2_ENV.json` consumes
exactly the seven `MAEZO_PORTAL_READ_*` variables required by the existing tests.
Missing configuration fails immediately. With independently installed fixtures,
it runs real read-only direct native catalog/task/purpose-separation and BFF
queue/detail checks. This is a consumer preflight, **not** a Q2 provisioning
implementation or proof that the missing owner package is available. Native-v2
observer credentials must not be reused for Q2 authority.

Do not launch the broad integration lane until all four family admissions have
passed. Local focal checks, generated JSON and a running Docker daemon do not
establish runtime acceptance or merge readiness.

## Ownership and runtime readback

The lifecycle first refuses every existing Compose resource, then persists a
unique owner token and labels its services, network and named PostgreSQL volume.
`stop` is a no-op for a prepared fixture whose admission failed. Once claimed,
teardown checks each resource's owner label before any project deletion, retains
foreign resources on refusal, and never requests orphan removal. Probe projects
and temporary extraction/mount containers have separate persisted identities;
interrupted operations retain those identities for supervised cleanup.

All fixture children must be caller-owned regular files or private directories;
symlinks and hard links are refused. Logs use no-follow opens and descriptor
permission checks. JAR extraction uses a fresh private directory. The missing-bind
negative control accepts only the explicit missing-source error containing the
exact absent path; unrelated failures remain failures.

Preflight and cutover read the actual running engine and PostgreSQL image IDs,
owner labels, installed JAR bytes, environment and mounts. Configuration hashes
are pinned before service startup; observed identities and hashes enter the
private preflight receipt. These checks bind runtime observations to build
receipts without turning supplied metadata into independent admission authority.
