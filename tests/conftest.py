"""Suite-wide fixtures.

Today this file holds exactly one thing: structlog configuration isolation. It exists because of
AF-13, and the reason is worth stating rather than leaving to be rediscovered.

WHAT CHANGED. `maezo.platform.observability.setup_observability` configures structlog GLOBALLY
(`processors`, `wrapper_class`, `logger_factory`, and `cache_logger_on_first_use=True`). Until
AF-13 it had no production caller, so nothing in a test run ever invoked it except the handful of
tests that test it directly. Wiring the three composition roots means any test that drives
`agent_runtime` / `worker_runtime` / `gateway` bring-up now reconfigures structlog for the REST OF
THE PROCESS.

WHY THAT BREAKS OTHER TESTS, MECHANICALLY. `cache_logger_on_first_use=True` makes
`BoundLoggerLazyProxy.bind` install a `finalized_bind` closure as an INSTANCE attribute on the
proxy (structlog `_config.py`), capturing the processor chain that was configured at that moment.
Every module-level `logger = structlog.get_logger(__name__)` in `src/` is such a proxy. Once one is
cached, `structlog.testing.capture_logs()` — which works by swapping the GLOBAL processor chain —
can no longer intercept it, and the event goes to stdout instead of the capture list. Seven tests
in `tests/unit/runtime/` asserting on captured log events failed exactly this way, and only when
run after the daemon-bring-up tests: a pure test-ORDER dependency, invisible when each file is run
alone.

WHY THIS IS THE RIGHT PLACE TO FIX IT, and not `setup_observability`. Caching bound loggers is a
legitimate production choice (it is a hot path), and weakening a shipped module's configuration to
make a test suite pass would be fixing the wrong object. What is not legitimate is one test leaking
global process state into the next. So this restores the configuration a test found, and — only
when a test actually changed it — clears the per-proxy caches that configuration installed.

COST. The `sys.modules` walk is guarded by an equality check on the configuration, so it runs only
for the few tests that reconfigure structlog, not for the ~8000 that do not.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

#: The lazy-proxy type `structlog.get_logger()` returns. Resolved through the public API rather
#: than by importing `structlog._config`, so this does not depend on a private module path.
_LAZY_PROXY_TYPE = type(structlog.get_logger("maezo.tests.conftest.probe"))

#: The instance attribute `BoundLoggerLazyProxy.bind` installs when it caches. Deleting it returns
#: the proxy to its lazy state, so the NEXT bind re-reads the (restored) global configuration.
_CACHED_BIND_ATTR = "bind"


def _uncache_lazy_loggers() -> int:
    """Return every module-level structlog proxy to its lazy state. Returns how many were cleared.

    Total by construction: a module whose `__dict__` cannot be read, or a value whose `isinstance`
    check raises, is skipped rather than allowed to fail a test's teardown.
    """
    cleared = 0
    for module in list(sys.modules.values()):
        namespace: Any = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        for value in list(namespace.values()):
            try:
                is_proxy = isinstance(value, _LAZY_PROXY_TYPE)
            except Exception:  # noqa: BLE001 — exotic __class__ descriptors must not break teardown.
                continue
            if is_proxy and _CACHED_BIND_ATTR in vars(value):
                del value.__dict__[_CACHED_BIND_ATTR]
                cleared += 1
    return cleared


@pytest.fixture(autouse=True)
def _structlog_configuration_isolation() -> Iterator[None]:
    """Restore the structlog configuration a test found, and drop caches it caused (AF-13).

    See this module's docstring for why. Deliberately autouse and suite-wide: the leak comes from
    whichever test happens to bring a daemon up, and the victims are in unrelated files, so an
    opt-in fixture would have to be applied to the tests that are NOT at fault.
    """
    snapshot = dict(structlog.get_config())
    yield
    if dict(structlog.get_config()) != snapshot:
        structlog.configure(**snapshot)
        _uncache_lazy_loggers()
