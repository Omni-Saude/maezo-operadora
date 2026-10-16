"""Lucas, catalogo v4 (DL-0114): detalhamento da mensalidade do pagador e 2a via com codigo de barras.

Atras da chave `MAEZO_LUCAS_V4` e de um SEGUNDO SINAL, `fonte_detalhamento_habilitada`, que so' liga quando
os dois contratos novos da AMH estiverem PUBLICADOS e PINADOS (XRG-3):

  * billing-status 0.4.0 (manifest v1.10): a relacao do responsavel financeiro (sem nome) e, so' para o
    pagador PF, o total do pagador com o detalhamento por beneficiario (rotulos `voce`, `titular`,
    `dependente N`);
  * billing-segunda-via 0.1.0 (manifest v1.11, contrato SEPARADO): o codigo de barras do boleto em aberto
    ou vencido, so' para o pagador PF.

Este modulo e' o lado do Maezo desses contratos: os DOIS portos (`BillingDetalhamentoPort`,
`BillingSegundaViaPort`), um transporte com interface (`ExecutorGovernado`, sem implementacao padrao: a E/S
real e' do gateway, ainda nao escrita) e adaptadores que conferem cada resposta CAMPO A CAMPO contra a forma
do contrato (fail-closed: forma errada = `CONTRACT_VIOLATION`, nunca um dado "consertado"). Nos testes o
transporte e' um FAKE.

O CODIGO DE BARRAS E' CREDENCIAL DE PAGAMENTO (quem o tem paga, ou desvia, o boleto):
  * so' ao pagador verificado (consentimento + identidade: o resolvedor tem de ser o da identidade
    verificada);
  * NUNCA no estado do grafo, no resultado do turno, na referencia de memoria, em log ou em metrica. O
    grafo escreve um MARCADOR (`MARCADOR_CODIGO`) no texto; o codigo vive so' em `CredenciaisDoTurno`
    (repr redigido), e `LucasTurno` o coloca no texto no ULTIMO passo, dentro do remetente, depois do
    gate e da cerca de saida. O Lucas nao tem checkpointer: nada do turno e' persistido, e o que o turno
    grava (referencia da resposta) e' o hash do texto COM o marcador;
  * o texto e' fixo (nunca o modelo de linguagem); o PDF nao e' enviado: orienta o aplicativo.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable
from urllib.parse import quote

import structlog

from maezo.agents.lucas.fonte_cobranca_amh import FonteDeConsentimento, ResolvedorDeSujeito
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult
from maezo.runtime.competencia import competencia_valida
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

Reason = PortFailureReason

#: Onde o codigo de barras entra no texto. O grafo escreve isto; so' o remetente do turno o troca.
MARCADOR_CODIGO: Final[str] = "{{CODIGO_DE_BARRAS}}"

#: Os artefatos dos contratos novos (caminhos no repositorio da AMH) e os blocos do pin que os trazem.
ARTIFACT_DETALHAMENTO: Final[str] = "schemas/openapi/maezo/v1/billing-status-detalhamento.openapi.yaml"
ARTIFACT_SEGUNDA_VIA: Final[str] = "schemas/openapi/maezo/v1/billing-segunda-via.openapi.yaml"
LOCK_KEY_DETALHAMENTO: Final[str] = "manifest_v1_10"
LOCK_KEY_SEGUNDA_VIA: Final[str] = "manifest_v1_11"

OPERATION_DETALHAMENTO: Final[str] = "amh.get_billing_detalhamento"
OPERATION_SEGUNDA_VIA: Final[str] = "amh.get_billing_segunda_via"
_SERVER: Final[str] = "/interop/billing-status/v1"
_PATH_STATUS: Final[str] = "/subjects/{ref}/billing/status"
_PATH_SEGUNDA_VIA: Final[str] = "/subjects/{ref}/billing/segunda-via"
#: Finalidade FIXA das duas rotas (contrato): o atendimento do WhatsApp.
PURPOSE_OF_USE: Final[str] = "atendimento_whatsapp"

_REF: Final[re.Pattern[str]] = re.compile(
    r"^amh:psr:v1:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_DECIMAL: Final[re.Pattern[str]] = re.compile(r"^[0-9]+\.[0-9]{2}$")
_DATA: Final[re.Pattern[str]] = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
_COMPETENCIA: Final[re.Pattern[str]] = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_ROTULO: Final[re.Pattern[str]] = re.compile(r"^(você|titular|dependente( [0-9]+)?)$")
_LINHA_DIGITAVEL: Final[re.Pattern[str]] = re.compile(
    r"^[0-9]{5}\.[0-9]{5} [0-9]{5}\.[0-9]{6} [0-9]{5}\.[0-9]{6} [0-9] [0-9]{14}$"
)
_CODIGO_BARRAS: Final[re.Pattern[str]] = re.compile(r"^[0-9]{44}$")
_REQUEST_REF: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
RESPONSAVEIS_FINANCEIROS: Final[frozenset[str]] = frozenset(
    {"voce", "outra_pessoa", "empresa", "administradora", "desconhecido"}
)
_SITUACOES_SEGUNDA_VIA: Final[frozenset[str]] = frozenset({"em_aberto", "vencida"})
_MOTIVOS_INDISPONIVEL: Final[frozenset[str]] = frozenset(
    {"sem_bloqueto", "formato_nao_reconhecido", "arrecadacao_nao_suportada", "dv_invalido", "valor_diverge"}
)
#: Quantos beneficiarios o texto lista, no maximo (o contrato admite 50; WhatsApp nao e' extrato).
MAX_BENEFICIARIOS_NO_TEXTO: Final[int] = 10

#: Vocabulario do estado da 2a via que vai ao grafo.
SEGUNDA_VIA_COM_CODIGO: Final[str] = "com_codigo"
SEGUNDA_VIA_SEM_CODIGO: Final[str] = "sem_codigo"


# ================================================================================================
# Valores do contrato (sem dado pessoal; o detalhamento nunca traz nome)
# ================================================================================================
@dataclass(frozen=True, slots=True)
class BeneficiarioDetalhe:
    """Um beneficiario do pagador, SO' pela relacao. Sem nome, CPF, matricula nem identificador."""

    rotulo: str
    relacao: str
    e_voce: bool
    valor_pre_estabelecido: str | None
    valor_coparticipacao: str | None
    valor_adicionais: str | None
    valor_outros: str | None
    valor_total: str | None


