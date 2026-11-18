"""Portao de campanha (E-SF-15 / C9), variante FECHADA: nenhuma campanha ligada, lista manual, sem saude.

O portao recusa por padrao. Ordem: `admitir` (registrada, parada, aprovacao humana, criterios de
segmentacao), depois finalidade do pedido igual a da campanha, para lead e prospect so' finalidade 5
ou 7 (`finalidades_do_aceite`, CD-144/CD-151) e SO' ENTAO o `OutboundGate.avaliar` existente
(finalidade ratificada, modelo aprovado, permissao de contato por finalidade, supressao).
Nao existe atalho para pular o gate: o portao so' acrescenta recusas, nunca autoriza.

A cerca da segmentacao e' a lista POSITIVA `CRITERIOS_PERMITIDOS` (decisao E12); `CRITERIOS_PROIBIDOS`
existe so' para teste e mensagem, nao e' a cerca.

GAP: a aprovacao humana referenciada (`aprovacao_humana_ref`) NAO e' verificada aqui; o processo de campanha
com aprovacao humana nao existe. Este modulo nunca e' importado fora dos testes: o portao nao e' ligado
nesta onda e `REGISTRO_DE_CAMPANHAS` e' vazio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from maezo.platform.calendario.feriados_br import UFS

from .finalidades_do_aceite import (
    FINALIDADES_DO_ACEITE,
    TITULARES_DO_ACEITE_DO_PROSPECT,
    campanha_admitida_para_prospect,
)
from .outbound_gate import Decisao, OutboundGate, PedidoDeEnvio
from .whatsapp_templates import contem_dado_de_saude

#: Lista POSITIVA e fechada de criterios de segmentacao. Qualquer outro criterio e' recusado.
CRITERIOS_PERMITIDOS: Final[frozenset[str]] = frozenset(
    {"regiao", "modalidade", "numero_de_pessoas", "produto", "etapa"}
)

#: Etapas NAO de saude (nenhum vocabulario de etapa reutilizavel existe no repositorio: o de
#: `escalation_context` e' da escalada humana). Etapas do funil comercial/contratual.
ETAPAS_PERMITIDAS: Final[frozenset[str]] = frozenset(
    {"cotacao", "proposta", "assinatura", "pagamento", "ativacao"}
)

#: So' para teste e mensagem; NAO e' a cerca (a cerca e' `CRITERIOS_PERMITIDOS`).
CRITERIOS_PROIBIDOS: Final[tuple[str, ...]] = (
    "idade",
    "faixa_etaria",
    "data_de_nascimento",
    "deficiencia",
    "cpt",
    "declaracao_saude",
    "doenca_preexistente",
    "agravo",
    "uso_do_plano",
    "sinistro",
    "cid",
    "interesse_inferido_saude",
)

#: Valores admitidos por criterio (conjuntos FECHADOS; idade e saude nao entram pelo valor).
#: - regiao: UF (`platform.calendario.feriados_br.UFS`).
#: - modalidade: vocabulario de tipo de plano ja usado no repositorio (`agents/lucas/cancelamento_pf.py`).
#: - produto: VAZIO de proposito. Nao ha catalogo publicado de produtos: nenhum produto e' admitido (GAP).
#: - etapa: `ETAPAS_PERMITIDAS`.
VALORES_PERMITIDOS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "regiao": UFS,
        "modalidade": frozenset({"individual", "familiar", "coletivo_empresarial", "coletivo_adesao"}),
        "produto": frozenset(),
        "etapa": ETAPAS_PERMITIDAS,
    }
)


class MotivoCampanha(StrEnum):
    CAMPAIGN_NOT_REGISTERED = "CAMPAIGN_NOT_REGISTERED"
    CAMPAIGN_PURPOSE_MISMATCH = "CAMPAIGN_PURPOSE_MISMATCH"
    CAMPAIGN_STOPPED = "CAMPAIGN_STOPPED"
    CAMPAIGN_APPROVAL_MISSING = "CAMPAIGN_APPROVAL_MISSING"
    SEGMENT_CRITERION_NOT_ALLOWED = "SEGMENT_CRITERION_NOT_ALLOWED"
    PHI_IN_COMMERCIAL_INPUT = "PHI_IN_COMMERCIAL_INPUT"
    CAMPAIGN_PURPOSE_NOT_ALLOWED_FOR_PROSPECT = "CAMPAIGN_PURPOSE_NOT_ALLOWED_FOR_PROSPECT"
    CAMPAIGN_SUBJECT_KIND_NOT_ALLOWED = "CAMPAIGN_SUBJECT_KIND_NOT_ALLOWED"


class CampanhaRecusadaError(PermissionError):
    """Recusa tipada do portao; a mensagem e' so' o token."""

    def __init__(self, motivo: MotivoCampanha) -> None:
        self.motivo = motivo
        super().__init__(motivo.value)


@dataclass(frozen=True, slots=True)
class Campanha:
    campanha_ref: str
    finalidade: str
    segmento: Mapping[str, object]
    aprovacao_humana_ref: str | None
    #: Kill switch. Padrao True: uma campanha nasce parada.
    parada: bool = True


#: Nenhuma campanha registrada.
REGISTRO_DE_CAMPANHAS: tuple[Campanha, ...] = ()


def _verificar_segmento(segmento: Mapping[str, object]) -> MotivoCampanha | None:
    if not segmento:
        return MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
    if any(not isinstance(k, str) or k not in CRITERIOS_PERMITIDOS for k in segmento):
        return MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
    for criterio, valor in segmento.items():
        if criterio == "numero_de_pessoas":
            if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
                return MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
        elif not isinstance(valor, str) or valor not in VALORES_PERMITIDOS[criterio]:
            return MotivoCampanha.SEGMENT_CRITERION_NOT_ALLOWED
    # Defesa em profundidade (os conjuntos fechados ja' excluem saude); depois da cerca de valores.
    if any(isinstance(v, str) and contem_dado_de_saude(v) for v in segmento.values()):
        return MotivoCampanha.PHI_IN_COMMERCIAL_INPUT
    return None


class PortaoDeCampanha:
    def admitir(self, campanha: Campanha) -> None:
        """Levanta `CampanhaRecusadaError` (registro, parada, aprovacao, criterios, nesta ordem) ou volta."""
        if campanha not in REGISTRO_DE_CAMPANHAS:
            raise CampanhaRecusadaError(MotivoCampanha.CAMPAIGN_NOT_REGISTERED)
        if campanha.parada:
            raise CampanhaRecusadaError(MotivoCampanha.CAMPAIGN_STOPPED)
        if not campanha.aprovacao_humana_ref:
            raise CampanhaRecusadaError(MotivoCampanha.CAMPAIGN_APPROVAL_MISSING)
        motivo = _verificar_segmento(campanha.segmento)
        if motivo is not None:
            raise CampanhaRecusadaError(motivo)

    async def avaliar_envio(
        self, campanha: Campanha, pedido: PedidoDeEnvio, *, gate: OutboundGate
    ) -> Decisao:
        """`admitir` PRIMEIRO; so' entao a decisao do gate (que continua mandando)."""
        self.admitir(campanha)
        if pedido.finalidade != campanha.finalidade:
            raise CampanhaRecusadaError(MotivoCampanha.CAMPAIGN_PURPOSE_MISMATCH)
        # CD-144/CD-151: campanha para lead ou prospect so' com finalidade 5 ou 7 (numero publicado);
        # sem numero, recusa.
        tipo = pedido.tipo_titular
        if type(tipo) is not str or tipo not in FINALIDADES_DO_ACEITE:
            raise CampanhaRecusadaError(MotivoCampanha.CAMPAIGN_SUBJECT_KIND_NOT_ALLOWED)
        if tipo in TITULARES_DO_ACEITE_DO_PROSPECT and not campanha_admitida_para_prospect(
            campanha.finalidade
        ):
            raise CampanhaRecusadaError(MotivoCampanha.CAMPAIGN_PURPOSE_NOT_ALLOWED_FOR_PROSPECT)
        return await gate.avaliar(pedido)
