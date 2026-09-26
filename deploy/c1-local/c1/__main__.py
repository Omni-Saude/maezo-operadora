"""`python -m c1 <passo>` — cada passo e um modulo; o run.sh define a ordem."""

from __future__ import annotations

import importlib
import sys

STEPS = {
    "tls": "c1.tls",
    "db-base": "c1.db_base",
    "materials": "c1.materials",
    "db-native": "c1.db_native",
    "engine-config": "c1.engine_config",
    "assemble": "c1.assemble_step",
    "seed": "c1.seed",
    "publish": "c1.publish",
    "auth-install": "c1.auth_install",
    "auth-fixture": "c1.auth_fixture",
    "issuer": "c1.issuer",
    "w1": "c1.w1",
    "portal-init": "c1.portal_check",
    "portal": "c1.portal_check",
    "h1-task": "c1.h1_task",
    "assignment": "c1.assignment_step",
    "portal-human": "c1.portal_human",
    "rotate": "c1.rotate",
    "rotate-issuer": "c1.rotate_issuer",
    "portal-r2": "c1.portal_check",
    "supervisor": "c1.supervisor",
    "portal-sup": "c1.portal_check",
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        raise SystemExit(f"uso: python -m c1 {{{','.join(STEPS)}}}")
    importlib.import_module(STEPS[sys.argv[1]]).main()


if __name__ == "__main__":
    main()
