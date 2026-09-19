"""Fixed child observer for fresh ledger current execution; never a proof consumer."""

from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from types import FrameType
from typing import Any

SCHEMA = "maezo-ledger-current-observation/v1"


def main() -> int:
    # Only the parent runner creates this request. An exported request/report does
    # not authenticate itself and is never read as acceptance by the public CLI.
    request = json.loads(Path(sys.argv[1]).read_text())
    root = Path(request["root"])
    initial_environment = dict(os.environ)
    sys.path[:0] = [str(root / "src"), str(root)]
    executed: set[str] = set()

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "exec" and args and hasattr(args[0], "co_filename"):
            name = args[0].co_filename
            if not name.startswith("<"):
                executed.add(str(Path(name).resolve()))

    sys.addaudithook(audit)
    import hashlib

    import pytest

    class Observer:
        def __init__(self) -> None:
            self.discovered: list[dict[str, str]] = []
            self.selected: list[dict[str, str]] = []
            self.deselected: list[str] = []
            self.collection_errors: list[str] = []
            self.reports: list[dict[str, Any]] = []
            self.codes: dict[str, Any] = {}
            self.entered: set[str] = set()

        def identity(self, item: Any) -> dict[str, str]:
            function = inspect.unwrap(item.obj)
            code = getattr(function, "__code__", None)
            if code is None:
                raise RuntimeError("current observation requires an executable Python test body")
            self.codes[item.nodeid] = code
            return {
                "nodeid": item.nodeid,
                "file": str(item.path.resolve()),
                "body_file": str(Path(code.co_filename).resolve()),
                "body_digest": hashlib.sha256(code.co_code).hexdigest(),
            }

        def pytest_itemcollected(self, item: Any) -> None:
            self.discovered.append(self.identity(item))

        def pytest_collection_finish(self, session: Any) -> None:
            self.selected = [self.identity(item) for item in session.items]

        def pytest_deselected(self, items: list[Any]) -> None:
            self.deselected.extend(item.nodeid for item in items)

        def pytest_collectreport(self, report: Any) -> None:
            if report.outcome != "passed":
                self.collection_errors.append(report.nodeid)

        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_runtest_call(self, item: Any) -> Any:
            previous = sys.getprofile()
            target = self.codes.get(item.nodeid)

            def profile(frame: FrameType, event: str, arg: Any) -> None:
                if event == "call" and frame.f_code is target:
                    self.entered.add(item.nodeid)
                if previous is not None:
                    previous(frame, event, arg)

            sys.setprofile(profile)
            try:
                return (yield)
            finally:
                sys.setprofile(previous)

        def pytest_runtest_logreport(self, report: Any) -> None:
            self.reports.append(
                {
                    "nodeid": report.nodeid,
                    "phase": report.when,
                    "outcome": report.outcome,
                    "wasxfail": hasattr(report, "wasxfail"),
                    "body_entered": report.nodeid in self.entered,
                }
            )

        def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
            sources = set(executed)
            for module in tuple(sys.modules.values()):
                name = getattr(module, "__file__", None)
                if isinstance(name, str) and not name.startswith("<"):
                    sources.add(str(Path(name).resolve()))
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTEST_CURRENT_TEST", "PYTEST_VERSION"}
            }
            runtime = {
                "executable": str(Path(sys.executable).resolve()),
                "version": sys.version,
                "prefix": sys.prefix,
                "base_prefix": sys.base_prefix,
                "pytest_version": pytest.__version__,
                "initial_environment": initial_environment,
                "final_environment": environment,
                "plugin_autoload_disabled": session.config.getoption("disable_plugin_autoload"),
            }
            data = {
                "schema": SCHEMA,
                "run_id": request["run_id"],
                "root": str(root),
                "test_path": request["test_path"],
                "discovered": self.discovered,
                "selected": self.selected,
                "deselected": self.deselected,
                "collection_errors": self.collection_errors,
                "reports": self.reports,
                "finished": True,
                "exitstatus": int(exitstatus),
                "runtime": runtime,
                "sources": sorted(sources),
            }
            Path(request["observation"]).write_text(json.dumps(data, sort_keys=True) + "\n")

    observer = Observer()
    return int(pytest.main(request["pytest_argv"], plugins=[observer]))


if __name__ == "__main__":
    raise SystemExit(main())
