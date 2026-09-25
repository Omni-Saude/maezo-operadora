"""`python -m tools.staff_ops rows|job-init|human-init|job-run|syn|task-facts` (ver `__init__`)."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if len(args) == 1 else ""
    if command == "rows":
        from .rows import main as run
    elif command == "job-init":
        from .job import init_main as run
    elif command == "human-init":
        from .job import human_init_main as run
    elif command == "job-run":
        from .job import run_main as run
    elif command == "task-facts":
        from .facts import main as run
    elif command == "syn":
        from .syn import main as run
    else:
        print(
            "uso: python -m tools.staff_ops rows|job-init|human-init|job-run|syn|task-facts", file=sys.stderr
        )
        return 64
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
