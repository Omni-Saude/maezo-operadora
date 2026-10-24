"""TRAVAS DE SAIDA da Helena (ADR-0067 §5): regras fixas sobre o texto que vai sair, depois de tudo.

`sem_links` — trava `sem_link` (dono, 18/10/2026 e Plano de ajustes v2, etapa 10): a Helena NUNCA manda link
externo. Qualquer endereco (http/https, www. ou dominio como gov.br) sai do texto no ultimo passo antes do
envio, venha de onde vier (base de saude, redacao do modelo, texto antigo). A fonte continua citada pelo
NOME ("Fonte: Saude de A a Z (Ministerio da Saude)"); o rotulo que so' apresentava o link ("Para ler mais:",
"Link:") sai junto.
"""

from __future__ import annotations

import re
from typing import Final

import structlog

from .textos import LINK

logger = structlog.get_logger(__name__)

#: Rotulo que so' apresentava um link (sem link depois dele, nao diz nada).
_ROTULO_DE_LINK: Final[re.Pattern[str]] = re.compile(
    r"(?im)^[ \t]*(?:para (?:ler|saber) mais|leia mais|saiba mais|link|acesse|veja|site)\s*:[ \t]*$"
)
_ESPACOS: Final[re.Pattern[str]] = re.compile(r"[ \t]{2,}")
_ESPACO_ANTES_DE_PONTO: Final[re.Pattern[str]] = re.compile(r"[ \t]+([.,;:!?)])")
_PARENTESES_VAZIOS: Final[re.Pattern[str]] = re.compile(r"\(\s*\)")
_LINHAS_VAZIAS: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


def sem_links(texto: str) -> str:
    """O texto sem nenhum link (o nome da fonte fica). Sem link no texto: o MESMO texto, byte a byte."""
    if not LINK.search(texto):
        return texto
    limpo = LINK.sub("", texto)
    limpo = _ROTULO_DE_LINK.sub("", limpo)
    limpo = _PARENTESES_VAZIOS.sub("", limpo)
    limpo = _ESPACO_ANTES_DE_PONTO.sub(r"\1", limpo)
    limpo = _ESPACOS.sub(" ", limpo)
    limpo = "\n".join(linha.rstrip() for linha in limpo.split("\n"))
    limpo = _LINHAS_VAZIAS.sub("\n\n", limpo).strip()
    logger.warning("helena_link_retirado_na_saida")
    return limpo


__all__ = ["sem_links"]
