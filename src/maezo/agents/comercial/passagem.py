"""A passagem que o agente `comercial` aceita: so' referencias, nunca texto (GTM-E-13).

A Helena decide a passagem depois das travas de saude e entrega a SAIDA TIPADA
`maezo.agents.helena.habilidades.passagem.comercial.HandoffComercial`. Um agente nao importa outro (ADR-0067
§1), entao o formato e' repetido aqui e uma cerca de paridade (`tests/unit/agents/
test_esf17_passagem_comercial.py`) prova que os dois lados tem os mesmos campos.

O que a passagem NUNCA carrega: corpo da mensagem, sintoma, diagnostico, motivo de saude, telefone
ou qualquer campo livre. Campo a mais, campo a menos ou referencia fora do formato e' recusa
tipada (`ComercialInputError`), sem eco do valor recebido.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

#: Versao do formato da passagem. Mudar o formato e' mudar os dois lados e a cerca de paridade.
PASSAGEM_SCHEMA: Final[str] = "esf17-passagem-comercial.proposed.v1"

#: O unico motivo de passagem que existe: intencao de contratar/cotar sem sinal de saude.
MOTIVO_INTENCAO_CONTRATAR: Final[str] = "intencao_contratar"

#: Pseudonimo keyed do wamid de entrada (`hk1_` + HMAC-SHA256 em hex), o mesmo formato que o
#: consumidor administrativo do Lucas exige. Nunca o wamid cru.
_MESSAGE_REF: Final[re.Pattern[str]] = re.compile(r"hk1_[0-9a-f]{64}\Z")

CAMPOS_DA_PASSAGEM: Final[frozenset[str]] = frozenset({"schema_version", "para", "motivo", "message_ref"})


class ComercialInputError(ValueError):
    """Erro tecnico limitado: o texto e' um codigo fixo, nunca o valor recebido."""


@dataclass(frozen=True, slots=True)
class PassagemComercial:
    schema_version: str
    para: Literal["comercial"]
    motivo: Literal["intencao_contratar"]
    message_ref: str


def parse_passagem(payload: object) -> PassagemComercial:
    """Reconstroi a passagem a partir de um mapeamento FECHADO; qualquer desvio e' recusa."""
    if not isinstance(payload, Mapping) or frozenset(payload) != CAMPOS_DA_PASSAGEM:
        raise ComercialInputError("comercial_passagem_contract_mismatch")
    schema = payload["schema_version"]
    para = payload["para"]
    motivo = payload["motivo"]
    ref = payload["message_ref"]
    if type(schema) is not str or schema != PASSAGEM_SCHEMA or para != "comercial":
        raise ComercialInputError("comercial_passagem_contract_mismatch")
    if motivo != MOTIVO_INTENCAO_CONTRATAR:
        raise ComercialInputError("comercial_passagem_motivo_invalido")
    if type(ref) is not str or _MESSAGE_REF.fullmatch(ref) is None:
        raise ComercialInputError("comercial_passagem_message_ref_invalida")
    return PassagemComercial(
        schema_version=PASSAGEM_SCHEMA, para="comercial", motivo="intencao_contratar", message_ref=ref
    )
