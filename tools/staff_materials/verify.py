"""`verify` — o pacote e conferido pelo PROPRIO loader do portal, com pins que o aprovador forneceu.

Nao existe um segundo verificador aqui: `decode_bundle` (`maezo.gateway.staff_cases.materials`) e
o que o init do portal roda antes de materializar o pacote. Os pins vem de um arquivo escrito pelo
aprovador com os nomes dos campos de `PortalProductionSettings` (os mesmos `MAEZO_PORTAL_*` do
deploy, sem o prefixo). Nunca se derivam pins do proprio pacote: isso seria o pacote atestando a
si mesmo.
"""

from __future__ import annotations

import json
import os
from typing import Any

from maezo.gateway.external_cases.models import digest, parse
from maezo.gateway.staff_cases.materials import decode_bundle
from maezo.gateway.staff_cases.production_config import PortalProductionSettings, PublicManifest

from .secure_io import MaterialError


def _no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise MaterialError("arquivo de pins com campo repetido")
        result[key] = value
    return result


def load_pins(raw: bytes) -> PortalProductionSettings:
    # BaseSettings tambem le o ambiente. Um `MAEZO_PORTAL_*` solto na shell do aprovador mudaria
    # o que esta sendo conferido sem aparecer no arquivo: recusar e mais honesto do que ignorar.
    if any(name.upper().startswith("MAEZO_PORTAL_") for name in os.environ):
        raise MaterialError("rode o verify sem nenhuma variavel MAEZO_PORTAL_* no ambiente")
    try:
        values = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
        if type(values) is not dict:
            raise MaterialError("arquivo de pins precisa ser um objeto")
        return PortalProductionSettings(**values)
    except MaterialError:
        raise
    except Exception:
        raise MaterialError("pins recusados pela validacao de settings do portal") from None


def verify_bundle(raw: bytes, pins: PortalProductionSettings) -> PublicManifest:
    try:
        manifest, _ = decode_bundle(raw, pins)
    except Exception:
        # O loader deliberadamente nao diz o motivo; a ferramenta tambem nao inventa um.
        raise MaterialError("pacote recusado pelo loader do portal (decode_bundle)") from None
    return manifest


def manifest_digest(raw: bytes) -> str:
    """O `staff_public_manifest_sha256` que o aprovador calcula POR ULTIMO, depois de conferir."""
    try:
        return digest(parse(PublicManifest, raw).wire())
    except Exception:
        raise MaterialError("manifesto fora do perfil fechado do loader") from None
