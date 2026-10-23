"""Entrypoint `python -m maezo.platform.agente_remoto` (ADR-0067 fase 4b). Ver `servico.py`.

Importar este modulo nao tem efeito colateral (nenhum `asyncio.run` no import).
"""

from __future__ import annotations

import asyncio

from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

from .servico import run


def main() -> None:
    # Mesma friccao pydantic-settings/mypy do receptor (`webhooks/__main__.py`): os campos obrigatorios vem do
    # ambiente em tempo de execucao, e a construcao levanta se faltarem.
    asyncio.run(run(WhatsAppWebhookSettings()))  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
