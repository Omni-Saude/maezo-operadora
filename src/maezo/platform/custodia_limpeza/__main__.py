"""Entrada do job: `python -m maezo.platform.custodia_limpeza` (uma rodada; sem argumentos)."""

from __future__ import annotations

import asyncio
import sys

from .job import LimpezaCustodiaSettings, rodar


def main() -> int:
    return asyncio.run(rodar(LimpezaCustodiaSettings()))


if __name__ == "__main__":
    sys.exit(main())
