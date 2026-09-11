"""Ephemeral guard for historical ledger recipe execution."""
from __future__ import annotations

import importlib
import json
import os
import sys
import sysconfig
from pathlib import Path

_ROOT = Path(os.environ["LEDGER_ARCHIVE_ROOT"]).resolve()
_GUARD_ROOT = Path(os.environ["LEDGER_SOURCE_GUARD_ROOT"]).resolve()
_MARKER = Path(os.environ["LEDGER_SOURCE_GUARD_MARKER"])
_TRUSTED = {
    Path(value).resolve()
    for value in sysconfig.get_paths().values()
    if value
}
_SEEN = set()


def _is_relative_to_any(path, roots):
    return any(path.is_relative_to(root) for root in roots)


def _record(raw):
    if not raw or raw.startswith("<"):
        return
    path = Path(raw).resolve()
    _SEEN.add(path)


def _audit(event, args):
    if event == "exec" and args:
        _record(getattr(args[0], "co_filename", None))


sys.addaudithook(_audit)


def _module_paths():
    paths = set(_SEEN)
    for _name, module in sorted(sys.modules.items()):
        raw = getattr(module, "__file__", None)
        if raw:
            _record(raw)
    paths.update(_SEEN)
    return sorted(paths)


def _escaped(paths):
    allowed = {*_TRUSTED, _ROOT, _GUARD_ROOT}
    return [path for path in paths if not _is_relative_to_any(path, allowed)]


def _archived(paths):
    return [path for path in paths if path.is_relative_to(_ROOT)]


def _write_marker(paths):
    _MARKER.write_text(
        json.dumps(
            {
                "archived": [str(path) for path in _archived(paths)],
                "escaped": [str(path) for path in _escaped(paths)],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def pytest_sessionstart(session):
    importlib.import_module("maezo")
    paths = _module_paths()
    escaped = _escaped(paths)
    if escaped:
        raise RuntimeError("historical source import escaped archive: " + ", ".join(map(str, escaped)))


def pytest_sessionfinish(session, exitstatus):
    paths = _module_paths()
    escaped = _escaped(paths)
    _write_marker(paths)
    if escaped:
        raise RuntimeError("historical source import escaped archive: " + ", ".join(map(str, escaped)))

_BASE_START = pytest_sessionstart
_BASE_FINISH = pytest_sessionfinish
_PHASE = {"started": False, "finished": False, "collected": [], "selected": [],
          "deselected": [], "phases": [], "collection_errors": [], "exitstatus": -999}


def pytest_sessionstart(session):
    _BASE_START(session)
    _PHASE["started"] = True


def pytest_itemcollected(item):
    _PHASE["collected"].append(item.nodeid)


def pytest_collection_finish(session):
    _PHASE["selected"] = [item.nodeid for item in session.items]


def pytest_deselected(items):
    _PHASE["deselected"].extend(item.nodeid for item in items)


def pytest_collectreport(report):
    if report.failed:
        _PHASE["collection_errors"].append(report.nodeid)


def pytest_runtest_logreport(report):
    _PHASE["phases"].append({"nodeid": report.nodeid, "when": report.when,
                             "outcome": report.outcome, "wasxfail": hasattr(report, "wasxfail")})


def pytest_sessionfinish(session, exitstatus):
    _BASE_FINISH(session, exitstatus)
    _PHASE["finished"] = True
    _PHASE["exitstatus"] = int(exitstatus)
    paths = _module_paths()
    result = {"archived": [str(p) for p in _archived(paths)],
              "escaped": [str(p) for p in _escaped(paths)], "coverage": _PHASE}
    fd = os.open(os.environ["HISTORICAL_PHASE_RECEIPT"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(result, output, sort_keys=True)
