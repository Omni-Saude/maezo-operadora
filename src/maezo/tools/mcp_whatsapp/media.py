"""Download de midia da WhatsApp Cloud API (DL-0162: o pedido de reembolso recebe foto/PDF).

Duas chamadas, como a Meta exige: `GET {base}/{media_id}` devolve a URL curta (e o mime e o tamanho
declarados); `GET {url}` devolve os bytes. As duas levam o mesmo bearer. O que este modulo garante:

  * so' mime da lista fechada (`image/jpeg`, `image/png`, `application/pdf`) e tamanho ate' o teto — o
    tamanho e' conferido ANTES de baixar (declarado) e DEPOIS (real), e o corpo e' lido em pedacos, nunca
    inteiro na memoria alem do teto;
  * nenhum `media_id`, URL, token ou conteudo em log ou em excecao: a Meta embute identificadores que
    ligam o arquivo ao numero. Toda falha vira `MidiaRecusada` com um CODIGO FECHADO;
  * o arquivo nao e' lido nem interpretado (sem OCR): so' bytes e mime.

Nao guarda nada: quem chama entrega os bytes a custodia da Zona PHI (ADR-0006).

Este modulo so' tem os TIPOS e a regra de mime (sem rede). O cliente HTTP (`WhatsAppMediaClient`) vive em
`server.py`, o modulo que o desenho do §8.2 (`check_effect_chokepoint_fence`) autoriza a construir o
`httpx.AsyncClient` da Meta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

MIMES_ACEITOS: Final[frozenset[str]] = frozenset({"image/jpeg", "image/png", "application/pdf"})
#: 10 MiB: acima do que um celular produz numa foto de nota fiscal e abaixo do teto da Meta (100 MiB p/ doc).
TAMANHO_MAXIMO_BYTES: Final[int] = 10 * 1024 * 1024
PEDACO_BYTES: Final[int] = 64 * 1024

MotivoRecusa = Literal["mime", "tamanho", "indisponivel", "id_invalido"]


class MidiaRecusada(Exception):  # noqa: N818 - nome do dominio; nao e' um erro de programacao
    """O arquivo nao foi baixado. `motivo` e' fechado; nenhuma mensagem carrega id, URL ou conteudo."""

    def __init__(self, motivo: MotivoRecusa) -> None:
        super().__init__(motivo)
        self.motivo: MotivoRecusa = motivo


@dataclass(frozen=True, slots=True)
class MidiaBaixada:
    conteudo: bytes = field(repr=False)
    mime: str


class BaixadorDeMidia(Protocol):
    async def baixar(self, media_id: str) -> MidiaBaixada: ...


def mime_aceito(mime: object) -> bool:
    """So' o tipo, sem parametros (`image/jpeg; charset=...` vale `image/jpeg`)."""
    return isinstance(mime, str) and mime.split(";", 1)[0].strip().lower() in MIMES_ACEITOS


def mime_limpo(mime: str) -> str:
    return mime.split(";", 1)[0].strip().lower()
