"""Erros com `code` fixo; nunca ecoam valor."""

from __future__ import annotations


class _CodigoFixoError(Exception):
    code: str = ""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CanalDeEmailRecusado(_CodigoFixoError):  # noqa: N818
    """O canal nao foi admitido ou esta desligado."""


class ComposicaoRecusada(_CodigoFixoError):  # noqa: N818
    """O corpo nao pode ser composto."""


class EventoRecusado(_CodigoFixoError):  # noqa: N818
    """Evento do provedor recusado."""
