"""A normalizacao lexica do texto do beneficiario, em comum entre o pre-roteamento (plataforma) e as
respostas fixas de cadastro da Helena (ADR-0067). Morava em `platform/webhooks/whatsapp/pre_roteamento.py`."""

from __future__ import annotations

import re
import unicodedata

_NAO_PALAVRA = re.compile(r"[^0-9a-z]+")


def normalizar(texto: str) -> str:
    """Sem acento (NFKD), caixa baixa, pontuacao vira espaco, espacos colapsados."""
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return _NAO_PALAVRA.sub(" ", sem_acento.casefold()).strip()
