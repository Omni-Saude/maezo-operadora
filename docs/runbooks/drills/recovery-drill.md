# Drill: Audit-DB Recovery — restore, verify chain integrity, verify against engine history

**Audience:** DBA, security, compliance, on-call
**Last updated:** 2026-08-11
**Cadence:** Quarterly (or after any incident touching the audit chain / Postgres)

**Purpose:** Prove that (1) the audit database can actually be restored from a backup, (2) the
restored hash chain verifies as intact (no tamper, no gap/fork), and (3) the restored chain's
contents are consistent with CIB Seven's own process history — **before** ever pointing a live
worker daemon at the restored data. See [`audit-recovery.md`](../audit-recovery.md) for the
chain design this drill exercises.

> **Every command below was actually run against a live local stack while writing this drill**
> (`docker compose --profile core up -d` in a throwaway worktree), including the "simulate loss"
> step. One real trap was found and is called out explicitly in step 1 rather than smoothed over:
> the tenant schema **must** exist before the first `alembic upgrade` run against it, or Postgres
> silently falls through to creating the tables in `public` instead (confirmed: reproduced this
> live, then cleaned it up — see the warning box in step 1).

---

## Preconditions

- **Local/dev track (below): fully runnable today.** `docker compose --profile core up -d`
  healthy; `uv sync --locked --extra dev` done.
- **Staging/prod track: requires infra access not exercised by this repo's own tooling** — AWS
  credentials with `rds:RestoreDBClusterToPointInTime` / `rds:RestoreDBClusterFromSnapshot` on
  the target Aurora cluster (DBA/infra role). See §4 below.

## Step-by-step (local/dev track)

**1. Bring up the core stack and create + migrate a drill tenant schema.**

> **Create the schema BEFORE the first `alembic upgrade` run — do not skip this.**
> `src/maezo/platform/migrations/env.py` sets `search_path` to `"<tenant>", "public"` but never
> issues `CREATE SCHEMA` itself; if `<tenant>` doesn't exist yet, Postgres silently resolves
> every unqualified `CREATE TABLE` to the next schema in the path that *does* exist — i.e.
> `public` — with no error. `tests/integration/conftest.py`'s own bootstrap
> (`_create_schema()`/`_apply_migrations()`) does the `CREATE SCHEMA IF NOT EXISTS` step first
> for exactly this reason; skip it and you'll get a real-looking but wrongly-scoped migration
> (reproduced live while writing this drill, then cleaned up).

```bash
make dev-stack
# wait for `docker compose ps` to show cibseven "healthy" (JVM cold start, ~90s)

docker compose exec -T postgres psql -U maezo -d maezo -c 'CREATE SCHEMA IF NOT EXISTS amh_drill;'

docker compose --profile core --profile app run --rm worker-runtime \
  python -m alembic -x tenant=amh_drill upgrade head
```