@dataclass(frozen=True, slots=True)
class GrupoPagador:
    total_mensalidade: str | None
    total_pre_estabelecido: str | None
    total_coparticipacao: str | None
    total_adicionais: str | None
    qt_beneficiarios: int | None
    detalhamento_fecha: bool
    beneficiarios: tuple[BeneficiarioDetalhe, ...]


@dataclass(frozen=True, slots=True)
class CompetenciaDetalhada:
    competencia: str
    #: `None` para quem nao e' o pagador PF; tupla (normalmente 1 grupo) para o pagador.
    grupos: tuple[GrupoPagador, ...] | None


@dataclass(frozen=True, slots=True)
class BillingDetalhamentoView:
    portable_subject_ref: str
    responsavel_financeiro: str | None
    financeiro_liberado: bool | None
    competencias: tuple[CompetenciaDetalhada, ...]


@dataclass(frozen=True, slots=True)
class BoletoSegundaVia:
    """Um boleto da 2a via. A linha digitavel e o codigo de barras sao CREDENCIAL: `repr=False`."""

    competencia: str
    vencimento: str | None
    situacao: str
    valor_titulo: str | None
    disponivel: bool
    indisponivel_motivo: str | None
    linha_digitavel: str | None = field(repr=False)
    codigo_barras: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class BillingSegundaViaView:
    portable_subject_ref: str
    boletos: tuple[BoletoSegundaVia, ...]
    fonte_atualizada_em: str


