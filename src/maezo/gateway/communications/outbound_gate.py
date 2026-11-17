"""OutboundGate — a fonte UNICA das verificacoes de todo envio PROATIVO (E-SF-13 / GTM-E-41).

Proativo = mensagem que a operadora inicia (template fora da janela de 24 h, lembrete, aviso, recuperacao,
campanha). A RESPOSTA 1:1 a uma mensagem recebida (`platform/webhooks/whatsapp/dispatch.py`,
`_ScopedWhatsAppSender` -> `WhatsAppServer.send_message`) NAO passa por aqui: ela continua no gate de
efeito do principal (`gateway/seams/whatsapp.py`), como antes.

FECHADO POR CONSTRUCAO. As verificacoes leem o registro `spec/communications/whatsapp-templates.yaml`
(`whatsapp_templates.py`). O registro publicado tem zero finalidades ratificadas, entao 100% dos pedidos
sao recusados com `PURPOSE_NOT_RATIFIED` e nenhum transporte e' chamado. Na imagem de producao o arquivo
nem e' empacotado: o registro carregado e' o vazio, que recusa igual. Nao ha' flag, variavel de ambiente
ou parametro que pule uma verificacao; abrir um envio exige editar o registro (ratificacao registrada) E
as demais condicoes abaixo.

ORDEM das verificacoes (a primeira que falha decide; o motivo e' um token, nunca texto livre):

  1. `PURPOSE_NOT_RATIFIED`        finalidade ausente ou nao ratificada no registro.
  2. `TEMPLATE_NOT_REGISTERED`     template fora do registro.
     `TEMPLATE_PURPOSE_MISMATCH`   finalidade ou tipo de titular do pedido != os do template.
  3. `TEMPLATE_NOT_APPROVED`       `meta_status != approved` ou sem `content_review_ref`.
  4. `VARIABLES_INVALID`           numero de valores != variaveis do template.
     `PHI_IN_COMMERCIAL_INPUT`     corpo, classe ou VALOR com dado de saude (GT-2). O vocabulario e' o
                                   mesmo de `vendor_notice.py` (recusa tipada antes de qualquer egress).
     `VARIABLE_VALUE_INVALID`      (ainda no passo 4, depois do detector) o VALOR de uma variavel nao passa
                                   no validador da SUA classe fechada (`VALIDADORES_DE_CLASSE`; classe sem
                                   validador nunca valida). Esta e' a garantia; o detector e' a defesa extra.
  5. `VARIABLE_CLASS_PENDING_DPO`  classe pessoal com a taxonomia de classes ainda nao ratificada (DPO).
  6. `SUBJECT_KIND_CLOSED`         tipo de titular sem oposicao executavel (beneficiario: GTM-04/D4 aberto;
                                   ADDENDUM-01 §7 (a)).
  7. `recontact_permission_absent` / `recontact_permission_unqualified` — permissao de contato (GT-3,
     re-especificado no ADDENDUM-01): so' um texto PUBLICADO no registro, versionado, com sha, finalidades
     NOMEADAS (nao genericas) que incluam a do pedido, prova e ultimo evento `granted`. O consentimento de
     atendimento (`atendimento_whatsapp` / `wa-consent-v2`, "nesta conversa") NUNCA vale.
  8. `SUPPRESSION_SOURCE_UNAVAILABLE` / `SUPPRESSED` — fonte de supressao (SP-OP-SUPP-001) ausente ou fora
     do ar => nao envia (REG-20, desconhecido nunca vira zero); titular ou canal suprimido => nao envia.

So' depois disso o gate emite uma `AutorizacaoDeEnvio`. `enviar()` acrescenta, para quem usa o gate de
ponta a ponta: `RECIPIENT_UNRESOLVED` (custodia sem contato ou vencida) e `DUPLICATE` (reserva idempotente:
a mesma chave nunca gera um segundo POST). O `WhatsAppServer.send_template` recusa sem autorizacao valida.

Este fecho por classe e' pre-condicao de qualquer PR que ligue o envio real de recuperacao, retencao ou
boas-vindas (D20, D21, D22).

LOG: so' o motivo e contagens (`contagens`). Nunca telefone, nome, referencia, variavel ou texto. Nada
aqui emite evento de medicao (A9).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

import structlog

from .whatsapp_templates import (
    FINALIDADES_GENERICAS,
    PREFIXO_VERSAO_CONSENTIMENTO_ATENDIMENTO,
    SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO,
    VALIDADORES_DE_CLASSE,
    RegistroDeTemplates,
    contem_dado_de_saude,
    registro_de_producao,
    valor_valido_para_classe,
)

logger = structlog.get_logger(__name__)


class Motivo(StrEnum):
    """Recusas tipadas do gate. Os dois minusculos sao os tokens do GT-3 (ADD-A9-CONSENT §6), verbatim."""

    PURPOSE_NOT_RATIFIED = "PURPOSE_NOT_RATIFIED"
    TEMPLATE_NOT_REGISTERED = "TEMPLATE_NOT_REGISTERED"
    TEMPLATE_PURPOSE_MISMATCH = "TEMPLATE_PURPOSE_MISMATCH"
    TEMPLATE_NOT_APPROVED = "TEMPLATE_NOT_APPROVED"
    VARIABLES_INVALID = "VARIABLES_INVALID"
    PHI_IN_COMMERCIAL_INPUT = "PHI_IN_COMMERCIAL_INPUT"
    VARIABLE_CLASS_PENDING_DPO = "VARIABLE_CLASS_PENDING_DPO"
    VARIABLE_VALUE_INVALID = "VARIABLE_VALUE_INVALID"
    SUBJECT_KIND_CLOSED = "SUBJECT_KIND_CLOSED"
    RECONTACT_PERMISSION_ABSENT = "recontact_permission_absent"
    RECONTACT_PERMISSION_UNQUALIFIED = "recontact_permission_unqualified"
    SUPPRESSION_SOURCE_UNAVAILABLE = "SUPPRESSION_SOURCE_UNAVAILABLE"
    SUPPRESSED = "SUPPRESSED"
    RECIPIENT_UNRESOLVED = "RECIPIENT_UNRESOLVED"
    DUPLICATE = "DUPLICATE"
    #: O transporte recebeu um envio proativo sem autorizacao emitida por um gate.
    OUTBOUND_NOT_AUTHORIZED = "OUTBOUND_NOT_AUTHORIZED"


#: O UNICO escopo de permissao que qualifica contato proativo (allowlist, nao lista de alheios). Qualquer
#: outro escopo e' alheio e recusa — em especial `atendimento_whatsapp`, o escopo do consentimento
#: `wa-consent-v2` (`gateway/amh_interop.py::CONSENT_SCOPE`), e o de medicao.
ESCOPO_CONTATO_PROATIVO: Final[str] = "contato_proativo"
#: Origens que NAO sao um aceite: permissao inferida de `journey.save`/`prospect.save` (GT-3 (c)) ou o
#: consentimento de atendimento.
ORIGENS_NAO_ACEITE: Final[frozenset[str]] = frozenset(
    {"inferida_journey_save", "inferida_prospect_save", "consentimento_atendimento"}
)
ORIGEM_ACEITE_CONTATO: Final[str] = "aceite_contato"


@dataclass(frozen=True, slots=True)
class PermissaoDeContato:
    """A permissao RESOLVIDA do titular (nunca o telefone). Molde: `consent-record` (ADD-A9-CONSENT §4(b))."""

    origem: str
    escopo: str
    texto_versao: str
    texto_sha256: str
    canal: str = "whatsapp"
    ultimo_evento: str = "granted"
    prova_ref: str | None = field(default=None, repr=False)


def verificar_permissao(
    permissao: PermissaoDeContato | None, *, finalidade: str, registro: RegistroDeTemplates
) -> Motivo | None:
    """GT-3. `None` = permissao qualificada para ESTA finalidade; senao o motivo da recusa."""
    if permissao is None or permissao.ultimo_evento != "granted":
        return Motivo.RECONTACT_PERMISSION_ABSENT
    if (
        permissao.escopo != ESCOPO_CONTATO_PROATIVO
        or permissao.origem in ORIGENS_NAO_ACEITE
        or permissao.origem != ORIGEM_ACEITE_CONTATO
        or permissao.texto_versao.startswith(PREFIXO_VERSAO_CONSENTIMENTO_ATENDIMENTO)
        or permissao.texto_sha256 == SHA256_TEXTO_CONSENTIMENTO_ATENDIMENTO
        or permissao.canal != "whatsapp"
        or not permissao.prova_ref
    ):
        return Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    publicado = next(
        (
            t
            for t in registro.textos_de_permissao
            if t.versao == permissao.texto_versao and t.sha256 == permissao.texto_sha256
        ),
        None,
    )
    if publicado is None:
        return Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    nomeadas = {f for f in publicado.finalidades if f not in FINALIDADES_GENERICAS}
    if finalidade not in nomeadas:
        return Motivo.RECONTACT_PERMISSION_UNQUALIFIED
    return None


class FonteDeSupressao(Protocol):
    """As chaves vigentes de SP-OP-SUPP-001 como `(subject_ref, contact_channel)`. Levanta se nao responde."""

    async def vigentes(self) -> frozenset[tuple[str, str]]: ...


class ReservaDeSaida(Protocol):
    """Reserva idempotente ANTES do POST (liberada se o POST falhar)."""

    async def reservar(self, chave: str) -> bool: ...

    async def liberar(self, chave: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PedidoDeEnvio:
    """Um envio proativo pedido. So' referencias opacas; os valores das variaveis nunca vao a log."""

    template_id: str
    finalidade: str
    tipo_titular: str
    titular_ref: str = field(repr=False)
    canal_ref: str = field(repr=False)
    variaveis: tuple[str, ...] = field(repr=False)
    permissao: PermissaoDeContato | None = field(repr=False)
    chave_idempotencia: str = field(repr=False)


