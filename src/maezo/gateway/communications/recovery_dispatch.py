"""Guarda de despacho da recuperacao de abandono (E-SF-16 / TB-E-18-a, TB-E-09-d), variante FECHADA.

Fica ANTES do `OutboundGate` e so' acrescenta recusas; o gate continua decidindo depois (modelo, permissao
publicada, supressao, idempotencia). Nada envia: a chave nasce desligada, o leitor recusa liga-la e a
cadencia e' um parametro proposto, sem base, igual a zero toques.

Ordem (a primeira recusa encerra):
  0. referencia do titular em forma canonica (sem ela nao ha' como consultar a oposicao);
  1. oposicao registrada: consulta a fonte de supressao ANTES DE TUDO; fonte que nao responde ou devolve
     algo fora do formato recusa; grafia diferente na fonte conta como a mesma pessoa;
  2. tipo de mensagem: so' "lembrete". Pedido de documentacao nunca e' recuperacao (sem efeito no relogio);
  3. o relogio-sombra canonico tem de ser o do proprio titular; elegibilidade re-checada no envio
     (`recovery_eligibility`);
  4. permissao: aceite valido do proprio titular, que liste a finalidade 7 (retomada); nada herdado;
  5. cadencia: toques ja' enviados >= toques permitidos (zero) recusa.

Mora ao lado do portao de saida (`gateway/communications/`), nao no caminho de entrada do webhook. Nao importa
logger, nao loga nada e nao recebe wamid nem telefone: so' referencias opacas e o estado do relogio. Nao e'
importado por `platform/webhooks/whatsapp/dispatch.py`, pelo despachante nem por raiz de composicao (teste).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from maezo.domain.rn413_contracting_clock import exigir_relogio_canonico

from ..commercial_t0_ledger import digest_do_proponente
from .finalidades_do_aceite import aceite_autoriza, validar_aceite
from .outbound_gate import FonteDeSupressao
from .recovery_eligibility import Elegivel, avaliar_elegibilidade

__all__ = [
    "CANAL",
    "FINALIDADE_RETOMADA",
    "RECUPERACAO_LIGADA",
    "TIPOS_DE_MENSAGEM",
    "TOQUES_PERMITIDOS_PROPOSTO_SEM_BASE",
    "MotivoDespachoRecuperacao",
    "PedidoDeRecuperacao",
    "RecuperacaoDespachoRecusado",
    "despachar_recuperacao",
    "guardar_envio_de_recuperacao",
    "ler_chave_da_recuperacao",
]

CANAL: Final[str] = "whatsapp"
#: Finalidade 7 do aceite unico: retomada de contratacao nao concluida.
FINALIDADE_RETOMADA: Final[int] = 7
#: Classe fechada do que uma mensagem de recuperacao pode ser.
TIPOS_DE_MENSAGEM: Final[frozenset[str]] = frozenset({"lembrete"})
#: Cadencia: parametro PROPOSTO, SEM BASE (nao ha' decisao de cadencia). Zero toques: nada sai.
TOQUES_PERMITIDOS_PROPOSTO_SEM_BASE: Final[int] = 0
#: Referencia opaca do titular em forma canonica (minusculas, digitos, "_" e "-"). Outra forma recusa.
_REF_CANONICA: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}")
#: Chave do envio de recuperacao. Desligada.
RECUPERACAO_LIGADA: Final[bool] = False


class MotivoDespachoRecuperacao(StrEnum):
    RECOVERY_SWITCH_OFF = "RECOVERY_SWITCH_OFF"
    RECOVERY_SUBJECT_REF_INVALID = "RECOVERY_SUBJECT_REF_INVALID"
    RECOVERY_CLOCK_NOT_OF_SUBJECT = "RECOVERY_CLOCK_NOT_OF_SUBJECT"
    RECOVERY_OPPOSITION_REGISTERED = "RECOVERY_OPPOSITION_REGISTERED"
    RECOVERY_SUPPRESSION_UNAVAILABLE = "RECOVERY_SUPPRESSION_UNAVAILABLE"
    DOCUMENT_REQUEST_FORBIDDEN = "DOCUMENT_REQUEST_FORBIDDEN"
    RECOVERY_MESSAGE_KIND_NOT_ALLOWED = "RECOVERY_MESSAGE_KIND_NOT_ALLOWED"
    RECONTACT_PERMISSION_UNQUALIFIED = "recontact_permission_unqualified"
    RECOVERY_CADENCE_EXHAUSTED = "RECOVERY_CADENCE_EXHAUSTED"


class RecuperacaoDespachoRecusado(PermissionError):  # noqa: N818
    """Recusa tipada; a mensagem e' so' o codigo, nunca o valor recebido."""

    def __init__(self, motivo: MotivoDespachoRecuperacao) -> None:
        self.motivo = motivo
        super().__init__(motivo.value)


@dataclass(frozen=True, slots=True)
class PedidoDeRecuperacao:
    """Um lembrete candidato. So' referencias; nenhum telefone, nome, idade, plano, valor ou dado de saude."""

    tipo_titular: str
    titular_ref: str
    etapa: str
    tipo_mensagem: str
    aceite: object
    aceite_registrado: bool
    toques_ja_enviados: int


