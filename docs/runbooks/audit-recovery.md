# Runbook: Audit Recovery — hash-chain design, integrity verification, recovery posture

**Audience:** Security, compliance, DBA, on-call
**Last updated:** 2026-08-11
**Applies to:** Maezo Healthcare Plan, all environments

> **What NOT to do, up front:** never hand-edit a row in the `audit_chain` table. The chain is
> tamper-evident by construction (§1) — any manual `UPDATE`/`INSERT`/`DELETE` against
> `audit_chain` breaks the hash links or the anti-fork constraint (or both) and will be caught
> the next time `verify_chain` runs (§2), but the record of *what actually happened* is gone.
> There is no "repair" operation for the chain itself — see §3.

---

## Table of Contents

1. [Hash-chain design](#1-hash-chain-design)
2. [Verifying chain integrity today](#2-verifying-chain-integrity-today)
3. [What NOT to do](#3-what-not-to-do)
4. [Recovery posture (backup/restore)](#4-recovery-posture-backuprestore)
5. [External anchor — pending Wave-4](#5-external-anchor--pending-wave-4)

---

## 1. Hash-chain design

**Code:** `src/maezo/gateway/audit_postgres.py` (module docstring + `PostgresAuditSink`),
`src/maezo/platform/migrations/versions/0002_audit_chain.py`

`PostgresAuditSink` (ADR-0007, DL-0018, B7) makes the audit trail durable by writing every
record straight through to the `audit_chain` table on `emit()` — no batching, no fire-and-forget,
no in-memory head cache. Design decisions, quoted from the module's own header (re-verify against
`docs/adr/0027-audit-transport-postgres-first.md` for the transport ADR):

- **Fail-closed emit.** `emit()` raises on any DB failure (connection loss, constraint
  violation, lock timeout). Callers MUST NOT swallow exceptions from `emit()` and continue — an
  unauditable action must not proceed silently.
- **No in-memory head cache.** Every `emit()` reads the current chain tail from Postgres *inside*
  the same locked transaction that inserts the new record. A fresh `PostgresAuditSink` instance
  (after a crash/restart, or a second replica) needs no explicit "recover head" step — its very
  first `emit()` already sees the true tail, because the tail is derived from durable storage,
  never process memory.
- **Per-tenant serialization, not global.** The platform is schema-per-tenant; `0002_audit_chain.py`'s
  `UNIQUE(prev_record_hash)` constraint is scoped to one tenant's own `audit_chain` table.
  `pg_advisory_xact_lock(hashtext($tenant_id))` serializes concurrent writers for the **same**
  tenant only — different tenants proceed fully in parallel; a global lock would serialize
  unrelated tenants for no correctness benefit.
- **Advisory lock is the primary guard; `UNIQUE(prev_record_hash)` is belt-and-suspenders.** The
  lock makes "read tail → compute hash → insert" atomic per tenant, so two concurrent `emit()`
  calls for the same tenant can never observe the same tail. If that lock were ever bypassed (a
  bug, a writer that doesn't use this sink, a second uncoordinated connection pool), the `UNIQUE`
  constraint on `prev_record_hash` turns a concurrent fork attempt into a hard
  `UniqueViolationError` instead of a silently-accepted second chain. The one gap a plain
  nullable column would leave — two genesis rows both satisfying `UNIQUE` because Postgres treats
  `NULL <> NULL` — is closed by `AuditRecord.GENESIS_PREV_HASH`, a real 64-char sentinel value,
  never SQL `NULL`.

`0002_audit_chain.py` additionally documents (DL-0018) that the chain is **non-partitioned** —
pruning/retention is enforced via `DELETE` by age, not table partitioning, specifically to
preserve the global `UNIQUE(prev_record_hash)` constraint (a partitioned table's `UNIQUE` would
have to include the partition key, opening a `(prev_record_hash, ts)` + non-atomic trigger gap
under `READ COMMITTED`).

PHI note (from the same module docstring): `AuditRecord.details` must already be safe-to-persist
structured data by the time it reaches this sink — the sink never receives, hashes, or stores raw
PHI; `input_hash` is a SHA-256 of `details`, never the identifiable details themselves.

## 2. Verifying chain integrity today

**Code:** `src/maezo/gateway/audit_postgres.py` (`verify_chain`, `_cli`, `ChainVerificationResult`)

```bash
uv run python -m maezo.gateway.audit_postgres --dsn "$DATABASE_URL" --tenant amh
```

This is a real, working CLI (`if __name__ == "__main__": sys.exit(_cli())` at the bottom of
`audit_postgres.py`) that walks the chain **structurally** from genesis via `prev_record_hash`
links (deliberately not by `timestamp`, which is clock-skew-fragile) and, for every record,
recomputes the hash from the stored row content and checks it against the stored `record_hash`,
and checks that `prev_record_hash` correctly links to the previous record's `record_hash`. It
detects two independent failure modes:

- **tamper** — a stored row's recomputed hash does not match its stored `record_hash`.
- **gap/fork** — some rows are not reachable by following `prev_record_hash` links from genesis
  (`verified_records < total_records`), e.g. an orphaned branch or a hole left by a partial write
  that somehow bypassed the sink's transactional guarantee.

Output is JSON (`ChainVerificationResult` fields: `tenant_id`, `valid`, `total_records`,
`verified_records`, `reason`, `break_at_hash`); the CLI exits `0` if valid, `1` otherwise. This
function **never raises** for a merely-invalid chain — an invalid chain is the expected,
reportable outcome — only connection-level failures raise. Because a tenant's chain lives
entirely within that tenant's own schema (schema-per-tenant), verification is inherently scoped
to `--tenant`; there is no cross-tenant chain to walk.

**Readiness-probe distinct from integrity verification.** `PostgresAuditSink.check_ready()` is a
*separate*, much cheaper check — a bounded, read-only `SELECT to_regclass('audit_chain')` under
the tenant's `search_path`, proving only that the sink can reach the table, not that its contents
are untampered. It backs the worker daemon's `/readyz` `audit_sink_ready` gate (see
[`worker-runtime.md`](worker-runtime.md)) and is not a substitute for `verify_chain` — a table
that is reachable but has been tampered with will still show `check_ready()` green.

## 3. What NOT to do

- **No manual row surgery.** Never `UPDATE`/`DELETE`/hand-`INSERT` against `audit_chain`. Every
  row's `record_hash` is a function of its own content plus `prev_record_hash`; editing one row
  breaks that row's hash and/or every descendant's chain link, and `verify_chain` will report it
  as `tamper` (not distinguishable, after the fact, from an actual attack) — you cannot "fix a
  typo" in an already-chained audit record.
- **No re-chaining / re-hashing scripts.** There is no supported tool in this repo to recompute
  and rewrite the chain after a manual edit; building one would defeat the entire tamper-evidence
  property this design exists to provide.
- **No silent restore-and-continue.** If a restore is performed (§4), `verify_chain` MUST be run
  against the restored data (and, once Wave-4 lands, checked against the external anchor — §5)
  before the daemon is allowed back into service. Do not assume a successful `pg_restore`/Aurora
  point-in-time-restore implies an intact chain.

## 4. Recovery posture (backup/restore)

**Code:** `deploy/terraform/modules/aurora-postgres/main.tf`, `variables.tf`

Production/staging Postgres is Aurora PostgreSQL, provisioned by the `aurora-postgres` Terraform
module. Backups are **native Aurora automated backups** — `backup_retention_period =
var.backup_retention_days` and `preferred_backup_window = "03:00-04:00"` are set on the cluster
resource; `deletion_protection`/`skip_final_snapshot`/`final_snapshot_identifier` are gated by
`var.enable_deletion_protection`. This is real, applied infrastructure (not aspirational) — but
**there is no repo-level restore script or wrapper**: restoring means using AWS's own tooling
against the cluster identified by `local.cluster_identifier =
"${var.name_prefix}-aurora-${var.environment}"`, e.g.:

```bash
# Point-in-time restore (standard AWS RDS/Aurora tooling — not wrapped by this repo):
aws rds restore-db-cluster-to-point-in-time \
  --source-db-cluster-identifier <cluster_identifier> \
  --db-cluster-identifier <cluster_identifier>-restored \
  --restore-to-time <ISO8601 timestamp>

# Or restore from a specific automated/manual snapshot:
aws rds restore-db-cluster-from-snapshot \
  --db-cluster-identifier <cluster_identifier>-restored \
  --snapshot-identifier <snapshot_id> \
  --engine aurora-postgresql
```

This is standard AWS operator knowledge, not a documented in-repo procedure — **requires infra
access (DBA/infra role) not exercised by this repo's own tooling.** After a restore completes
against a new/renamed cluster, point the application at it (`DATABASE_URL` in the Aurora
`ExternalSecret`, consumed by the `worker-daemon`/`agent-runtime` Deployments — see
[`worker-runtime.md`](worker-runtime.md) §3) and, before resuming traffic:

1. Apply any pending migrations for the tenant schema:
   `alembic -x tenant=<schema> upgrade head` (per `src/maezo/platform/migrations/env.py`'s own
   module docstring: "To run migrations targeting a specific tenant: `alembic -x tenant=amh
   upgrade head`"; the tenant may also be set via the `MAEZO_TENANT_ID` env var, which env.py
   reads directly — `_x_tenant or os.environ.get("MAEZO_TENANT_ID", "public")`).
2. Run `verify_chain` (§2) against the restored database for that tenant.
3. Only then allow the worker daemon / gateway back into the fetch-and-lock rotation.

> **Documentation/implementation discrepancy found while writing this runbook (reported, not
> fixed — out of scope for this leg).** `deploy/helm/maezo-tenant/templates/job-migrations.yaml`'s
> migration Job sets env vars `ALEMBIC_DATABASE_URL` and `MAEZO_TENANT` and runs `alembic upgrade
> head` (no `-x tenant=` argument) — its own header comment claims "env.py lê
> ALEMBIC_DATABASE_URL + MAEZO_TENANT (não DATABASE_URL/TENANT_ID)". Reading
> `src/maezo/platform/migrations/env.py` directly (`grep -n "ALEMBIC_DATABASE_URL\|MAEZO_TENANT\b"`
> — zero hits for either), the DSN is read only from `config.get_main_option("sqlalchemy.url")`
> (i.e. `alembic.ini`'s `sqlalchemy.url`, a literal pointing at the local compose Postgres by
> default) and the tenant only from `MAEZO_TENANT_ID` (default `"public"`) or `-x tenant=`. As
> written, neither env var the Helm Job sets is consumed by the code it invokes — this looks like
> genuine drift between the Job script and a since-changed `env.py`, not a documentation nuance.
> **Do not rely on `ALEMBIC_DATABASE_URL`/`MAEZO_TENANT` for a manual recovery run** — use
> `-x tenant=<schema>` and set `sqlalchemy.url` (edit `alembic.ini` or pass `-x` if your Alembic
> version supports a URL override) until this is reconciled by a platform engineer with edit
> access to the Helm chart and/or migrations code.

## 5. External anchor — pending Wave-4

**Code:** `docs/adr/0029-audit-chain-pruning-reanchor.md`

The chain as it stands stops at the Postgres boundary: hash-linked + `UNIQUE(prev_record_hash)`
anti-fork, but with **no external anchor** against a privileged rewrite of the database itself
(a superuser or a restore from a maliciously-doctored snapshot could, in principle, replace the
whole table with an internally-consistent but false chain). ADR-0029 ("Audit-Chain Pruning via
Signed Checkpoint Re-Anchor") designs a signed-checkpoint re-anchor mechanism, but per
`PLANS.md` it is currently **Proposed — designed, not ratified, not implemented**; a WORM/
retention-lock external anchor writer (separate account, KMS/HSM key) is planned for a parallel
Wave-4 effort, built inert (flag-gated) until DPO ratification per that ADR.

**Anchor verify: pending Wave-4 merge.** This runbook will gain a §6 ("Verifying against the
external anchor") once that lands. Until then, §2's `verify_chain` (Postgres-internal structural
verification) is the full extent of what can be verified today — say so plainly to auditors
rather than implying an external-anchor check exists.
