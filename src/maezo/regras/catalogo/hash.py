"""Hash POR REGRA `maezo-regra-hash.v1` (blueprint §3.3).

    regra_sha256 = sha256( JCS({schema, id, tipo, fonte_sha256:[...], aprovam:sorted, ciencia:sorted,
                                titulo, regra, exemplo, orientacao, o_que_e, o_que_nao_e,
                                norma_sha256|null, validade, editavel}) )

Fora do hash, de propósito: `aprovacoes`, `liberacoes`, `comprovacoes`, `estado_declarado`,
`historico_de_mudanca` e `alias` — senão transcrever uma aprovação mudaria o hash que ela aprova
(laço). `fonte_sha256` segue a ORDEM declarada das fontes (reordenar é mudança revisável).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from maezo.regras.canonico import sha256_canonico
from maezo.regras.catalogo.extratores import fonte_sha256
from maezo.regras.catalogo.schema import Regra

SCHEMA_HASH: Final[str] = "maezo-regra-hash.v1"


def material_do_hash(regra: Regra, fontes_sha256: list[str]) -> dict[str, Any]:
    """O objeto que é canonicalizado — exposto para os vetores de teste e para a paridade."""
    return {
        "schema": SCHEMA_HASH,
        "id": regra.id,
        "tipo": regra.tipo,
        "fonte_sha256": list(fontes_sha256),
        "aprovam": sorted(regra.aprovam),
        "ciencia": sorted(regra.ciencia),
        "titulo": regra.titulo,
        "regra": regra.regra,
        "exemplo": regra.exemplo,
        "orientacao": regra.orientacao,
        "o_que_e": regra.o_que_e,
        "o_que_nao_e": regra.o_que_nao_e,
        "norma_sha256": regra.norma.sha256 if regra.norma is not None else None,
        "validade": regra.validade,
        "editavel": [dict(item) for item in regra.editavel],
    }


def fontes_sha256(raiz: Path, regra: Regra) -> list[str]:
    """Digest de cada fonte, na ordem declarada. `FonteError` se alguma não resolve."""
    return [fonte_sha256(raiz, regra.tipo, fonte) for fonte in regra.fonte]


def regra_sha256(raiz: Path, regra: Regra) -> str:
    """O hash que a aprovação amarra (B4). Recalculado pelo CI, pelo portal e pelo `regras-sync`."""
    return sha256_canonico(material_do_hash(regra, fontes_sha256(raiz, regra)))


__all__ = ["SCHEMA_HASH", "fontes_sha256", "material_do_hash", "regra_sha256"]
