"""E-SF-22r — boas-vindas do beneficiario (variante decidida), construida inteira com ENVIO FECHADO.

Decisoes: o gatilho e' SO' o fato de cobertura ativa vindo da fonte (`coverageState == "active"`); a vigencia
e' a data da fonte, nunca calculada. O convite ao programa de cuidado so' existe depois da cobertura ativa,
com texto unico, sem pergunta de saude, para TODOS os novos beneficiarios (nenhuma selecao por saude), e o
aceite exige consentimento ESPECIFICO, nunca na mesma sessao da declaracao de saude. O dado do programa
nunca alimenta doenca preexistente, CPT, rescisao, reajuste ou retencao. Zero dado de saude em mensagem.
Nenhum envio sai (o unico caminho e' `OutboundGate.avaliar`, fechado pelo registro publicado), nenhum evento
de medicao, idade fora de criterio. Nada aqui esta montado em runtime.

LIGAR o envio e' PR proprio, com cada condicao do registro comprovada: termo de contato assinado, encarregado
indicado, registro de admissao, oposicao executavel, numero e modelos provisionados. Autorizacao do dono nao
cumpre pre-condicao.

Erros: `BoasVindasRecusada.code` fixo; a mensagem nunca ecoa o valor recebido.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any, Final, Literal

from maezo.gateway.communications.outbound_gate import Decisao, OutboundGate, PedidoDeEnvio
from maezo.gateway.communications.whatsapp_templates import contem_dado_de_saude

TEMPLATE_BOAS_VINDAS: Final[str] = "ret.boas_vindas.v1"
FINALIDADE_BOAS_VINDAS: Final[str] = "retencao_onboarding"
TIPO_TITULAR: Final[str] = "beneficiario"
ORIGEM_FONTE_COBERTURA: Final[str] = "fonte_cobertura"


class BoasVindasRecusada(ValueError):  # noqa: N818 - nome fixado pelo epico
    """Recusa tipada: `code` fixo, sem eco do valor."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _ref_valida(v: object) -> bool:
    return type(v) is str and bool(v) and v.isascii() and v.isprintable() and not any(c.isspace() for c in v)


# --------------------------------------------------------------------------- 1. gatilho
@dataclass(frozen=True, slots=True)
class FatoDeCobertura:
    beneficiario_ref: str
    contrato_ref: str
    coverage_state: str | None
    vigencia_inicio: date | None
    origem: str


@dataclass(frozen=True, slots=True)
class IntencaoDeBoasVindas:
    beneficiario_ref: str
    contrato_ref: str
    vigencia_inicio: date
    chave_idempotencia: str
    _selo: str = field(repr=False, compare=False)


#: Chave do selo, so' deste processo: so' `avaliar_gatilho` produz intencao com selo valido.
_CHAVE_DO_SELO: Final[bytes] = secrets.token_bytes(32)


def _selar(b: str, c: str, v: date) -> str:
    return hmac.new(_CHAVE_DO_SELO, json.dumps([b, c, v.isoformat()]).encode(), hashlib.sha256).hexdigest()


def _selo_valido(intencao: object) -> bool:
    return (
        type(intencao) is IntencaoDeBoasVindas
        and type(intencao._selo) is str
        and type(intencao.vigencia_inicio) is date
        and hmac.compare_digest(
            intencao._selo, _selar(intencao.beneficiario_ref, intencao.contrato_ref, intencao.vigencia_inicio)
        )
    )


def _exigir_selo(intencao: object) -> None:
    if not _selo_valido(intencao):
        raise BoasVindasRecusada("sem_cobertura_ativa")


def chave_de(beneficiario_ref: str, contrato_ref: str) -> str:
    return hashlib.sha256(json.dumps([beneficiario_ref, contrato_ref]).encode()).hexdigest()


def avaliar_gatilho(fato: FatoDeCobertura) -> IntencaoDeBoasVindas | None:
    """So' a cobertura ativa da fonte gera intencao; `unknown`/`revoked`/ausente nao geram nada."""
    if type(fato) is not FatoDeCobertura:
        raise BoasVindasRecusada("tipo_invalido")
    if type(fato.origem) is not str or fato.origem != ORIGEM_FONTE_COBERTURA:
        raise BoasVindasRecusada("gatilho_fora_da_fonte_de_cobertura")
    if (fato.coverage_state is not None and type(fato.coverage_state) is not str) or (
        fato.vigencia_inicio is not None and type(fato.vigencia_inicio) is not date
    ):
        raise BoasVindasRecusada("tipo_invalido")
    if not _ref_valida(fato.beneficiario_ref) or not _ref_valida(fato.contrato_ref):
        raise BoasVindasRecusada("ref_invalida")
    if fato.coverage_state != "active" or fato.vigencia_inicio is None:
        return None
    return IntencaoDeBoasVindas(
        beneficiario_ref=fato.beneficiario_ref,
        contrato_ref=fato.contrato_ref,
        vigencia_inicio=fato.vigencia_inicio,
        chave_idempotencia=chave_de(fato.beneficiario_ref, fato.contrato_ref),
        _selo=_selar(fato.beneficiario_ref, fato.contrato_ref, fato.vigencia_inicio),
    )


