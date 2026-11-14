"""EXCECAO REGIONAL da zona PHI do DEV autorizada pelo DPO (21/10/2026) — temporaria, rotulada, so' dev.

Contexto: o GLM-5 e o Mistral Large 3 em sa-east-1 pararam de responder em 21/10/2026 (CloudTrail:
`InternalServerException`/`ServiceUnavailableException`), e nenhum modelo regional de Sao Paulo passou no
criterio do classificador. O DONO, como DPO, autorizou: "se nao funcionar o 1... tem que ir pro 2, desde que
funcione e resolva" — a zona PHI do dev usa o GLM-5 em us-east-1 ATE' a AWS normalizar sa-east-1. Producao
fora de escopo.

Como funciona: a excecao so' existe com `MAEZO_PHI_EXCECAO_REGIONAL` igual ao rotulo LITERAL abaixo.
Desligada (o padrao), tudo e' sa-east-1 como sempre: o endpoint de us-east-1 e' recusado e uma resposta
atestada fora do Brasil e' descartada. Ligada, o transporte fala com `bedrock-runtime.us-east-1` e a resposta
e' atestada HONESTAMENTE como `us-east-1` (nunca como Brasil); o provedor aceita essa regiao e so' ela. Toda
chamada leva a regiao no log.
"""

from __future__ import annotations

import os
from typing import Final

#: O rotulo da excecao (vai literal na env e no log de cada chamada).
ROTA_EXCECAO_US: Final[str] = "excecao_us_east_1_autorizada_dpo_2026-10-21"
ENV_PHI_EXCECAO_REGIONAL: Final[str] = "MAEZO_PHI_EXCECAO_REGIONAL"
REGIAO_EXCECAO: Final[str] = "us-east-1"
HOST_EXCECAO: Final[str] = "bedrock-runtime.us-east-1.amazonaws.com"
ENDPOINT_EXCECAO: Final[str] = f"https://{HOST_EXCECAO}"


def excecao_ativa() -> bool:
    """A excecao so' vale com o rotulo LITERAL na env (qualquer outro valor = desligada)."""
    return os.environ.get(ENV_PHI_EXCECAO_REGIONAL, "").strip() == ROTA_EXCECAO_US


__all__ = [
    "ENDPOINT_EXCECAO",
    "ENV_PHI_EXCECAO_REGIONAL",
    "HOST_EXCECAO",
    "REGIAO_EXCECAO",
    "ROTA_EXCECAO_US",
    "excecao_ativa",
]