#: Chave do selo, so' deste processo. O selo e' um HMAC dos campos: uma autorizacao valida alterada
#: (`dataclasses.replace` com outro template ou outras variaveis) deixa de ser valida.
_CHAVE_DO_SELO: Final[bytes] = secrets.token_bytes(32)


def _selar(
    template_name: str, language: str, parametros: tuple[str, ...], finalidade: str, chave: str
) -> str:
    corpo = json.dumps([template_name, language, list(parametros), finalidade, chave], ensure_ascii=False)
    return hmac.new(_CHAVE_DO_SELO, corpo.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class AutorizacaoDeEnvio:
    """O que o gate emite quando TODAS as verificacoes passam. So' o gate tem o selo."""

    template_name: str
    language: str
    parametros: tuple[str, ...] = field(repr=False)
    finalidade: str
    chave_idempotencia: str = field(repr=False)
    _selo: str = field(repr=False, compare=False)


def autorizacao_valida(autorizacao: object) -> bool:
    """`True` so' para uma autorizacao emitida por um `OutboundGate` (o transporte confere)."""
    if not isinstance(autorizacao, AutorizacaoDeEnvio) or not isinstance(autorizacao._selo, str):
        return False
    esperado = _selar(
        autorizacao.template_name,
        autorizacao.language,
        tuple(autorizacao.parametros),
        autorizacao.finalidade,
        autorizacao.chave_idempotencia,
    )
    return hmac.compare_digest(autorizacao._selo, esperado)


class EnvioProativoRecusadoError(PermissionError):
    """Recusa tipada; a mensagem e' so' o token do motivo."""

    def __init__(self, motivo: Motivo) -> None:
        self.motivo = motivo
        super().__init__(motivo.value)


@dataclass(frozen=True, slots=True)
class Decisao:
    motivo: Motivo | None
    autorizacao: AutorizacaoDeEnvio | None = None

    @property
    def permitido(self) -> bool:
        return self.motivo is None and self.autorizacao is not None


@dataclass(frozen=True, slots=True)
class ResultadoDoGate:
    """Resultado de `enviar`: `postado` so' quando o transporte foi chamado e nao falhou."""

    motivo: Motivo | None
    postado: bool
    resposta: dict[str, object] | None = field(default=None, repr=False)


class OutboundGate:
    """O gate. `registro` vem de `registro_de_producao()` em producao; testes passam um sintetico."""

    def __init__(self, registro: RegistroDeTemplates, *, supressao: FonteDeSupressao | None) -> None:
        self._registro = registro
        self._supressao = supressao
        self.contagens: Counter[str] = Counter()

    @classmethod
    def de_producao(cls, *, supressao: FonteDeSupressao | None) -> OutboundGate:
        return cls(registro_de_producao(), supressao=supressao)

    def _recusar(self, motivo: Motivo) -> Decisao:
        self.contagens[motivo.value] += 1
        logger.info("outbound_gate_recusa", motivo=motivo.value)
        return Decisao(motivo=motivo)

    def _verificar_template(self, pedido: PedidoDeEnvio) -> Motivo | None:
        r = self._registro
        tpl = r.templates.get(pedido.template_id)
        if tpl is None:
            return Motivo.TEMPLATE_NOT_REGISTERED
        if tpl.purpose_ref != pedido.finalidade or tpl.subject_kind != pedido.tipo_titular:
            return Motivo.TEMPLATE_PURPOSE_MISMATCH
        if not tpl.aprovado:
            return Motivo.TEMPLATE_NOT_APPROVED
        if len(pedido.variaveis) != len(tpl.variables):
            return Motivo.VARIABLES_INVALID
        if (
            (tpl.body is not None and contem_dado_de_saude(tpl.body))
            or any(contem_dado_de_saude(v) for v in pedido.variaveis)
            or any(v.classe not in r.classes for v in tpl.variables)
        ):
            return Motivo.PHI_IN_COMMERCIAL_INPUT
        # A GARANTIA: cada valor passa no validador da SUA classe fechada (a lista de termos acima e' so'
        # defesa em profundidade). Classe sem validador nunca valida.
        if any(
            not valor_valido_para_classe(v.classe, valor, VALIDADORES_DE_CLASSE)
            for v, valor in zip(tpl.variables, pedido.variaveis, strict=True)
        ):
            return Motivo.VARIABLE_VALUE_INVALID
        if not r.taxonomia_ratificada and any(
            r.classes[v.classe].tipo != "nao_pessoal" for v in tpl.variables
        ):
            return Motivo.VARIABLE_CLASS_PENDING_DPO
        return None

    def finalidade_aberta(self, finalidade: str, *, tipo_titular: str) -> Motivo | None:
        """Pre-verificacao SEM dado (passos 1 e 6): deixa um job parar antes de ler qualquer fonte.

        `None` nao autoriza nada — so' diz que vale a pena montar pedidos, que passam por `avaliar`.
        """
        if not self._registro.finalidade_ratificada(finalidade):
            return self._recusar(Motivo.PURPOSE_NOT_RATIFIED).motivo
        if tipo_titular not in self._registro.oposicao_executavel_para:
            return self._recusar(Motivo.SUBJECT_KIND_CLOSED).motivo
        return None

    async def avaliar(self, pedido: PedidoDeEnvio) -> Decisao:
        """Todas as verificacoes, na ordem do docstring do modulo. Nenhuma chamada ao transporte."""
        r = self._registro
        if not r.finalidade_ratificada(pedido.finalidade):
            return self._recusar(Motivo.PURPOSE_NOT_RATIFIED)
        motivo = self._verificar_template(pedido)
        if motivo is not None:
            return self._recusar(motivo)
        if pedido.tipo_titular not in r.oposicao_executavel_para:
            return self._recusar(Motivo.SUBJECT_KIND_CLOSED)
        motivo = verificar_permissao(pedido.permissao, finalidade=pedido.finalidade, registro=r)
        if motivo is not None:
            return self._recusar(motivo)
        if self._supressao is None:
            return self._recusar(Motivo.SUPPRESSION_SOURCE_UNAVAILABLE)
        try:
            vigentes = await self._supressao.vigentes()
        except Exception:  # fonte fora do ar: desconhecido nunca vira "sem supressao"
            return self._recusar(Motivo.SUPPRESSION_SOURCE_UNAVAILABLE)
        if any(ref == pedido.titular_ref or canal == pedido.canal_ref for ref, canal in vigentes):
            return self._recusar(Motivo.SUPPRESSED)
        tpl = r.templates[pedido.template_id]
        # PONTO UNICO de autorizacao do envio. Pre-condicao de qualquer PR que ligue o envio real de
        # recuperacao, retencao ou boas-vindas (D20, D21, D22): o fecho por classe (validador de VALOR
        # em `_verificar_template`) tem de valer para as classes do template; a lista de termos nao basta.
        self.contagens["autorizado"] += 1
        return Decisao(
            motivo=None,
            autorizacao=AutorizacaoDeEnvio(
                template_name=tpl.name,
                language=tpl.language,
                parametros=pedido.variaveis,
                finalidade=pedido.finalidade,
                chave_idempotencia=pedido.chave_idempotencia,
                _selo=_selar(
                    tpl.name, tpl.language, pedido.variaveis, pedido.finalidade, pedido.chave_idempotencia
                ),
            ),
        )

    async def enviar(
        self,
        pedido: PedidoDeEnvio,
        *,
        destinatario: Callable[[], Awaitable[str | None]],
        reserva: ReservaDeSaida,
        transporte: Callable[[str, AutorizacaoDeEnvio], Awaitable[dict[str, object]]],
    ) -> ResultadoDoGate:
        """Ponta a ponta: avaliar -> contato (custodia) -> reserva -> UM POST. Recusa = zero POST."""
        decisao = await self.avaliar(pedido)
        if not decisao.permitido or decisao.autorizacao is None:
            return ResultadoDoGate(motivo=decisao.motivo, postado=False)
        telefone = await destinatario()
        if not telefone:
            self._recusar(Motivo.RECIPIENT_UNRESOLVED)
            return ResultadoDoGate(motivo=Motivo.RECIPIENT_UNRESOLVED, postado=False)
        if not await reserva.reservar(pedido.chave_idempotencia):
            self._recusar(Motivo.DUPLICATE)
            return ResultadoDoGate(motivo=Motivo.DUPLICATE, postado=False)
        try:
            resposta = await transporte(telefone, decisao.autorizacao)
        except Exception:
            await reserva.liberar(pedido.chave_idempotencia)
            self.contagens["falha_transporte"] += 1
            raise
        self.contagens["postado"] += 1
        return ResultadoDoGate(motivo=None, postado=True, resposta=resposta)


def exigir_autorizacao(autorizacao: object, *, parametros: Sequence[str] | None = None) -> AutorizacaoDeEnvio:
    """Para o transporte: devolve a autorizacao valida ou levanta `EnvioProativoRecusadoError`."""
    if not isinstance(autorizacao, AutorizacaoDeEnvio) or not autorizacao_valida(autorizacao):
        raise EnvioProativoRecusadoError(Motivo.OUTBOUND_NOT_AUTHORIZED)
    if parametros is not None and tuple(parametros) != autorizacao.parametros:
        raise EnvioProativoRecusadoError(Motivo.VARIABLES_INVALID)
    return autorizacao