(`docker compose run` overrides the service's default `command:`; the container runs on the
compose network, so `alembic.ini`'s baked-in `postgres:5432` DSN resolves correctly — this is
the same image/`alembic.ini` the `worker-daemon` migration Job in Helm uses, just invoked
directly instead of via a Job. `-x tenant=<schema>` is documented in
`src/maezo/platform/migrations/env.py`'s own module docstring.) Confirm:

```bash
docker compose exec -T postgres psql -U maezo -d maezo -c "SELECT to_regclass('amh_drill.audit_chain');"
# expect: amh_drill.audit_chain (not NULL)
```

**2. Generate at least one real audit record with a matching engine process instance.**

Start a real process on the engine (any deployed process key with a business key works; the
example below reuses the ad hoc echo-process helper the T1.1 spine integration test itself uses,
via that test directly — it exercises the real `WorkerHarness` + real audit sink end-to-end and
prints the business key it used):

```bash
uv run pytest tests/integration/test_worker_runtime_spine.py::test_happy_path_fetch_complete_history -q -m integration
```

That test starts a process with business key `bk-happy` and completes it through a **real,
separate, ephemeral** tenant schema (`it_<run-id>`, dropped at teardown) — it proves the
mechanism works, but its own audit rows do not persist for this drill. To populate **this
drill's** `amh_drill` schema with a real, hash-chained record referencing that same engine
process instance, emit one directly through the real sink (this is exactly what
`WorkerHarness`/`start_process_idempotent` do internally — see
[`audit-recovery.md`](../audit-recovery.md) §1):

```bash
uv run python - <<'PY'
import asyncio
import os
from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink

async def main():
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    sink = PostgresAuditSink(f"postgresql://maezo:maezo@localhost:{port}/maezo", "amh_drill")
    record = AuditRecord(
        agent_id="drill-operator",
        tenant_id="amh_drill",
        agent_version="drill@v0",
        action="process.start:t11_it_happy",
        decision="ALLOW",
        details={"business_key": "bk-happy", "note": "recovery-drill seed record"},
    )
    record_hash = await sink.emit(record)
    print(f"emitted record_hash={record_hash}")
    await sink.aclose()

asyncio.run(main())
PY
```

**3. Snapshot the Postgres data volume** — the local stand-in for "take a backup". Confirm the
actual volume name for your compose project first (it is `<project-dir-basename>_pgdata` by
Compose's default naming — e.g. `agent-wdocs_pgdata` for a worktree at `.../agent-wdocs`):

```bash
docker volume ls | grep pgdata
docker compose --profile core stop postgres
docker run --rm -v <project>_pgdata:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/audit-drill-backup.tgz -C /data .
docker compose --profile core start postgres
```

**4. Simulate loss and restore** — destroy the volume, recreate it from the snapshot:

```bash
docker compose --profile core down
docker volume rm <project>_pgdata
docker volume create <project>_pgdata
docker run --rm -v <project>_pgdata:/data -v "$(pwd)":/backup alpine \
  tar xzf /backup/audit-drill-backup.tgz -C /data
docker compose --profile core up -d
```

**5. Verify chain integrity against the restored data:**

```bash
uv run python -m maezo.gateway.audit_postgres \
  --dsn "postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5433}/maezo" \
  --tenant amh_drill
```

Expect JSON with `"valid": true` and `"total_records"` matching the pre-backup count (`1` if you
only did step 2 once).

**6. Verify against engine history** — for the business key seeded in step 2 (`bk-happy`), query
the same engine-history endpoint the transport layer itself uses
(`src/maezo/tools/mcp_cibseven/transport.py`, `processInstanceBusinessKey` query param):

```bash
curl -s "http://localhost:8080/engine-rest/history/process-instance?processInstanceBusinessKey=bk-happy" | jq .
```

Expect a non-empty array containing the matching process instance, with a state consistent with
what the corresponding audit record claims (a non-null `endTime`, `"state": "COMPLETED"`).
**There is no automated cross-check script in this repo for this step** — it is a manual, sampled
procedure using an existing REST endpoint, not push-button. (Note: the engine's own history
retention — `camunda:historyTimeToLive` on the test process — means this will eventually age out
independently of the Postgres restore; that is expected and not a chain-integrity finding.)

## Expected evidence

- `verify_chain` JSON output: `valid: true`, `total_records` matching the pre-backup count.
- For every sampled business key: an engine-history entry present and state-consistent with the
  corresponding audit record.
- Record the drill's date, operator, and both outputs (chain-verify JSON + history-query JSON)
  wherever this program tracks drill evidence (e.g. `docs/evidence-ledger.md`, if the owning team
  decides to log drills there — this document does not itself define that ledger row format).

## Abort criteria

- `verify_chain` returns `"valid": false` (tamper or gap/fork) → **STOP.** Do not point a live
  worker daemon at this restore. There is no supported repair path for a broken chain
  ([`audit-recovery.md`](../audit-recovery.md) §3) — escalate to security/compliance as a real
  incident, not a drill artifact to retry past.
- A sampled business key present in the audit chain but absent from engine history (or the
  reverse, outside of expected history-retention aging — see step 6's note) → **STOP** and treat
  as a genuine data-consistency finding. Do not re-sample business keys until you find one that
  happens to match; report the mismatch.

## §4 — Staging/prod track (requires infra not yet present in this repo's tooling)

Replace steps 3–4 above with the real Aurora restore commands documented in
[`audit-recovery.md`](../audit-recovery.md) §4 (`aws rds restore-db-cluster-to-point-in-time` /
`aws rds restore-db-cluster-from-snapshot` against `local.cluster_identifier =
"${var.name_prefix}-aurora-${var.environment}"`), then repeat steps 5–6 against the restored
cluster's endpoint (via the Aurora `ExternalSecret`'s `database-url`, per
[`worker-runtime.md`](../worker-runtime.md) §3). This repo has **no script wrapping that AWS
call**, and as of this writing no execution of this track has been recorded — when it is first
run for real, record the date, operator, and outcome here rather than assuming success from the
local track's pass.

## Cleanup

```bash
docker compose exec -T postgres psql -U maezo -d maezo -c 'DROP SCHEMA IF EXISTS amh_drill CASCADE;'
docker compose --profile core down -v   # matches CI's own teardown (see worker-runtime.md §5)
rm -f audit-drill-backup.tgz
```
