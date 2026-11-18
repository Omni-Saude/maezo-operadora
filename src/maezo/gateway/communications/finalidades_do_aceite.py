"""Finalidades do aceite unico de contato, por tipo de titular, e o que o portao de CAMPANHA admite (CD-144).

Duas coisas diferentes, de proposito separadas:

  * `FINALIDADES_DO_ACEITE`: o que um aceite PODE cobrir. Lead e prospect: {1, 5, 7} (andamento da
    contratacao, ofertas, retomada de contratacao nao concluida). Beneficiario: {1..6}. A 7 nunca entra
    no aceite do beneficiario. Esta tabela NAO e' licenca de campanha. Tipo fora dos tres recusa.
  * `FINALIDADES_DE_CAMPANHA_PARA_PROSPECT`: o que o portao de campanha admite para lead e prospect:
    so' 5 e 7. A 1 e' transacional e fica com o gate de cada mensagem, nunca com campanha.

Regras do aceite (padrao conservador; nunca amplia o que a pessoa autorizou):
  * subconjunto NAO VAZIO da tabela do tipo de titular; fora dela recusa;
  * finalidade ausente = nao autorizada; nada e' herdado de outra finalidade nem de aceite anterior;
  * a finalidade 5 exige marca propria e explicita (`marca_finalidade_5 is True`), sem valor padrao.

O numero de cada finalidade publicada (`purpose_ref` -> numero) e' um mapa VAZIO: nenhuma finalidade publicada
traz numero hoje. Preencher e' ato do registro de finalidades, nao deste modulo. Com o mapa vazio, toda
campanha para prospect e' recusada.

Nada liga envio: o `OutboundGate` continua recusando por conta propria. Nenhum texto normativo aqui.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = [
    "FINALIDADES_DE_CAMPANHA_PARA_PROSPECT",
    "FINALIDADES_DO_ACEITE",
    "FINALIDADE_OFERTAS",
    "NUMERO_DA_FINALIDADE",
    "TITULARES_DO_ACEITE_DO_PROSPECT",
    "AceiteDeFinalidades",
    "AceiteRecusado",
    "aceite_autoriza",
    "campanha_admitida_para_prospect",
    "validar_aceite",
]

#: Finalidade 5 (ofertas): so' com marca propria e explicita.
FINALIDADE_OFERTAS: Final[int] = 5

#: O que um aceite pode cobrir, por tipo de titular. NAO e' licenca de campanha.
FINALIDADES_DO_ACEITE: Final[Mapping[str, frozenset[int]]] = MappingProxyType(
    {
        "lead": frozenset({1, 5, 7}),
        "prospect": frozenset({1, 5, 7}),
        "beneficiario": frozenset({1, 2, 3, 4, 5, 6}),
    }
)

#: O que o portao de CAMPANHA admite para prospect. A 1 (andamento da contratacao) fica fora: e' transacional.
FINALIDADES_DE_CAMPANHA_PARA_PROSPECT: Final[frozenset[int]] = frozenset({5, 7})

#: Tipos de titular sujeitos a regra de campanha do prospect (CD-151: lead e prospect).
TITULARES_DO_ACEITE_DO_PROSPECT: Final[frozenset[str]] = frozenset({"lead", "prospect"})

#: `purpose_ref` publicado -> numero da finalidade. VAZIO: nenhum numero publicado; nao preencher aqui.
NUMERO_DA_FINALIDADE: Final[Mapping[str, int]] = MappingProxyType({})


class AceiteRecusado(ValueError):  # noqa: N818
    """Recusa com codigo fixo; nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AceiteDeFinalidades:
    """As finalidades que UMA pessoa aceitou, como a fonte as registra."""

    tipo_titular: str
    finalidades: frozenset[int]
    #: Marca propria da finalidade 5. Sem padrao verdadeiro: ausente/None/False com a 5 = recusa.
    marca_finalidade_5: bool | None = None


def validar_aceite(aceite: object) -> AceiteDeFinalidades:
    """Devolve o aceite se ele obedece as regras; senao levanta `AceiteRecusado`."""
    if type(aceite) is not AceiteDeFinalidades:
        raise AceiteRecusado("aceite_invalido")
    if type(aceite.tipo_titular) is not str or aceite.tipo_titular not in FINALIDADES_DO_ACEITE:
        raise AceiteRecusado("aceite_titular_desconhecido")
    finalidades = aceite.finalidades
    if type(finalidades) is not frozenset or any(type(f) is not int for f in finalidades):
        raise AceiteRecusado("aceite_invalido")
    if not finalidades:
        raise AceiteRecusado("aceite_vazio")
    if not finalidades <= FINALIDADES_DO_ACEITE[aceite.tipo_titular]:
        raise AceiteRecusado("aceite_finalidade_fora_da_tabela")
    marca = aceite.marca_finalidade_5
    if marca is not None and type(marca) is not bool:
        raise AceiteRecusado("aceite_invalido")
    if FINALIDADE_OFERTAS in finalidades and marca is not True:
        raise AceiteRecusado("aceite_finalidade_5_sem_marca")
    if FINALIDADE_OFERTAS not in finalidades and marca is True:
        raise AceiteRecusado("aceite_marca_sem_finalidade_5")
    return aceite


def aceite_autoriza(aceite: object, finalidade: object) -> bool:
    """So' a finalidade listada no aceite valido. Aceite invalido, ausente ou outra finalidade = False."""
    if type(finalidade) is not int:
        return False
    try:
        valido = validar_aceite(aceite)
    except AceiteRecusado:
        return False
    return finalidade in valido.finalidades


def campanha_admitida_para_prospect(purpose_ref: object) -> bool:
    """A finalidade da campanha (texto publicado) tem numero e esse numero e' 5 ou 7. Mapa vazio = False."""
    if type(purpose_ref) is not str:
        return False
    numero = NUMERO_DA_FINALIDADE.get(purpose_ref)
    return numero in FINALIDADES_DE_CAMPANHA_PARA_PROSPECT