@runtime_checkable
class BillingDetalhamentoPort(Protocol):
    """billing-status 0.4.0 (`incluir_detalhamento` + `somente_pagador`). Sucesso ou recusa, nunca excecao."""

    async def get_billing_detalhamento(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        competencia: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[BillingDetalhamentoView]: ...


@runtime_checkable
class BillingSegundaViaPort(Protocol):
    """billing-segunda-via 0.1.0: codigo de barras so' do pagador PF. Sucesso ou recusa, nunca excecao."""

    async def get_segunda_via(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        request_ref: str,
        competencia: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[BillingSegundaViaView]: ...


# ================================================================================================
# O transporte (interface; a E/S real e' do gateway e nao existe ainda — os testes usam um FAKE)
# ================================================================================================
@dataclass(frozen=True, slots=True, repr=False)
class PedidoGovernado:
    """Descritor so' do servidor. `request_ref` e' o `X-Amh-Request-Ref` (opaco); a referencia de
    consentimento e' metadado de auditoria, nunca query."""

    operation: str
    path: str
    query: tuple[tuple[str, str | int], ...]
    consent_decision_ref: str | None
    request_ref: str | None
    timeout_seconds: float
    method: str = field(default="GET", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class RespostaGovernada:
    status_code: int
    body: bytes = field(repr=False)


class ExecutorGovernado(ABC):
    """Porta de composicao do gateway, sem implementacao padrao. A resposta da 2a via NUNCA vai para log,
    telemetria, indice em memoria ou cache (`Cache-Control: no-store`); quem implementa a E/S garante."""

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(self, request: PedidoGovernado) -> PortResult[RespostaGovernada]: ...


class _FormaInvalidaError(ValueError):
    """A resposta nao tem a forma do contrato. Sem texto de valor: nunca ecoa o corpo."""


def _json(corpo: bytes) -> dict[str, Any]:
    try:
        valor = json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        raise _FormaInvalidaError from None
    if not isinstance(valor, dict):
        raise _FormaInvalidaError
    return valor


def _texto_na_forma(valor: object, forma: re.Pattern[str], *, anulavel: bool = False) -> str | None:
    if valor is None and anulavel:
        return None
    if not isinstance(valor, str) or not forma.match(valor):
        raise _FormaInvalidaError
    return valor


def _booleano(valor: object, *, anulavel: bool = False) -> bool | None:
    if valor is None and anulavel:
        return None
    if not isinstance(valor, bool):
        raise _FormaInvalidaError
    return valor


def _enum(valor: object, conjunto: Collection[str], *, anulavel: bool = False) -> str | None:
    """O valor tem de ser texto E membro do vocabulario fechado (valor de outro tipo nunca estoura `in`)."""
    if valor is None and anulavel:
        return None
    if not isinstance(valor, str) or valor not in conjunto:
        raise _FormaInvalidaError
    return valor


def _beneficiario(bruto: object) -> BeneficiarioDetalhe:
    if not isinstance(bruto, dict):
        raise _FormaInvalidaError
    relacao = _enum(bruto.get("relacao"), ("titular", "dependente"))
    return BeneficiarioDetalhe(
        rotulo=_texto_na_forma(bruto.get("rotulo"), _ROTULO) or "",
        relacao=str(relacao),
        e_voce=_booleano(bruto.get("e_voce")) is True,
        valor_pre_estabelecido=_texto_na_forma(bruto.get("valor_pre_estabelecido"), _DECIMAL, anulavel=True),
        valor_coparticipacao=_texto_na_forma(bruto.get("valor_coparticipacao"), _DECIMAL, anulavel=True),
        valor_adicionais=_texto_na_forma(bruto.get("valor_adicionais"), _DECIMAL, anulavel=True),
        valor_outros=_texto_na_forma(bruto.get("valor_outros"), _DECIMAL, anulavel=True),
        valor_total=_texto_na_forma(bruto.get("valor_total"), _DECIMAL, anulavel=True),
    )


def _grupo(bruto: object) -> GrupoPagador:
    if not isinstance(bruto, dict):
        raise _FormaInvalidaError
    beneficiarios = bruto.get("beneficiarios")
    if not isinstance(beneficiarios, list) or len(beneficiarios) > 50:
        raise _FormaInvalidaError
    quantidade = bruto.get("qt_beneficiarios")
    if quantidade is not None and (
        not isinstance(quantidade, int) or isinstance(quantidade, bool) or quantidade < 0
    ):
        raise _FormaInvalidaError
    return GrupoPagador(
        total_mensalidade=_texto_na_forma(bruto.get("total_mensalidade"), _DECIMAL, anulavel=True),
        total_pre_estabelecido=_texto_na_forma(bruto.get("total_pre_estabelecido"), _DECIMAL, anulavel=True),
        total_coparticipacao=_texto_na_forma(bruto.get("total_coparticipacao"), _DECIMAL, anulavel=True),
        total_adicionais=_texto_na_forma(bruto.get("total_adicionais"), _DECIMAL, anulavel=True),
        qt_beneficiarios=quantidade,
        detalhamento_fecha=_booleano(bruto.get("detalhamento_fecha")) is True,
        beneficiarios=tuple(_beneficiario(b) for b in beneficiarios),
    )


def _visao_do_detalhamento(corpo: dict[str, Any], ref: str) -> BillingDetalhamentoView:
    if corpo.get("portable_subject_ref") != ref:
        # A resposta e' de OUTRO sujeito: nunca devolver fato de cobranca de quem nao foi pedido.
        raise _FormaInvalidaError
    resumo = corpo.get("resumo")
    competencias = corpo.get("competencias")
    if not isinstance(resumo, dict) or not isinstance(competencias, list):
        raise _FormaInvalidaError
    responsavel = _enum(resumo.get("responsavel_financeiro"), RESPONSAVEIS_FINANCEIROS, anulavel=True)
    liberado = _booleano(resumo.get("financeiro_liberado"), anulavel=True)
    detalhadas: list[CompetenciaDetalhada] = []
    for item in competencias:
        if not isinstance(item, dict):
            raise _FormaInvalidaError
        grupos = item.get("detalhamento_pagador")
        if grupos is not None and (not isinstance(grupos, list) or len(grupos) > 5):
            raise _FormaInvalidaError
        detalhadas.append(
            CompetenciaDetalhada(
                competencia=_texto_na_forma(item.get("competencia"), _COMPETENCIA) or "",
                grupos=None if grupos is None else tuple(_grupo(g) for g in grupos),
            )
        )
    return BillingDetalhamentoView(
        portable_subject_ref=ref,
        responsavel_financeiro=responsavel,
        financeiro_liberado=liberado,
        competencias=tuple(detalhadas),
    )


def _boleto_da_segunda_via(bruto: object) -> BoletoSegundaVia:
    if not isinstance(bruto, dict):
        raise _FormaInvalidaError
    situacao = _enum(bruto.get("situacao"), _SITUACOES_SEGUNDA_VIA)
    motivo = _enum(bruto.get("indisponivel_motivo"), _MOTIVOS_INDISPONIVEL, anulavel=True)
    disponivel = _booleano(bruto.get("disponivel"))
    linha = _texto_na_forma(bruto.get("linha_digitavel"), _LINHA_DIGITAVEL, anulavel=True)
    codigo = _texto_na_forma(bruto.get("codigo_barras"), _CODIGO_BARRAS, anulavel=True)
    if disponivel and linha is None and codigo is None:
        raise _FormaInvalidaError  # diz que ha' codigo e nao traz nenhum
    if not disponivel and (linha is not None or codigo is not None):
        raise _FormaInvalidaError  # traz credencial que o proprio contrato diz indisponivel
    return BoletoSegundaVia(
        competencia=_texto_na_forma(bruto.get("competencia"), _COMPETENCIA) or "",
        vencimento=_texto_na_forma(bruto.get("vencimento"), _DATA, anulavel=True),
        situacao=str(situacao),
        valor_titulo=_texto_na_forma(bruto.get("valor_titulo"), _DECIMAL, anulavel=True),
        disponivel=disponivel is True,
        indisponivel_motivo=None if motivo is None else str(motivo),
        linha_digitavel=linha,
        codigo_barras=codigo,
    )


def _visao_da_segunda_via(corpo: dict[str, Any], ref: str) -> BillingSegundaViaView:
    if corpo.get("portable_subject_ref") != ref:
        raise _FormaInvalidaError
    boletos = corpo.get("boletos")
    pdf = corpo.get("pdf")
    if not isinstance(boletos, list) or not 1 <= len(boletos) <= 3:
        raise _FormaInvalidaError
    if (
        not isinstance(pdf, dict)
        or pdf.get("disponivel") is not False
        or pdf.get("orientacao") != "pelo_aplicativo"
    ):
        raise _FormaInvalidaError
    fonte = corpo.get("fonte_atualizada_em")
    if not isinstance(fonte, str) or not fonte:
        raise _FormaInvalidaError
    return BillingSegundaViaView(
        portable_subject_ref=ref,
        boletos=tuple(_boleto_da_segunda_via(b) for b in boletos),
        fonte_atualizada_em=fonte,
    )


_STATUS_SEM_CORPO: Final[dict[int, PortFailureReason]] = {
    400: Reason.INVALID_REQUEST,
    401: Reason.NOT_AUTHENTICATED,
    403: Reason.SCOPE_NOT_SUPPORTED,
    404: Reason.NOT_FOUND,
    429: Reason.RATE_LIMITED,
    500: Reason.UPSTREAM_UNAVAILABLE,
}


async def _executar(
    executor: ExecutorGovernado, pedido: PedidoGovernado, operacao: str
) -> PortResult[RespostaGovernada]:
    """Confere o registro da operacao, executa com prazo e confere o envelope. Nunca levanta."""
    try:
        if (
            type(executor.registered_operations) is not frozenset
            or operacao not in executor.registered_operations
        ):
            return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
        async with asyncio.timeout(pedido.timeout_seconds):
            resposta = await executor.execute(pedido)
        if not isinstance(resposta, PortResult) or type(resposta.succeeded) is not bool:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
        if not resposta.succeeded:
            falha = resposta.failure
            if not isinstance(falha, PortFailure) or not isinstance(falha.reason, Reason):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            # O detalhe de um executor estrangeiro nao e' confiavel como livre de PHI.
            return PortResult.refused(falha.reason)
        recebida = resposta.value
        if not isinstance(recebida, RespostaGovernada) or type(recebida.status_code) is not int:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)
        return PortResult.ok(recebida)
    except TimeoutError:
        return PortResult.refused(Reason.TIMEOUT)
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES:
        return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)


def _prazo_valido(timeout_seconds: object) -> bool:
    if type(timeout_seconds) not in (int, float) or not isinstance(timeout_seconds, int | float):
        return False
    return math.isfinite(timeout_seconds) and timeout_seconds > 0


class AdaptadorDetalhamento:
    """`BillingDetalhamentoPort` sobre o billing-status 0.4.0, com a E/S delegada ao executor."""

    def __init__(self, executor: ExecutorGovernado) -> None:
        if not isinstance(executor, ExecutorGovernado):
            raise TypeError("AdaptadorDetalhamento: executor invalido")
        self._executor = executor

    async def get_billing_detalhamento(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        competencia: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[BillingDetalhamentoView]:
        if (
            not isinstance(portable_subject_ref, str)
            or not _REF.match(portable_subject_ref)
            or purpose_of_use != PURPOSE_OF_USE
            or not isinstance(consent_decision_ref, str)
            or not consent_decision_ref.strip()
            or (competencia is not None and not competencia_valida(competencia))
            or not _prazo_valido(timeout_seconds)
        ):
            return PortResult.refused(Reason.INVALID_REQUEST)
        query: list[tuple[str, str | int]] = [
            ("purpose_of_use", purpose_of_use),
            ("incluir_contratacao", "true"),
            ("incluir_detalhamento", "true"),
            # A regra de seguranca NO SERVIDOR: quem nao e' o pagador PF recebe so' a relacao e nenhum valor.
            ("somente_pagador", "true"),
        ]
        if competencia is not None:
            query.append(("competencia", competencia))
        pedido = PedidoGovernado(
            operation=OPERATION_DETALHAMENTO,
            path=_SERVER + _PATH_STATUS.replace("{ref}", quote(portable_subject_ref, safe="")),
            query=tuple(query),
            consent_decision_ref=consent_decision_ref,
            request_ref=None,
            timeout_seconds=float(timeout_seconds),
        )
        resultado = await _executar(self._executor, pedido, OPERATION_DETALHAMENTO)
        if not resultado.succeeded or resultado.value is None:
            falha = resultado.failure
            return PortResult.refused(falha.reason if falha is not None else Reason.CONTRACT_VIOLATION)
        recebida = resultado.value
        if recebida.status_code != 200:
            return PortResult.refused(_STATUS_SEM_CORPO.get(recebida.status_code, Reason.CONTRACT_VIOLATION))
        try:
            return PortResult.ok(_visao_do_detalhamento(_json(recebida.body), portable_subject_ref))
        except _FormaInvalidaError:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)


class AdaptadorSegundaVia:
    """`BillingSegundaViaPort` sobre o billing-segunda-via 0.1.0. O corpo (credencial) nao e' guardado."""

    def __init__(self, executor: ExecutorGovernado) -> None:
        if not isinstance(executor, ExecutorGovernado):
            raise TypeError("AdaptadorSegundaVia: executor invalido")
        self._executor = executor

    async def get_segunda_via(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        request_ref: str,
        competencia: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[BillingSegundaViaView]:
        if (
            not isinstance(portable_subject_ref, str)
            or not _REF.match(portable_subject_ref)
            or purpose_of_use != PURPOSE_OF_USE
            or not isinstance(request_ref, str)
            or not _REQUEST_REF.match(request_ref)
            or (competencia is not None and not competencia_valida(competencia))
            or not _prazo_valido(timeout_seconds)
        ):
            return PortResult.refused(Reason.INVALID_REQUEST)
        query: list[tuple[str, str | int]] = [("purpose_of_use", purpose_of_use)]
        if competencia is not None:
            query.append(("competencia", competencia))
        pedido = PedidoGovernado(
            operation=OPERATION_SEGUNDA_VIA,
            path=_SERVER + _PATH_SEGUNDA_VIA.replace("{ref}", quote(portable_subject_ref, safe="")),
            query=tuple(query),
            consent_decision_ref=None,
            request_ref=request_ref,
            timeout_seconds=float(timeout_seconds),
        )
        resultado = await _executar(self._executor, pedido, OPERATION_SEGUNDA_VIA)
        if not resultado.succeeded or resultado.value is None:
            falha = resultado.failure
            return PortResult.refused(falha.reason if falha is not None else Reason.CONTRACT_VIOLATION)
        recebida = resultado.value
        if recebida.status_code != 200:
            return PortResult.refused(_STATUS_SEM_CORPO.get(recebida.status_code, Reason.CONTRACT_VIOLATION))
        try:
            return PortResult.ok(_visao_da_segunda_via(_json(recebida.body), portable_subject_ref))
        except _FormaInvalidaError:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)


# ================================================================================================
# O segundo sinal: os contratos novos estao PUBLICADOS e PINADOS?
# ================================================================================================
def contratos_v4_pinados(pin_path: Path | None) -> bool:
    """`True` so' quando o pin (`contracts.lock.json`) traz os DOIS blocos, v1.10 e v1.11, cada um com o seu
    artefato. Hoje e' `False`: os manifests estao UNPUBLISHED na AMH e o pin so' entra depois da publicacao
    (XRG-3). Sem caminho de pin ou com pin ilegivel: `False`, nunca levanta. (Quem resolve o caminho e' a
    composicao da plataforma; os agentes nao importam adaptadores.)"""
    if pin_path is None:
        return False
    try:
        bruto = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(bruto, dict):
        return False
    for chave, artefato in (
        (LOCK_KEY_DETALHAMENTO, ARTIFACT_DETALHAMENTO),
        (LOCK_KEY_SEGUNDA_VIA, ARTIFACT_SEGUNDA_VIA),
    ):
        bloco = bruto.get(chave)
        artefatos = bloco.get("artifacts") if isinstance(bloco, dict) else None
        if not isinstance(artefatos, list) or not any(
            isinstance(a, dict) and a.get("path") == artefato and a.get("sha256") for a in artefatos
        ):
            return False
    return True


def fonte_detalhamento_habilitada(*, chave_ligada: bool, pin_path: Path | None = None) -> bool:
    """O SEGUNDO SINAL (`MAEZO_LUCAS_V4` ligada E os contratos v1.10/v1.11 pinados). Sem ele o Lucas v4 faz
    so' o que os dados ja' disponiveis (rota 0.3.0) sustentam."""
    return chave_ligada is True and contratos_v4_pinados(pin_path)


# ================================================================================================
# Os fatos que vao ao grafo (nunca a credencial) e a credencial do turno
# ================================================================================================
@dataclass(frozen=True, slots=True)
class FatosV4:
    """O que a fonte v4 entrega ao turno. NENHUM campo e' credencial de pagamento. Ausente = `None`/vazio."""

    responsavel_financeiro: str | None = None
    #: Um item por competencia com UM grupo de pagador (varios grupos = ausente: nunca se soma aqui).
    detalhamento: tuple[tuple[str, GrupoPagador], ...] = ()
    segunda_via_estado: str | None = None
    segunda_via_boletos: tuple[BoletoSegundaVia, ...] = ()
    #: Quantos boletos disponiveis alem do mostrado.
    segunda_via_outros: int = 0

    def como_entrada_lucas(self) -> dict[str, Any]:
        """As chaves de entrada do `LucasState`. So' as presentes; a credencial nunca vai."""
        saida: dict[str, Any] = {}
        if self.responsavel_financeiro in RESPONSAVEIS_FINANCEIROS:
            saida["responsavel_financeiro"] = self.responsavel_financeiro
        if self.detalhamento:
            saida["detalhamento_pagador"] = [
                {
                    "competencia": competencia,
                    "total_mensalidade": grupo.total_mensalidade,
                    "total_pre_estabelecido": grupo.total_pre_estabelecido,
                    "total_coparticipacao": grupo.total_coparticipacao,
                    "total_adicionais": grupo.total_adicionais,
                    "fecha": grupo.detalhamento_fecha,
                    "beneficiarios": [
                        {
                            "rotulo": b.rotulo,
                            "valor_pre_estabelecido": b.valor_pre_estabelecido,
                            "valor_coparticipacao": b.valor_coparticipacao,
                            "valor_adicionais": b.valor_adicionais,
                            "valor_outros": b.valor_outros,
                            "valor_total": b.valor_total,
                        }
                        for b in grupo.beneficiarios
                    ],
                }
                for competencia, grupo in self.detalhamento
            ]
        if self.segunda_via_estado in (SEGUNDA_VIA_COM_CODIGO, SEGUNDA_VIA_SEM_CODIGO):
            saida["segunda_via_estado"] = self.segunda_via_estado
            saida["segunda_via_boletos"] = [
                {
                    "competencia": b.competencia,
                    "vencimento": b.vencimento,
                    "situacao": b.situacao,
                    "valor_titulo": b.valor_titulo,
                    "outros": self.segunda_via_outros,
                }
                for b in self.segunda_via_boletos[:1]
            ]
        return saida


class CredenciaisDoTurno:
    """A credencial de pagamento de UM turno, em memoria, ate' o envio. Nunca em estado, log ou resultado.

    `repr` e `str` redigidos; nao serializavel (`__reduce__` recusa). O codigo e' a linha digitavel (a que a
    pessoa digita ou cola no aplicativo do banco) e, sem ela, o codigo de barras de 44 digitos.
    """

    __slots__ = ("_codigo",)

    def __init__(self, codigo: str | None) -> None:
        self._codigo = codigo if isinstance(codigo, str) and codigo else None

    def __repr__(self) -> str:
        return "CredenciaisDoTurno(<redigido>)"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        raise TypeError("CredenciaisDoTurno: a credencial de pagamento nao e' serializavel")

    @property
    def tem_codigo(self) -> bool:
        return self._codigo is not None

    def preencher(self, texto: str, *, sem_codigo: str) -> str:
        """O texto com o marcador trocado pelo codigo. Marcador sem codigo = `sem_codigo` (o texto honesto
        de "nao encontrei"), nunca o marcador cru ao beneficiario."""
        if MARCADOR_CODIGO not in texto:
            return texto
        if self._codigo is None:
            return sem_codigo
        return texto.replace(MARCADOR_CODIGO, self._codigo)


def sem_marcador_nem_credencial(texto: str) -> bool:
    """O texto nao tem o marcador cru? Usado pelo remetente como ultima conferencia antes do envio."""
    return MARCADOR_CODIGO not in texto


# ================================================================================================
# A fonte v4 (pagador verificado; reaproveita o resolvedor e a base legal da cobranca)
# ================================================================================================
class FonteV4:
    """Detalhamento e 2a via do PAGADOR. Nunca levanta por falha da dependencia; ausente e' `None`."""

    def __init__(
        self,
        *,
        resolvedor: ResolvedorDeSujeito,
        consentimento: FonteDeConsentimento,
        purpose_of_use: str = PURPOSE_OF_USE,
        detalhamento: BillingDetalhamentoPort | None = None,
        segunda_via: BillingSegundaViaPort | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        # Credencial de pagamento e valores do contrato SO' para beneficiario VERIFICADO (consentimento +
        # identidade): o resolvedor pelo telefone identifica, mas nao verifica.
        if getattr(resolvedor, "identidade_verificada", False) is not True:
            raise ValueError("fonte_v4: exige o resolvedor da identidade verificada")
        if not _prazo_valido(timeout_seconds):
            raise ValueError("fonte_v4: timeout_seconds invalido")
        self._resolvedor = resolvedor
        self._consentimento = consentimento
        self._purpose = purpose_of_use
        self._detalhamento = detalhamento
        self._segunda_via = segunda_via
        self._timeout = float(timeout_seconds)

    @property
    def tem_detalhamento(self) -> bool:
        return self._detalhamento is not None

    @property
    def tem_segunda_via(self) -> bool:
        return self._segunda_via is not None

    async def _ref_e_consentimento(self, pseudo_id: str, phone_hash: str | None) -> tuple[str, str] | None:
        if not pseudo_id:
            return None
        ref = await self._resolvedor.portable_ref(pseudo_id, phone_hash=phone_hash)
        if not ref:
            return None
        consentimento = await self._consentimento.decisao(ref, self._purpose)
        if not consentimento:
            return None
        return ref, consentimento

    async def detalhamento(
        self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None
    ) -> FatosV4 | None:
        """A relacao do responsavel financeiro e, so' para o pagador PF liberado, o total e o detalhamento da
        competencia. `None` = sem fato (fonte fora, sem consentimento, recusa): quem chama segue sem ele."""
        if self._detalhamento is None:
            return None
        try:
            achado = await self._ref_e_consentimento(pseudo_id, phone_hash)
            if achado is None:
                return None
            ref, consentimento = achado
            leitura = await self._detalhamento.get_billing_detalhamento(
                ref,
                purpose_of_use=self._purpose,
                consent_decision_ref=consentimento,
                competencia=competencia,
                timeout_seconds=self._timeout,
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return None
        if not leitura.succeeded or leitura.value is None:
            logger.info("lucas_v4_detalhamento_sem_fato", motivo=str(getattr(leitura.failure, "reason", "")))
            return None
        visao = leitura.value
        if visao.financeiro_liberado is not True:
            # Quem nao e' o pagador nunca recebe valor do pagador: so' a relacao.
            return FatosV4(responsavel_financeiro=visao.responsavel_financeiro)
        detalhes: list[tuple[str, GrupoPagador]] = []
        for item in visao.competencias:
            if item.grupos is not None and len(item.grupos) == 1:
                detalhes.append((item.competencia, item.grupos[0]))
        return FatosV4(responsavel_financeiro=visao.responsavel_financeiro, detalhamento=tuple(detalhes))

    async def segunda_via(
        self,
        pseudo_id: str,
        competencia: str | None,
        *,
        phone_hash: str | None = None,
        request_ref: str,
    ) -> tuple[FatosV4 | None, CredenciaisDoTurno | None]:
        """A 2a via do PAGADOR: os fatos SEM credencial e a credencial a parte. `(None, None)` = a fonte nao
        respondeu (o Lucas segue com o texto de antes). Pagador sem boleto/codigo = estado `sem_codigo`."""
        if self._segunda_via is None:
            return None, None
        try:
            achado = await self._ref_e_consentimento(pseudo_id, phone_hash)
            if achado is None:
                return None, None
            ref, _consentimento = achado
            leitura = await self._segunda_via.get_segunda_via(
                ref,
                purpose_of_use=self._purpose,
                request_ref=request_ref,
                competencia=competencia,
                timeout_seconds=self._timeout,
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return None, None
        if not leitura.succeeded or leitura.value is None:
            falha = leitura.failure
            if falha is not None and falha.reason == Reason.NOT_FOUND:
                # 404 e' identico para "sem boleto em aberto", "nao e' o pagador PF" e "rota desligada".
                return FatosV4(segunda_via_estado=SEGUNDA_VIA_SEM_CODIGO), CredenciaisDoTurno(None)
            logger.info("lucas_v4_segunda_via_sem_fato", motivo=str(getattr(falha, "reason", "")))
            return None, None
        disponiveis = [b for b in leitura.value.boletos if b.disponivel]
        if not disponiveis:
            return FatosV4(segunda_via_estado=SEGUNDA_VIA_SEM_CODIGO), CredenciaisDoTurno(None)
        primeiro = disponiveis[0]
        credencial = CredenciaisDoTurno(primeiro.linha_digitavel or primeiro.codigo_barras)
        fatos = FatosV4(
            segunda_via_estado=SEGUNDA_VIA_COM_CODIGO,
            segunda_via_boletos=(primeiro,),
            segunda_via_outros=len(disponiveis) - 1,
        )
        return fatos, credencial


__all__ = [
    "ARTIFACT_DETALHAMENTO",
    "ARTIFACT_SEGUNDA_VIA",
    "LOCK_KEY_DETALHAMENTO",
    "LOCK_KEY_SEGUNDA_VIA",
    "MARCADOR_CODIGO",
    "MAX_BENEFICIARIOS_NO_TEXTO",
    "OPERATION_DETALHAMENTO",
    "OPERATION_SEGUNDA_VIA",
    "PURPOSE_OF_USE",
    "RESPONSAVEIS_FINANCEIROS",
    "SEGUNDA_VIA_COM_CODIGO",
    "SEGUNDA_VIA_SEM_CODIGO",
    "AdaptadorDetalhamento",
    "AdaptadorSegundaVia",
    "BeneficiarioDetalhe",
    "BillingDetalhamentoPort",
    "BillingDetalhamentoView",
    "BillingSegundaViaPort",
    "BillingSegundaViaView",
    "BoletoSegundaVia",
    "CompetenciaDetalhada",
    "CredenciaisDoTurno",
    "ExecutorGovernado",
    "FatosV4",
    "FonteV4",
    "GrupoPagador",
    "PedidoGovernado",
    "RespostaGovernada",
    "contratos_v4_pinados",
    "fonte_detalhamento_habilitada",
    "sem_marcador_nem_credencial",
]