class RegistroDeIntencoes:
    """Em memoria: exatamente uma intencao por chave; a segunda devolve a existente."""

    def __init__(self) -> None:
        self._por_chave: dict[str, IntencaoDeBoasVindas] = {}

    def registrar(self, intencao: IntencaoDeBoasVindas) -> IntencaoDeBoasVindas:
        _exigir_selo(intencao)
        return self._por_chave.setdefault(intencao.chave_idempotencia, intencao)

    def __len__(self) -> int:
        return len(self._por_chave)


_CHAVES_DO_FATO: Final[frozenset[str]] = frozenset(f.name for f in fields(FatoDeCobertura))


def fato_de_mapeamento(dados: Mapping[str, Any]) -> FatoDeCobertura:
    """Esquema FECHADO: so' as chaves do dataclass; qualquer outra (cpt, cid, dlp...) recusa."""
    if set(dados) != _CHAVES_DO_FATO:
        raise BoasVindasRecusada("esquema_fechado")
    for v in dados.values():
        if isinstance(v, str) and contem_dado_de_saude(v):
            raise BoasVindasRecusada("PHI_IN_COMMERCIAL_INPUT")
    vig = dados["vigencia_inicio"]
    cs = dados["coverage_state"]
    if (vig is not None and type(vig) is not date) or (cs is not None and type(cs) is not str):
        raise BoasVindasRecusada("tipo_invalido")
    if type(dados["origem"]) is not str:
        raise BoasVindasRecusada("tipo_invalido")
    if not _ref_valida(dados["beneficiario_ref"]) or not _ref_valida(dados["contrato_ref"]):
        raise BoasVindasRecusada("ref_invalida")
    return FatoDeCobertura(
        beneficiario_ref=dados["beneficiario_ref"],
        contrato_ref=dados["contrato_ref"],
        coverage_state=cs,
        vigencia_inicio=vig,
        origem=dados["origem"],
    )


# --------------------------------------------------------------------------- 2. envio fechado
def pedido_de_boas_vindas(intencao: IntencaoDeBoasVindas) -> PedidoDeEnvio:
    """Pedido do despachante unico; sem variaveis (o modelo nao tem corpo); sem permissao resolvida."""
    _exigir_selo(intencao)
    return PedidoDeEnvio(
        template_id=TEMPLATE_BOAS_VINDAS,
        finalidade=FINALIDADE_BOAS_VINDAS,
        tipo_titular=TIPO_TITULAR,
        titular_ref=intencao.beneficiario_ref,
        canal_ref="canal-nao-resolvido",
        variaveis=(),
        permissao=None,
        chave_idempotencia=intencao.chave_idempotencia,
    )


async def enviar_boas_vindas(intencao: IntencaoDeBoasVindas, *, gate: OutboundGate) -> Decisao:
    """So' delega ao `OutboundGate.avaliar`; nao ha' outro caminho de envio."""
    return await gate.avaliar(pedido_de_boas_vindas(intencao))


# --------------------------------------------------------------------------- 3. documentos
EstadoDoDocumento = Literal["pendente", "disponivel", "entregue"]
TipoDePlano = Literal["individual", "familiar", "coletivo_empresarial", "coletivo_adesao"]

DOCUMENTOS_DO_INDIVIDUAL: Final[tuple[str, ...]] = ("contrato", "condicoes_gerais", "material_explicativo")


def marcar_entregue(doc: EstadoDoDocumento, *, recibo_ref: str | None) -> EstadoDoDocumento:
    """`entregue` so' com recibo valido; sem recibo nunca se declara entregue."""
    if doc != "disponivel":
        raise BoasVindasRecusada("documento_nao_disponivel")
    if not _ref_valida(recibo_ref):
        raise BoasVindasRecusada("entrega_sem_recibo")
    return "entregue"


def documentos_exigidos(tipo_plano: TipoDePlano) -> tuple[str, ...]:
    if tipo_plano in ("individual", "familiar"):
        return DOCUMENTOS_DO_INDIVIDUAL
    if tipo_plano in ("coletivo_empresarial", "coletivo_adesao"):
        # GAP: os documentos exigidos dos planos coletivos nao estao decididos; so' o material explicativo.
        return ("material_explicativo",)
    raise BoasVindasRecusada("tipo_plano_invalido")


# --------------------------------------------------------------------------- 4. convite
#: PROVISORIO: redacao com juridico e encarregado. Sem pergunta, sem termo de saude, sem nome, sem prazo.
TEXTO_CONVITE: Final[str] = (
    "Seu plano está ativo. A operadora oferece programas de cuidado abertos a todos os beneficiários. "
    "Se quiser saber mais, responda SABER MAIS."
)


def convite_ao_programa(intencao: IntencaoDeBoasVindas) -> str:
    """Existe so' a partir de uma intencao (cobertura ativa); sem parametro de selecao; texto unico."""
    _exigir_selo(intencao)
    return TEXTO_CONVITE


def processar_aceite_do_convite(
    *, consentimento_especifico_ref: str | None, sessao_declaracao_saude: bool
) -> Literal["sem_consentimento", "ponte_nao_construida"]:
    if sessao_declaracao_saude or not _ref_valida(consentimento_especifico_ref):
        return "sem_consentimento"  # nenhum dado e' processado
    # GAP: a ponte para o processo de programas de cuidado e' caminho de outra janela; nada de saude aqui.
    return "ponte_nao_construida"


# --------------------------------------------------------------------------- 5. cerca
USOS_VEDADOS_DO_DADO_DO_PROGRAMA: Final[frozenset[str]] = frozenset(
    {"doenca_preexistente", "cpt", "rescisao", "reajuste", "retencao"}
)