def ler_chave_da_recuperacao(valor: object) -> bool:
    """O leitor da chave. Qualquer valor diferente de "desligado" recusa: ligar nao e' desta variante."""
    if valor is None or valor is False or valor == "desligado":
        return False
    raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_SWITCH_OFF)


def _normalizar(valor: str) -> str:
    """Compatibilidade Unicode, sem caractere invisivel, de controle ou espaco, em caixa unica."""
    sem_invisiveis = "".join(
        ch for ch in unicodedata.normalize("NFKC", valor) if unicodedata.category(ch)[0] not in {"C", "Z"}
    )
    return unicodedata.normalize("NFKC", sem_invisiveis.casefold())


def _parece_documentacao(tipo: str) -> bool:
    baixo = tipo.lower()
    return "document" in baixo or "pericia" in baixo or "entrevista" in baixo


async def guardar_envio_de_recuperacao(
    pedido: PedidoDeRecuperacao, *, relogio: object, supressao: FonteDeSupressao
) -> Elegivel:
    """Aplica a ordem do modulo. Devolve `Elegivel` (nao e' autorizacao) ou levanta a recusa tipada."""
    ref = pedido.titular_ref
    if type(ref) is not str or not _REF_CANONICA.fullmatch(ref):
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_SUBJECT_REF_INVALID)
    try:
        vigentes = await supressao.vigentes()
    except Exception as exc:  # noqa: BLE001 - fonte que nao responde nunca vira "sem oposicao"
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_SUPPRESSION_UNAVAILABLE) from exc
    if not isinstance(vigentes, (frozenset, set)) or not all(
        type(v) is tuple and len(v) == 2 and all(type(x) is str for x in v) for v in vigentes
    ):
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_SUPPRESSION_UNAVAILABLE)
    # Grafia diferente na fonte (maiusculas, espacos, invisivel, compatibilidade Unicode) ainda conta
    # como a mesma pessoa. Referencia da fonte que nem normalizada fica em forma canonica torna a fonte
    # ilegivel: na duvida, recusa (nunca "sem oposicao").
    normalizadas = {(_normalizar(r), _normalizar(c)) for r, c in vigentes}
    if not all(_REF_CANONICA.fullmatch(r) for r, _ in normalizadas):
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_SUPPRESSION_UNAVAILABLE)
    if (ref, CANAL) in normalizadas:
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_OPPOSITION_REGISTERED)
    tipo = pedido.tipo_mensagem
    if type(tipo) is not str or tipo not in TIPOS_DE_MENSAGEM:
        if type(tipo) is str and _parece_documentacao(tipo):
            raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.DOCUMENT_REQUEST_FORBIDDEN)
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_MESSAGE_KIND_NOT_ALLOWED)
    # O relogio tem de ser o DESTE titular: relogio de outra pessoa nunca libera.
    if exigir_relogio_canonico(relogio).proponente_digest != digest_do_proponente(ref):
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_CLOCK_NOT_OF_SUBJECT)
    elegivel = avaliar_elegibilidade(
        tipo_titular=pedido.tipo_titular,
        etapa=pedido.etapa,
        aceite_registrado=pedido.aceite_registrado,
        relogio=relogio,
    )
    if not aceite_autoriza(pedido.aceite, FINALIDADE_RETOMADA) or (
        validar_aceite(pedido.aceite).tipo_titular != pedido.tipo_titular
    ):
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECONTACT_PERMISSION_UNQUALIFIED)
    toques = pedido.toques_ja_enviados
    if type(toques) is not int or toques < 0 or toques >= TOQUES_PERMITIDOS_PROPOSTO_SEM_BASE:
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_CADENCE_EXHAUSTED)
    return elegivel


async def despachar_recuperacao(
    pedido: PedidoDeRecuperacao, *, relogio: object, supressao: FonteDeSupressao
) -> Elegivel:
    """Ponto de entrada do despacho. Com a chave desligada, recusa antes de qualquer consulta."""
    if not RECUPERACAO_LIGADA:
        raise RecuperacaoDespachoRecusado(MotivoDespachoRecuperacao.RECOVERY_SWITCH_OFF)
    return await guardar_envio_de_recuperacao(pedido, relogio=relogio, supressao=supressao)
