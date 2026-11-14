"""Entrypoint `python -m maezo.platform.agente_remoto` (ADR-0067 fase 4b). Ver `servico.py`.

Importar este modulo nao tem efeito colateral (nenhum `asyncio.run` no import).
"""

from __future__ import annotations

import asyncio

from .servico import run
from .settings import AgenteRemotoSettings


def main() -> None:
    # Settings proprias: as do receptor SEM os segredos do webhook (que o agente nao recebe nem guarda).
    asyncio.run(run(AgenteRemotoSettings()))


if __name__ == "__main__":
    main()
