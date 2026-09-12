"""Read-only D7-D operator CLI. Legacy bootstrap entrypoint remains unchanged."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from maezo.gateway.engine_contracts import canonical_json
from maezo.platform.engine_bootstrap.secured_plan import ProvisioningPlanError, compile_boundary_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile a public D7 boundary grant plan; never executes it."
    )
    parser.add_argument("--boundary-file", type=Path, required=True)
    parser.add_argument("--boundary-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        path = args.boundary_file
        if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
            raise ProvisioningPlanError()
        with path.open("rb") as stream:
            raw = stream.read(1_048_577)
        plan = compile_boundary_plan(raw, expected_sha256=args.boundary_sha256)
        sys.stdout.write(canonical_json(plan).decode("utf-8") + "\n")
        return 0
    except Exception:
        sys.stderr.write("engine_provisioning_plan_refused\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
