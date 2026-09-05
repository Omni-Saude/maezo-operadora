"""D6-03: `alembic.ini`'s local-dev `sqlalchemy.url` no longer hardcodes the literal credential
`maezo:maezo` -- each `${VAR:-default}` token in the URL now resolves against `os.environ`,
falling back to the SAME default that shipped before (`src/maezo/platform/migrations/env.py`'s
`_expand_env_defaults`).

`env.py` is an alembic environment script: it is executed by alembic's own machinery (which sets
up `alembic.context` as a real `EnvironmentContext` first) and is NOT importable as a normal
module — `from maezo.platform.migrations import env` raises `AttributeError: module
'alembic.context' has no attribute 'config'` outside a real alembic invocation, by design (module
docstring). This test therefore:

1. Extracts and execs ONLY `_ENV_VAR_DEFAULT_RE`/`_expand_env_defaults` straight from the real
   `env.py` source via `ast`, so the assertions below exercise the actual production regex/function
   body, never a hand-duplicated copy that could silently drift from it.
2. Separately runs the REAL alembic offline `--sql` pipeline (via `alembic.command.upgrade(...,
   sql=True)`, no DB connection) against the real `alembic.ini`, proving the `${VAR:-default}`
   syntax does not break offline SQL generation end-to-end (the concrete regression this gap's
   fix could have introduced).
"""

from __future__ import annotations

import ast
import io
import os
import re
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_PY = _REPO_ROOT / "src" / "maezo" / "platform" / "migrations" / "env.py"
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


def _load_expand_env_defaults():
    """Exec just the `_ENV_VAR_DEFAULT_RE` assignment and the `_expand_env_defaults` def from the
    real env.py source (via ast), sidestepping the module's alembic.context side effects."""
    tree = ast.parse(_ENV_PY.read_text(encoding="utf-8"), filename=str(_ENV_PY))
    wanted_names = {"_ENV_VAR_DEFAULT_RE"}
    wanted_funcs = {"_expand_env_defaults"}
    nodes: list[ast.stmt] = []
    for node in tree.body:
        is_wanted_assign = isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in wanted_names for t in node.targets
        )
        is_wanted_func = isinstance(node, ast.FunctionDef) and node.name in wanted_funcs
        if is_wanted_assign or is_wanted_func:
            nodes.append(node)
    assert len(nodes) == 2, (
        f"expected 1 assignment + 1 function from env.py, found {len(nodes)} -- "
        "did _ENV_VAR_DEFAULT_RE/_expand_env_defaults get renamed or removed?"
    )
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    ns: dict[str, object] = {"re": re, "os": os}
    exec(compile(module, str(_ENV_PY), "exec"), ns)  # noqa: S102 -- test-only, real source, no input
    return ns["_expand_env_defaults"]


@pytest.fixture()
def expand_env_defaults():
    return _load_expand_env_defaults()


def test_alembic_ini_declares_the_var_default_syntax_not_a_literal_credential() -> None:
    """Pin D6-03's actual edit: `alembic.ini` must not contain the bare literal `maezo:maezo`
    any more, and must use the `${VAR:-default}` form for user/password/db."""
    text = _ALEMBIC_INI.read_text(encoding="utf-8")
    assert "maezo:maezo" not in text
    assert "${MAEZO_PG_USER:-maezo}" in text
    assert "${MAEZO_PG_PASSWORD:-maezo}" in text
    assert "${MAEZO_PG_DB:-maezo}" in text


def test_expand_env_defaults_falls_back_to_the_pre_existing_default(
    expand_env_defaults, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env vars set -> byte-identical to the credential that shipped before (no behaviour
    change for dev)."""
    monkeypatch.delenv("MAEZO_PG_USER", raising=False)
    monkeypatch.delenv("MAEZO_PG_PASSWORD", raising=False)
    monkeypatch.delenv("MAEZO_PG_DB", raising=False)
    url = "postgresql+asyncpg://${MAEZO_PG_USER:-maezo}:${MAEZO_PG_PASSWORD:-maezo}@postgres:5432/${MAEZO_PG_DB:-maezo}"
    assert expand_env_defaults(url) == "postgresql+asyncpg://maezo:maezo@postgres:5432/maezo"


def test_expand_env_defaults_honors_real_environment_variables(
    expand_env_defaults, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the real env vars ARE set, they win over the default -- the whole point of the fix."""
    monkeypatch.setenv("MAEZO_PG_USER", "probeuser")
    monkeypatch.setenv("MAEZO_PG_PASSWORD", "probepass")
    monkeypatch.setenv("MAEZO_PG_DB", "probedb")
    url = "postgresql+asyncpg://${MAEZO_PG_USER:-maezo}:${MAEZO_PG_PASSWORD:-maezo}@postgres:5432/${MAEZO_PG_DB:-maezo}"
    assert expand_env_defaults(url) == "postgresql+asyncpg://probeuser:probepass@postgres:5432/probedb"


def test_expand_env_defaults_is_a_no_op_on_a_url_without_any_tokens(expand_env_defaults) -> None:
    """A real production DSN (no `${...}` tokens) must pass through unchanged -- the substitution
    must never touch a URL it was not asked to."""
    real_url = "postgresql+asyncpg://realuser:S3cr3t%25@aurora.example.internal:5432/maezo"
    assert expand_env_defaults(real_url) == real_url


def test_alembic_offline_sql_generation_still_works_with_the_var_default_syntax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The concrete regression this gap's fix could introduce: `${VAR:-default}` characters in
    `sqlalchemy.url` breaking alembic/SQLAlchemy's own URL parsing. Runs the REAL `alembic.ini` +
    `env.py` through alembic's real offline (`--sql`, no DB connection) pipeline, for both the
    default-only and the env-var-override cases."""
    monkeypatch.chdir(_REPO_ROOT)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for var in ("MAEZO_PG_USER", "MAEZO_PG_PASSWORD", "MAEZO_PG_DB"):
        monkeypatch.delenv(var, raising=False)

    from alembic import command
    from alembic.config import Config

    # env.py's module-level `fileConfig(config.config_file_name)` (alembic's own logging setup,
    # unrelated to this test) defaults to `disable_existing_loggers=True` -- a REAL, global,
    # cross-test side effect: it silently disables every logger not named in alembic.ini's
    # [loggers] section for the rest of the pytest process, breaking unrelated tests elsewhere
    # (e.g. `test_harness.py`'s `structlog.testing.capture_logs`) that run afterwards. Neutered
    # here since this test only cares about SQL generation, never alembic's own console output.
    monkeypatch.setattr("logging.config.fileConfig", lambda *a, **k: None)

    cfg = Config(str(_ALEMBIC_INI))
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "0001", sql=True)
    assert "CREATE TABLE" in buf.getvalue()

    # Same run, with the env vars overridden -- must still succeed (offline mode never actually
    # connects, so this proves the URL keeps parsing, not that the override reaches a live DB).
    monkeypatch.setenv("MAEZO_PG_USER", "probeuser")
    monkeypatch.setenv("MAEZO_PG_PASSWORD", "probepass")
    monkeypatch.setenv("MAEZO_PG_DB", "probedb")
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        command.upgrade(cfg, "0001", sql=True)
    assert "CREATE TABLE" in buf2.getvalue()
