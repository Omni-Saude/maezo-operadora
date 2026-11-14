"""ONDE BAIXAR O APLICATIVO (teste do dono de 25/10/2026). Modulo FOLHA (sem import do grafo).

"Onde eu baixo esse aplicativo?" caia na pergunta de canal (`graph._pergunta_de_canal`: "aplicativo") e
recebia "Nao tenho um link para mandar por aqui. A carteirinha digital e os detalhes do plano ficam no
aplicativo...": nao dizia COMO achar o aplicativo. A resposta do dono, sem link nenhum (dono, 18/10/2026: a
fonte vai so' pelo nome), mora no pacote de textos (chave `app_onde_baixar`).
"""

from __future__ import annotations

import re
from typing import Final

from maezo.agents.helena.habilidades.conversa.leitura_curta import normalizar

CHAVE_APP_ONDE_BAIXAR: Final[str] = "app_onde_baixar"
TEXTO_APP_ONDE_BAIXAR: Final[str] = (
    "Procure por 'Austa Clínicas' na loja de aplicativos do seu celular (Google Play ou App Store)."
)
PADRAO: Final[dict[str, str]] = {CHAVE_APP_ONDE_BAIXAR: TEXTO_APP_ONDE_BAIXAR}

_APP: Final[str] = r"(?:app|apps|aplicativo|aplicativos|aplicacao)"
#: Baixar/instalar/achar o aplicativo, o nome dele, ou o link dele (o link nunca sai: sai como procurar).
_ONDE_BAIXAR: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:baix\w*|instal\w*|download)\b[^?]{{0,40}}\b{_APP}\b"
    rf"|\b{_APP}\b[^?]{{0,40}}\b(?:baix\w*|instal\w*|download)\b"
    rf"|\b(?:onde|como) (?:eu )?(?:acho|encontro|consigo|pego|arrumo|tenho)\b[^?]{{0,20}}\b{_APP}\b"
    rf"|\bnome d[oe] (?:esse |este |seu |o )?{_APP}\b"
    rf"|\blink d[oe] (?:esse |este |seu |o )?{_APP}\b"
)
#: Outro aplicativo nomeado (governo, banco, mensageiro): a resposta do Austa Clinicas nao serve.
_OUTRO_APP: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sus|conecte|gov|governo|banco|caixa|whatsapp|zap|telegram|uber|ifood|nubank|pix)\b"
)


def pergunta_onde_baixar_o_app(texto: object) -> bool:
    """A mensagem pergunta onde baixar/achar o aplicativo do plano (sem nomear outro aplicativo)?"""
    if not isinstance(texto, str):
        return False
    plano = normalizar(texto)
    return bool(_ONDE_BAIXAR.search(plano)) and not _OUTRO_APP.search(plano)


__all__ = ["CHAVE_APP_ONDE_BAIXAR", "PADRAO", "TEXTO_APP_ONDE_BAIXAR", "pergunta_onde_baixar_o_app"]
