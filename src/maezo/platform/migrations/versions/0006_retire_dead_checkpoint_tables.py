"""Correct the checkpoint-schema record + retire dead placeholder tables (T3.4 F4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26

Audit finding (T3.4 R1, F4 -- checkpoint-schema TRIPLE divergence):

`0001_schema_agents.py` created `agent_checkpoints` / `agent_checkpoint_writes` with a comment
claiming the schema "matches langgraph expectations" (0001, lines 30-31 as originally written).
That claim was FALSE the day it was written and remains false: `langgraph-checkpoint-postgres`'s
`PostgresSaver` (wrapped by `maezo.runtime.checkpoint.Checkpointer`) never reads or writes
`agent_checkpoints` / `agent_checkpoint_writes`. Its own internal `MIGRATIONS` list
(`langgraph/checkpoint/postgres/base.py`) provisions FOUR different, unqualified tables via
`PostgresSaver.setup()` / `.asetup()`:

  - `checkpoint_migrations` -- the saver's own schema-version bookkeeping.
  - `checkpoints`           -- the working-layer checkpoint row itself.
  - `checkpoint_blobs`      -- BYTEA channel values (PHI-bearing), keyed by
                               (thread_id, checkpoint_ns, channel, version) -- NOT by
                               fhir_patient_id.
  - `checkpoint_writes`     -- BYTEA pending writes, keyed by
                               (thread_id, checkpoint_ns, checkpoint_id, task_id, idx).

None of those four tables is created by this Alembic chain anywhere -- they are provisioned
imperatively by calling `.setup()` (sync) / `.asetup()` (async) on a `PostgresSaver` instance.
T3.4 F4 confirmed by repo-wide sweep (`grep -rn '\\.setup(\\|\\.asetup(' src/`) that this
codebase calls neither, anywhere: `Checkpointer` (`runtime/checkpoint.py`) is wired into
`Harness` (`runtime/harness.py`), but the only production call site
(`runtime/agent_runtime/service.py::_load_agent_graph`) constructs `Harness(inference=...,
tool_deps=...)` with NO checkpointer at all and calls `graph.compile()` purely to validate graph
structure at readiness-check time -- it never runs a node and never touches Postgres for
checkpointing. Checkpoint persistence is therefore, today, wired nowhere in prod bootstrap. This
migration does not change that: provisioning `.setup()`/`.asetup()` is a separate, deliberate
decision outside T3.4's scope -- flagged here (and in the T3.4 F4 report), not silently wired.

`agent_checkpoints` / `agent_checkpoint_writes` were therefore never the langgraph checkpointer's
tables. The T3.4 F4 sweep found nothing that reads or writes either name: no INSERT/SELECT/DELETE
against them anywhere in `src/` or `tests/`; the only other mentions were `platform/erasure.py`'s
docstrings (which cited the same wrong names -- corrected separately, same T3.4 batch) and one
ADR-0027 aside (informational cross-reference, not a functional dependency). They are dead weight
that actively misleads a reader of 0001 into believing checkpoint persistence is Alembic-managed.

This migration drops both dead tables. 0001's own file is left untouched -- Alembic migrations
are an append-only historical record; correcting a past migration's prose in place would rewrite
history without changing any already-applied database state. This file (and the corrected
`erasure.py` docstrings) is the durable correction of the record. If checkpoint persistence is
ever wired for real, the correct fix is calling `PostgresSaver(...).setup()` / `.asetup()` once
per tenant connection -- NOT adding more Alembic DDL under these table names.

NOTA DE RECONCILIACAO 2026-09-05 (gap AF-18a, docs-only; APPEND-ONLY -- nenhuma linha acima foi
alterada, exatamente pela regra que este proprio arquivo enuncia: uma migration e registro
historico append-only). O paragrafo acima que diz "Checkpoint persistence is therefore, today,
wired nowhere in prod bootstrap" era VERDADE quando esta migration foi escrita (T3.4, 2026-07-26) e
DEIXOU DE SER depois, em T4b. Hoje o checkpointer duravel e provisionado em DOIS bring-ups, ambos
FAIL-CLOSED em producao:

  - `maezo.runtime.agent_runtime.service` -- `AgentState.checkpointer` e a sonda de prontidao
    `checkpointer_ready` (o daemon RECUSA rodar stateless quando `agent_runtime_mode` e producao);
    o saver e injetado em `_load_agent_graph(..., checkpointer=...)`.
  - `maezo.platform.webhooks.service` -- `_provision_dispatch_checkpointer`, que devolve `/webhook`
    501 quando nao consegue provisionar.

Ambos passam por `maezo.runtime.checkpoint.provision_checkpointer`, que AGUARDA `saver.setup()` --
ou seja, a chamada cuja ausencia o paragrafo acima documentava (a varredura T3.4 F4 de
`.setup(`/`.asetup(` daria hits hoje). Duas consequencias que NAO mudam:

  1. As quatro tabelas do langgraph (`checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`,
     `checkpoint_writes`) continuam provisionadas IMPERATIVAMENTE por `setup()`, nunca por esta
     cadeia Alembic -- a afirmacao central desta migration segue correta, e e por isso que
     `spec/policies/retention/erasure-plan.template.yaml` as enumera a parte.
  2. `agent_checkpoints`/`agent_checkpoint_writes` continuam mortas e removidas por esta migration.

Nao ha citacao por numero de linha nesta nota, de proposito: a correcao estrutural do gap AF-18a e
ancorar por SIMBOLO (modulo::simbolo), porque foi a deriva de `service.py:489,512` e de
`audit.py:310-344` que fez a auditoria original citar codigo que ja tinha se movido.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dead tables from 0001 -- never read or written by anything (T3.4 F4 sweep, see module
    # docstring). The REAL langgraph checkpointer tables (checkpoints / checkpoint_blobs /
    # checkpoint_writes / checkpoint_migrations) are provisioned by `PostgresSaver.setup()` /
    # `.asetup()`, not Alembic.
    op.execute("DROP TABLE IF EXISTS agent_checkpoint_writes CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_checkpoints CASCADE")


def downgrade() -> None:
    # Recreate the (dead, placeholder) tables exactly as 0001 defined them, for chain
    # reversibility only. Restoring these tables does NOT restore any checkpoint
    # functionality -- they were never wired to anything (see module docstring).
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_checkpoints (
            thread_id         text NOT NULL,
            checkpoint_ns     text NOT NULL DEFAULT '',
            checkpoint_id     text NOT NULL,
            parent_checkpoint_id text,
            type              text,
            checkpoint        jsonb NOT NULL,
            metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at        timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_checkpoint_writes (
            thread_id         text NOT NULL,
            checkpoint_ns     text NOT NULL DEFAULT '',
            checkpoint_id     text NOT NULL,
            task_id           text NOT NULL,
            idx               integer NOT NULL,
            channel           text NOT NULL,
            type              text,
            value             jsonb,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
    """)
