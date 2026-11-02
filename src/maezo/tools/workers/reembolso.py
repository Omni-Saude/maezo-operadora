"""SP-OP-REEMBOLSO-001 Worker — Reembolso ao Beneficiario (v0.2.0, DL-0161: 100% analise humana).

External tasks for beneficiary reimbursement. O sistema so ORGANIZA o pedido, os documentos e a
resposta; uma pessoa da analise decide direito e valor. Por isso este modulo NAO calcula valor e NAO
emite pagamento (o financeiro paga fora e registra `data_pagamento` em `UT_RegistrarPagamento`). Foram
removidos em DL-0161: `calculate_value`, `calculate_amount_entry`, `process_payment`,
`issue_payment_entry`, `validate_reembolso`/`check_prazo` e os erros de dinheiro associados.

Faixa B DORMENTE (R-070 opcao B, R-076; decisao do dono de 2026-11-01 no PR #1149): o interruptor R-058
(`REEMBOLSO_AUTO_PAGAMENTO_LIBERADO`, `False`) volta como metade "codigo" do kill-switch duplo, junto com
`validate_auto_criteria` (RELAIA fatos para a DMN `reembolso_auto_approval`; nao compara) e
`register_auto_approval` (guarda de efeito da aprovacao automatica integral). Com os defaults (R-058
`False`, teto 0, kill-switch de politica fechado, nenhuma fonte de fatos) nenhum pedido sai da analise
humana.

Negativa-like L0 hard: `send_reembolso_denial` e GUARDADO por ERR_REEMBOLSO_DENIAL_NOT_HUMAN — negar ou
reduzir reembolso so se materializa depois de decisao humana em UT_AnaliseReembolso,
UT_RevisaoAuditorMedico ou UT_CoordenacaoReembolso. O worker VALIDA a decisao; o aviso ao beneficiario
sai pelo Lucas (consumidor `agent_resume`), a partir do historico do motor.
"""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.engine_var_types import is_exact_engine_integer
from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

#: `check_coverage` raises this (as a `WorkerBpmnError`) when `protocolo_reembolso` arrives absent/blank —
#: a TECHNICAL origin guard (ADR-0030 §2): the worker never decides to pay/deny, it only signals
#: bad-at-source request data. MODELED boundary error: `BE_ReembolsoProtocoloInvalido` on
#: `ST_ValidarSolicitacao` routes to the NEUTRO terminal `End_ReembolsoProtocoloInvalido`.
ERR_REEMBOLSO_INVALID_PROTOCOLO = "ERR_REEMBOLSO_INVALID_PROTOCOLO"

#: Consumption-covered (`scripts/ci/check_bpmn_error_allowlist.py`'s "simple rule"):
#: `operadora.reembolso.check_coverage` is consumed ONLY by SP-OP-REEMBOLSO-001, which declares this
#: errorCode on `BE_ReembolsoProtocoloInvalido`. `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` is DELIBERATELY excluded:
#: it has no modeled boundary catch (`send_reembolso_denial` stays a `PermissionError` -> incident).
REEMBOLSO_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_REEMBOLSO_INVALID_PROTOCOLO})

#: Valor que `dias_desde_atendimento` assume quando as datas nao sao calculaveis. A DMN informativa
#: `reembolso_prazo` o trata pelo catch-all (NAO_AVALIADO); nunca vira negativa.
DIAS_NAO_CALCULAVEL = -1

#: Decisoes adversas: as UNICAS cujo efeito exige o guard humano (`send_reembolso_denial`).
_DECISOES_ADVERSAS = ("NEGAR", "APROVAR_PARCIAL")


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class ReembolsoDenialNotHumanError(PermissionError):
    """Raised when send_reembolso_denial is called without human authorization.

    Guard ERR_REEMBOLSO_DENIAL_NOT_HUMAN — the worker MUST refuse to validate a denial/reduction unless
    decisao_reembolso in {NEGAR, APROVAR_PARCIAL} was set by a human with analista_id (or auditor_id) and the
    texto_ao_beneficiario the analyst wrote for the beneficiary.
    """

    def __init__(self, missing_fields: list[str] | None = None) -> None:
        self.missing_fields = missing_fields or []
        msg = "ERR_REEMBOLSO_DENIAL_NOT_HUMAN: denial/reduction requires human decision"
        if self.missing_fields:
            msg += f"; missing: {', '.join(self.missing_fields)}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass
class ReembolsoInput:
    """Input for reembolso processing (contract SP-OP-REEMBOLSO-001 v0.2.0, "Variaveis de entrada")."""

    tenant_id: str = ""
    protocolo_reembolso: str = ""
    numero_guia_tiss: str | None = None
    beneficiario_pseudo_id: str = ""
    matricula_beneficiario: str = ""
    tipo_reembolso: str = ""
    codigo_procedimento_tuss: str | None = None
    categoria_procedimento: str = ""
    valor_solicitado_cents: int = 0
    data_atendimento: str = ""
    data_solicitacao: str = ""
    cid10: str | None = None
    documentos_refs: list[dict[str, Any]] = field(default_factory=list)
    relacao_com_titular: str = ""
    motivo_nao_rede: str = ""
    situacao_prazo: str = ""


@dataclass
class ReembolsoDenialInput:
    """Input for send_reembolso_denial — the gated adverse effect."""

    decisao_reembolso: str = ""
    justificativa: str = ""
    fundamentacao_contratual: str = ""
    texto_ao_beneficiario: str = ""
    valor_reembolso_aprovado_cents: int = 0
    valor_solicitado_cents: int = 0
    analista_id: str = ""
    auditor_id: str = ""
    cid10_referencia: str = ""
    parecer_auditor: str = ""


# ---------------------------------------------------------------------------
# R-070 opcao B — faixa automatica L2 DORMENTE (R-076; decisao do dono de 2026-11-01 no PR #1149:
# "manter as duas coisas" — a v2 100% humana de DL-0161 e' o modelo vivo; esta faixa existe DORMENTE,
# fechada pelo R-058 abaixo E pelo teto 0 da politica, e so' abre com a assinatura do dono + atuaria no
# bloco §6 de `docs/compliance/human-windows/R-070-dec-teto-d07.md`, hoje EM BRANCO).
#
# Este modulo NAO decide nada da faixa: `validate_auto_criteria` RELAIA fatos (politica + fonte
# injetavel) para a DMN `reembolso_auto_approval`, que faz TODA comparacao de roteamento (ADR-0012), e
# `register_auto_approval` so' re-verifica, como GUARDA de efeito (recusa = incidente, nunca roteamento),
# que o caminho automatico esta liberado e que o valor aprovado e' o solicitado e cabe no teto. O pagamento
# continua FORA do sistema: o financeiro registra `data_pagamento` em `UT_RegistrarPagamento`.
# ---------------------------------------------------------------------------

# R-058 — interruptor de CODIGO do caminho automatico (metade "codigo" do kill-switch duplo do R-070 §3.1
# item 5; a outra metade e' o teto 0 da politica). `Flow_GWFaixa_Auto` exige a variavel que
# `validate_auto_criteria` relaia desta constante, e `register_auto_approval` recusa a origem automatica
# enquanto ela for `False`. NAO le teto, politica de tenant nem variavel de processo: so' vira por mudanca
# revisavel e nomeada, junto com a assinatura do R-070 §6 — nunca como efeito colateral de subir o teto.
#: Kill-switch de codigo (R-058). `False` ate a assinatura do dono + atuaria no R-070 §6.
REEMBOLSO_AUTO_PAGAMENTO_LIBERADO: bool = False

#: Nome da variavel que `validate_auto_criteria` escreve a partir da constante acima e que a condicao de
#: `Flow_GWFaixa_Auto` le. Pinado para que modelo e codigo tenham UMA fonte do nome.
VAR_REEMBOLSO_AUTO_LIBERADO = "reembolso_auto_liberado"

#: Variavel que diz ao `GW_DesfechoPagamento` de onde veio a aprovacao. `validate_auto_criteria` escreve
#: `HUMANA` em TODA instancia (sobrescreve qualquer semente da partida) e so' `register_auto_approval`
#: escreve `AUTO_L2`, depois das guardas.
VAR_ORIGEM_APROVACAO = "origem_aprovacao"
ORIGEM_HUMANA = "HUMANA"
ORIGEM_AUTO_L2 = "AUTO_L2"

#: Erro do worker `register_auto_approval` (incidente; sem boundary modelado, de proposito).
ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO = "ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO"

# Acao/parametros da matriz de autonomia (L0-core.yaml + overlay do tenant), lidos pelo MESMO carregador
# do PEP (`CeilingResolver`). Valores DRAFT do R-070 §3.4; teto 0 ate o §6.
_CEILING_ACTION = "reembolso_auto_approval"
_CEILING_PARAM = "max_value_brl"
#: Kill-switch de politica: `true`, ausente ou nao-bool YAML => fechada.
_PARAM_FAIXA_DESLIGADA = "faixa_auto_desligada"
_PARAM_EXPO_QTD_BENEF = "exposicao_max_aprovacoes_beneficiario_30d"
_PARAM_EXPO_VALOR_BENEF_BRL = "exposicao_max_valor_beneficiario_30d_brl"
_PARAM_ORCAMENTO_TENANT_BRL = "orcamento_diario_tenant_brl"


@dataclass(frozen=True)
class FatosFaixaAuto:
    """Fatos da faixa B que NENHUMA fonte deste repo produz ainda (default = "desconhecido" = humano).

    Sem merito clinico conhecido (nao ha OCR nem triagem clinica na v2), sem tabela de reembolso (a da
    atuaria fica para uma versao futura, DL-0161), sem limite contratual (o link do contrato AMH esta em
    construcao), NF nao verificada (guarda G2 do #1074) e sem contadores de exposicao (R-070 §5). Uma
    fonte so' entra pelo seam `reembolso_fonte_faixa_auto` de :func:`register_reembolso_workers`;
    producao nao passa nenhuma, entao a faixa segue fechada mesmo com teto positivo.
    """

    avaliacao_clinica_requerida: bool | None = None
    valor_tabela_cents: int | None = None
    limite_contratual_cents: int | None = None
    valor_nf_verificado: bool = False
    aprovacoes_auto_benef_30d: int | None = None
    valor_auto_benef_30d_cents: int | None = None
    valor_auto_tenant_dia_cents: int | None = None


#: Fonte de :class:`FatosFaixaAuto` para um pedido (seam; nunca uma regra em Python).
FonteFatosFaixaAuto = Callable[["ReembolsoInput"], FatosFaixaAuto]


def _sem_fonte_faixa_auto(input_data: ReembolsoInput) -> FatosFaixaAuto:
    """Fonte padrao: nada conhecido, entao o catch-all da DMN manda o pedido a um humano."""
    del input_data
    return FatosFaixaAuto()


def _contagem_ou_none(value: object) -> int | None:
    """Relaia um inteiro exato nao-negativo (centavos ou contagem); qualquer outra coisa vira None."""
    if is_exact_engine_integer(value) and isinstance(value, int) and value >= 0:
        return value
    return None


@dataclass
class ReembolsoFaixaAutoResult:
    """Saida de `validate_auto_criteria`: so' FATOS relaiados, nenhuma comparacao e nenhuma decisao.

    Todo campo e' escrito no escopo do PROCESSO (a task nao tem inputParameter local homonimo — reparo
    B1), entao uma semente da partida com o mesmo nome e' sobrescrita antes de a DMN ler. Os defaults
    sao a forma fail-safe: um resultado construido sem passar pela funcao nunca abre nada.
    """

    reembolso_auto_liberado: bool = False
    origem_aprovacao: str = ORIGEM_HUMANA
    #: Lado da politica: teto e limites DRAFT em centavos/contagem (0 = nada admitido) e o kill-switch
    #: de politica (fechado salvo `false` YAML real).
    teto_l2_cents: int = 0
    faixa_auto_desligada: bool = True
    limite_aprovacoes_auto_benef_30d: int = 0
    limite_valor_auto_benef_30d_cents: int = 0
    orcamento_auto_tenant_dia_cents: int = 0
    #: Lado da fonte (:class:`FatosFaixaAuto`): None/False = desconhecido = humano.
    avaliacao_clinica_requerida: bool | None = None
    valor_tabela_dmn_cents: int | None = None
    limite_contratual_cents: int | None = None
    valor_nf_verificado: bool = False
    aprovacoes_auto_benef_30d: int | None = None
    valor_auto_benef_30d_cents: int | None = None
    valor_auto_tenant_dia_cents: int | None = None


def validate_auto_criteria(
    input_data: ReembolsoInput,
    *,
    resolver: CeilingResolver | None = None,
    fonte_faixa_auto: FonteFatosFaixaAuto | None = None,
) -> ReembolsoFaixaAutoResult:
    """RELAIA os fatos da faixa B para a DMN `reembolso_auto_approval` — sem comparar nada (ADR-0012).

    Serve `ST_ValidarFaixaAuto` (topico `operadora.reembolso.validate_auto_criteria`), que roda em TODA
    instancia antes da analise. Politica: a MESMA matriz que o PEP avalia (BRL -> centavos e' troca de
    unidade, inteiro x 100, nunca arredondamento). Fonte: valores que nao sao inteiros exatos
    nao-negativos / um `True` literal viram "desconhecido" — um fato nao confiavel nunca vira aprovacao.
    Tambem relaia o R-058 (constante, nao calculo) e grava `origem_aprovacao = HUMANA`.
    """
    resolver = resolver if resolver is not None else CeilingResolver()
    fonte = fonte_faixa_auto if fonte_faixa_auto is not None else _sem_fonte_faixa_auto
    tenant = input_data.tenant_id
    desligada = resolver.bool_param(tenant=tenant, action=_CEILING_ACTION, param=_PARAM_FAIXA_DESLIGADA)
    try:
        fatos = fonte(input_data)
    except Exception as exc:  # fail-closed: fonte quebrada = nada conhecido -> humano
        logger.warning("reembolso.faixa_auto.fonte_indisponivel", error_type=type(exc).__name__)
        fatos = FatosFaixaAuto()
    if not isinstance(fatos, FatosFaixaAuto):
        fatos = FatosFaixaAuto()
    clinica = fatos.avaliacao_clinica_requerida
    result = ReembolsoFaixaAutoResult(
        # R-058: RELAIA a constante; nenhum dado do pedido, do tenant ou do teto entra aqui.
        reembolso_auto_liberado=REEMBOLSO_AUTO_PAGAMENTO_LIBERADO,
        origem_aprovacao=ORIGEM_HUMANA,
        teto_l2_cents=resolver.ceiling_brl(tenant=tenant, action=_CEILING_ACTION, param=_CEILING_PARAM) * 100,
        faixa_auto_desligada=desligada is not False,
        limite_aprovacoes_auto_benef_30d=resolver.int_param(
            tenant=tenant, action=_CEILING_ACTION, param=_PARAM_EXPO_QTD_BENEF
        ),
        limite_valor_auto_benef_30d_cents=resolver.int_param(
            tenant=tenant, action=_CEILING_ACTION, param=_PARAM_EXPO_VALOR_BENEF_BRL
        )
        * 100,
        orcamento_auto_tenant_dia_cents=resolver.int_param(
            tenant=tenant, action=_CEILING_ACTION, param=_PARAM_ORCAMENTO_TENANT_BRL
        )
        * 100,
        avaliacao_clinica_requerida=clinica if isinstance(clinica, bool) else None,
        valor_tabela_dmn_cents=_contagem_ou_none(fatos.valor_tabela_cents),
        limite_contratual_cents=_contagem_ou_none(fatos.limite_contratual_cents),
        valor_nf_verificado=fatos.valor_nf_verificado is True,
        aprovacoes_auto_benef_30d=_contagem_ou_none(fatos.aprovacoes_auto_benef_30d),
        valor_auto_benef_30d_cents=_contagem_ou_none(fatos.valor_auto_benef_30d_cents),
        valor_auto_tenant_dia_cents=_contagem_ou_none(fatos.valor_auto_tenant_dia_cents),
    )
    logger.info(
        "reembolso.validate_auto_criteria.complete",
        protocolo_reembolso=input_data.protocolo_reembolso,
        reembolso_auto_liberado=result.reembolso_auto_liberado,
        faixa_auto_desligada=result.faixa_auto_desligada,
        teto_l2_cents=result.teto_l2_cents,
    )
    return result


class ReembolsoAutoPagamentoBloqueadoError(PermissionError):
    """ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO — o caminho automatico chegou ao registro sem estar liberado.

    Incidente (sem boundary modelado): nada e' aprovado, `origem_aprovacao` fica `HUMANA` e nenhum valor
    e' escrito. Nomes de motivo, nunca valores, entram na mensagem.
    """

    def __init__(self, motivos: list[str]) -> None:
        self.motivos = list(motivos)
        super().__init__(f"{ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO}: {', '.join(self.motivos)}")


def register_auto_approval(
    *,
    tenant_id: str,
    valor_aprovado_auto_cents: object,
    valor_solicitado_cents: object,
    origem_pagamento: str,
    resolver: CeilingResolver | None = None,
) -> dict[str, Any]:
    """Registra a aprovacao automatica INTEGRAL — GUARDADO (segunda barreira independente do R-058).

    Serve `ST_RegistrarAprovacaoAuto` (topico `operadora.reembolso.register_auto_approval`), alcancavel
    SO' por `Flow_GWFaixa_Auto`. Nao paga nada (o financeiro paga fora e registra em
    `UT_RegistrarPagamento`); escreve `valor_reembolso_aprovado_cents` (o valor que o financeiro paga) e
    `origem_aprovacao = AUTO_L2`. Recusa (incidente) quando: R-058 desligado; kill-switch de politica nao
    e' `false`; origem local nao e' `AUTO_L2`; o valor da DMN nao e' inteiro exato positivo IGUAL ao
    solicitado (aprovacao integral, nunca parcial); ou o valor nao cabe no teto do tenant
    (`CeilingResolver.within_l2_ceiling`, fail-closed em teto 0). A regra de roteamento vive na DMN; isto
    so' impede que um defeito do modelo ou da politica pague acima do teto.
    """
    resolver = resolver if resolver is not None else CeilingResolver()
    motivos: list[str] = []
    if REEMBOLSO_AUTO_PAGAMENTO_LIBERADO is not True:
        motivos.append("R-058: REEMBOLSO_AUTO_PAGAMENTO_LIBERADO=False")
    desligada = resolver.bool_param(tenant=tenant_id, action=_CEILING_ACTION, param=_PARAM_FAIXA_DESLIGADA)
    if desligada is not False:
        motivos.append("faixa_auto_desligada (politica) nao e' false")
    if origem_pagamento != ORIGEM_AUTO_L2:
        motivos.append("origem_pagamento nao e' AUTO_L2")
    aprovado, solicitado = valor_aprovado_auto_cents, valor_solicitado_cents
    if not (
        isinstance(aprovado, int)
        and isinstance(solicitado, int)
        and is_exact_engine_integer(aprovado)
        and is_exact_engine_integer(solicitado)
    ):
        motivos.append("valor integer-centavos invalido")
    elif aprovado <= 0:
        motivos.append("valor aprovado automatico nao positivo")
    elif aprovado != solicitado:
        motivos.append("valor aprovado automatico != solicitado (so' aprovacao integral)")
    elif not resolver.within_l2_ceiling(
        tenant=tenant_id, action=_CEILING_ACTION, param=_CEILING_PARAM, value_cents=aprovado
    ):
        motivos.append("valor fora do teto L2 do tenant (ou teto 0)")
    if motivos:
        logger.error("reembolso.register_auto_approval.bloqueado", motivos=motivos)
        raise ReembolsoAutoPagamentoBloqueadoError(motivos)
    logger.info("reembolso.register_auto_approval.complete", tenant_id=tenant_id)
    return {
        "valor_reembolso_aprovado_cents": aprovado,
        VAR_ORIGEM_APROVACAO: ORIGEM_AUTO_L2,
    }


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def _dias_desde_atendimento(data_atendimento: str, data_solicitacao: str) -> int:
    """Dias entre o atendimento e a solicitacao; `DIAS_NAO_CALCULAVEL` se nao for calculavel.

    Aritmetica de datas, sem regra de prazo: a regra (contratual/regulatoria) vive na DMN `reembolso_prazo`.
    """
    try:
        atendimento = date.fromisoformat(str(data_atendimento).strip()[:10])
        solicitacao = date.fromisoformat(str(data_solicitacao).strip()[:10])
    except ValueError:
        return DIAS_NAO_CALCULAVEL
    dias = (solicitacao - atendimento).days
    return dias if dias >= 0 else DIAS_NAO_CALCULAVEL


def check_coverage(input_data: ReembolsoInput) -> dict[str, Any]:
    """Valida a origem da solicitacao e resolve `dias_desde_atendimento` (topico `check_coverage`).

    ORIGIN GUARD (ADR-0030 Tier-2): `ST_ValidarSolicitacao` carrega o boundary
    `BE_ReembolsoProtocoloInvalido` -> `End_ReembolsoProtocoloInvalido` (terminal NEUTRO).
    `protocolo_reembolso`
    ausente/em branco lanca o `WorkerBpmnError` modelado, antes de qualquer outro passo.

    v0.2.0 (DL-0161): nao ha mais conferencia de cobertura por maquina (o analista avalia). O unico fato
    resolvido e `dias_desde_atendimento`, insumo da DMN informativa `reembolso_prazo`. NUNCA decide
    pagar/negar.
    """
    if not input_data.protocolo_reembolso.strip():
        logger.error("reembolso.check_coverage.protocolo_invalido")
        raise WorkerBpmnError(ERR_REEMBOLSO_INVALID_PROTOCOLO, "protocolo_reembolso ausente")

    dias = _dias_desde_atendimento(input_data.data_atendimento, input_data.data_solicitacao)
    logger.info(
        "reembolso.check_coverage.complete",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
        dias_desde_atendimento=dias,
    )
    return {"dias_desde_atendimento": dias}


def request_documents(
    beneficiario_pseudo_id: str,
    protocolo_reembolso: str,
    message_type: str = "pendencia_documentacao",
) -> dict[str, Any]:
    """Registra que a ETAPA de abertura da pendencia rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_SolicitarDocumentos`, topico `operadora.reembolso.request_documents`. Nao adversa:
    emparelha com a
    corrida de `GW_AguardarDocs` (message `msg.reembolso.docs_received` OU timer `ICE_PrazoPendencia`). Uma
    beneficiaria que nunca responde e decidida por um HUMANO em `UT_DecidirPendenciaExpirada`.

    Nenhum canal e contatado aqui (FAB-NOTIFIED-TRIO): o aviso com a pergunta do analista
    (`info_solicitada`) sai
    pelo Lucas, a partir do resultado `reembolso_info_solicitada` publicado por `ST_AvisarInfo`. O evento
    `agents.events.reembolso.pended` e publicado pelo proprio BPMN (`ST_PublishReembolsoPended`).
    """
    logger.info(
        "reembolso.request_documents",
        beneficiario_pseudo_id=beneficiario_pseudo_id,
        protocolo_reembolso=protocolo_reembolso,
        message_type=message_type,
        notified_asserted=False,
    )
    return {}


def analyze_request(input_data: ReembolsoInput) -> dict[str, Any]:
    """Prepare the reembolso analysis dossier — convoca o agente analista (Marina-classe).

    `ST_PrepararDossie` is the ONLY path into `UT_AnaliseReembolso`. O dossie reune evidencia estruturada
    (protocolo, tipo, categoria, valor solicitado, paciente, motivo, documentos e a situacao do prazo — esta
    ultima so INFORMATIVA) para INSTRUIR a decisao humana, nunca a substitui (principio Rafael, ADR-0005): a
    recomendacao devolvida nunca e um valor de `decisao_reembolso`.
    """
    logger.info(
        "reembolso.analyze_request.start",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
    )
    result = {
        "protocolo_reembolso": input_data.protocolo_reembolso,
        "tipo_reembolso": input_data.tipo_reembolso,
        "categoria_procedimento": input_data.categoria_procedimento,
        "valor_solicitado_cents": input_data.valor_solicitado_cents,
        "situacao_prazo": input_data.situacao_prazo,
        "dossie": "dossie_instruido",
        "recomendacao_sugerida": "ANALISE_HUMANA",  # O agente NUNCA decide (ADR-0005)
    }
    logger.info("reembolso.analyze_request.complete", protocolo_reembolso=input_data.protocolo_reembolso)
    return result


def notify_sla_risk(input_data: ReembolsoInput) -> dict[str, Any]:
    """Registra que a ETAPA de alerta de risco de SLA rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_NotificarRiscoSla`, alimentado SO pelo boundary NAO-interruptivo `BT_AlertaSla` em
    `UT_AnaliseReembolso`. Informativa e jamais adversa: a User Task segue aberta. Nenhum canal e contatado
    (FAB-SLA-RISK-NOTIFIED-SLICE4); a observabilidade fica no log, com `notified_asserted=False`.
    """
    logger.info(
        "reembolso.notify_sla_risk",
        protocolo_reembolso=input_data.protocolo_reembolso,
        tipo_reembolso=input_data.tipo_reembolso,
        notified_asserted=False,
    )
    return {}


def send_reembolso_denial(denial_input: ReembolsoDenialInput) -> dict[str, Any]:
    """Validate a reimbursement denial/reduction — GUARDED adverse effect.

    ERR_REEMBOLSO_DENIAL_NOT_HUMAN: MUST refuse if:
    - decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}
    - Missing justificativa, fundamentacao_contratual (internas) or texto_ao_beneficiario (o que o Lucas leva)
    - Missing analista_id (or auditor_id for clinical merit)
    - If clinical merit: missing cid10_referencia or parecer_auditor (F-045). Clinical merit is
      signalled by any of auditor_id / cid10_referencia / parecer_auditor (fields only the
      `UT_RevisaoAuditorMedico` form fills); then all three are required.
    - APROVAR_PARCIAL sem valor aprovado positivo e menor que o solicitado

    O worker so VALIDA; nao comunica nada (nenhum canal). O aviso sai pelo Lucas, que le o texto do historico.
    """
    logger.info(
        "reembolso.send_reembolso_denial.start",
        decisao_reembolso=denial_input.decisao_reembolso,
        analista_id=denial_input.analista_id,
    )

    missing: list[str] = []

    if denial_input.decisao_reembolso not in _DECISOES_ADVERSAS:
        missing.append("decisao_reembolso not in {NEGAR, APROVAR_PARCIAL}")
    if not denial_input.justificativa.strip():
        missing.append("justificativa")
    if not denial_input.fundamentacao_contratual.strip():
        missing.append("fundamentacao_contratual")
    if not denial_input.texto_ao_beneficiario.strip():
        missing.append("texto_ao_beneficiario")

    # Human identifier: analista_id or auditor_id
    if not (denial_input.analista_id.strip() or denial_input.auditor_id.strip()):
        missing.append("analista_id (or auditor_id)")

    # Merito clinico (F-045): contrato SP-OP-REEMBOLSO-001 ("Variaveis de saida" e "Campos
    # obrigatorios por decisao adversa") + BPMN `UT_RevisaoAuditorMedico` — negativa/reducao por
    # merito clinico exige auditor_id + cid10_referencia + parecer_auditor. A guarda NAO julga o
    # merito (L0 hard, medico auditor); ela so recusa COMUNICAR uma decisao clinica sem lastro.
    # Sinal de merito clinico = qualquer um dos tres campos que so o formulario do auditor
    # preenche; lastro parcial falha fechado, nunca um default silencioso. Nomes, nunca valores
    # (cid10_referencia/parecer_auditor sao PHI) entram na mensagem.
    clinical_fields = {
        "auditor_id": denial_input.auditor_id,
        "cid10_referencia": denial_input.cid10_referencia,
        "parecer_auditor": denial_input.parecer_auditor,
    }
    if any(value.strip() for value in clinical_fields.values()):
        missing.extend(name for name, value in clinical_fields.items() if not value.strip())

    requested_exact = is_exact_engine_integer(denial_input.valor_solicitado_cents)
    approved_exact = is_exact_engine_integer(denial_input.valor_reembolso_aprovado_cents)
    if not requested_exact:
        missing.append("valor_solicitado_cents integer-centavos invalido")
    if not approved_exact:
        missing.append("valor_reembolso_aprovado_cents integer-centavos invalido")

    # Non-exact values must never reach comparisons (bool is an int subclass).
    if denial_input.decisao_reembolso == "APROVAR_PARCIAL" and requested_exact and approved_exact:
        if denial_input.valor_reembolso_aprovado_cents <= 0:
            missing.append("valor_reembolso_aprovado_cents")
        elif denial_input.valor_reembolso_aprovado_cents >= denial_input.valor_solicitado_cents:
            missing.append("APROVAR_PARCIAL: valor aprovado >= valor solicitado (nao e reducao)")

    if missing:
        raise ReembolsoDenialNotHumanError(missing_fields=missing)

    return {
        "decisao_adversa_validada": True,
        # HONEST STATE (F-009, #1063): este worker so' VALIDA e registra; nada foi enviado aqui. O aviso sai
        # depois, pela tarefa de aviso do BPMN (agents.events.process_completed -> retomada do Lucas).
        "denial_sent": False,
        "denial_registered": True,
        "notification_status": "PENDING_CHANNEL",
        "decisao_reembolso": denial_input.decisao_reembolso,
        "valor_solicitado_cents": denial_input.valor_solicitado_cents,
        "valor_reembolso_aprovado_cents": denial_input.valor_reembolso_aprovado_cents,
        "analista_id": denial_input.analista_id or denial_input.auditor_id,
    }


def publish_completed(
    event_type: str = "reembolso.completed",
    payload: dict[str, Any] | None = None,
    desfecho: str = "",
) -> dict[str, Any]:
    """Publish reembolso completion event (dual engine+Kafka, ADR-0007)."""
    _payload = dict(payload or {})
    if desfecho:
        _payload["desfecho"] = desfecho

    logger.info("reembolso.publish_completed", event_type=event_type, desfecho=desfecho)

    return {
        "published": True,
        "topic": f"agents.events.{event_type}",
        "event_type": event_type,
        "payload": _payload,
    }


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — one per external-task topic. Explicit field selection ->
# typed dataclass -> the typed function above -> dict. Pure marshalling.
#
# Topic mapping vs spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn (excl. the shared
# `operadora.events.publish`):
#   check_coverage        -> operadora.reembolso.check_coverage (ST_ValidarSolicitacao)
#   request_documents     -> operadora.reembolso.request_documents (ST_SolicitarDocumentos)
#   analyze_request       -> operadora.reembolso.analyze_request (ST_PrepararDossie)
#   notify_sla_risk       -> operadora.reembolso.notify_sla_risk (ST_NotificarRiscoSla)
#   send_reembolso_denial -> operadora.reembolso.send_reembolso_denial (ST_EnviarNegativa,
# ST_ComunicarReducao; GUARDED)
#   validate_auto_criteria -> operadora.reembolso.validate_auto_criteria (ST_ValidarFaixaAuto; faixa B, relay)
#   register_auto_approval -> operadora.reembolso.register_auto_approval (ST_RegistrarAprovacaoAuto; faixa B,
# GUARDED — R-058)
# publish_completed folds into the generic events.publish task per BPMN — function-derived topic, KEPT
# registered
# (documented registry-completeness convention shared with recurso.py; do not remove).
# ---------------------------------------------------------------------------


def check_coverage_entry(variables: dict[str, Any], *, kafka: KafkaPublisher | None = None) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.check_coverage` -> `check_coverage`."""
    del kafka  # unused — check_coverage emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return check_coverage(input_data)


def request_documents_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.request_documents` -> `request_documents`."""
    del kafka  # unused — request_documents emits no domain event itself (see docstring)
    beneficiario_pseudo_id = variables.get("beneficiario_pseudo_id", "")
    protocolo_reembolso = variables.get("protocolo_reembolso", "")
    message_type = variables.get("message_type", "pendencia_documentacao")
    return request_documents(beneficiario_pseudo_id, protocolo_reembolso, message_type)


def analyze_request_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.analyze_request` -> `analyze_request`."""
    del kafka  # unused — analyze_request emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return analyze_request(input_data)


def notify_sla_risk_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.notify_sla_risk` -> `notify_sla_risk`."""
    del kafka  # unused — notify_sla_risk emits no domain event itself
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    return notify_sla_risk(input_data)


def send_reembolso_denial_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.send_reembolso_denial` -> `send_reembolso_denial`
    (GUARDED).

    Raises `ReembolsoDenialNotHumanError` (fail-closed, ERR_REEMBOLSO_DENIAL_NOT_HUMAN) when the human
    decision
    (`decisao_reembolso` in {NEGAR, APROVAR_PARCIAL} + required fields) is missing.
    """
    del kafka  # unused — send_reembolso_denial emits no domain event itself
    denial_input = ReembolsoDenialInput(**pick_fields(variables, ReembolsoDenialInput))
    return send_reembolso_denial(denial_input)


def validate_auto_criteria_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    resolver: CeilingResolver | None = None,
    fonte_faixa_auto: FonteFatosFaixaAuto | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.validate_auto_criteria` -> `validate_auto_criteria`."""
    del kafka  # unused — validate_auto_criteria emits no domain event
    input_data = ReembolsoInput(**pick_fields(variables, ReembolsoInput))
    result = validate_auto_criteria(input_data, resolver=resolver, fonte_faixa_auto=fonte_faixa_auto)
    return dataclasses.asdict(result)


def register_auto_approval_entry(
    variables: dict[str, Any],
    *,
    kafka: KafkaPublisher | None = None,
    resolver: CeilingResolver | None = None,
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.register_auto_approval` -> `register_auto_approval`
    (GUARDED).

    `valor_aprovado_auto_cents` e `origem_pagamento` chegam LOCAIS a `ST_RegistrarAprovacaoAuto`
    (`camunda:inputParameter`, a partir de `auto_aprovacao.valor_aprovado_auto_cents` da DMN); nenhum dos
    dois e' lido de semente do escopo do processo.
    """
    del kafka  # unused — register_auto_approval emits no domain event
    return register_auto_approval(
        tenant_id=str(variables.get("tenant_id", "")),
        valor_aprovado_auto_cents=variables.get("valor_aprovado_auto_cents"),
        valor_solicitado_cents=variables.get("valor_solicitado_cents"),
        origem_pagamento=str(variables.get("origem_pagamento", "")),
        resolver=resolver,
    )


def publish_completed_entry(
    variables: dict[str, Any], *, kafka: KafkaPublisher | None = None
) -> dict[str, Any]:
    """Dict-boundary entry for `operadora.reembolso.publish_completed` -> `publish_completed`."""
    del kafka  # unused — see module bootstrap docstring on the Kafka seam
    event_type = variables.get("event_type", "reembolso.completed")
    payload = variables.get("payload") or {}
    desfecho = variables.get("desfecho", "")
    return publish_completed(event_type=event_type, payload=payload, desfecho=desfecho)


def register_reembolso_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-REEMBOLSO-001 dict-boundary entry functions on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) and threaded via `functools.partial`; no entry function
    calls `kafka.publish` today — see `ans_submit.register_ans_submit_workers`'s docstring for the same
    documented
    sync/async-boundary rationale.
    """
    # Faixa B (R-070 opcao B) — seams com nome proprio (`register_all_workers` espalha `**seams` para todo
    # modulo). Producao nao passa nenhum: a matriz REAL resolve o teto (0) e nao existe fonte de fatos, entao
    # a DMN manda todo pedido a um humano. Os testes injetam os dois para provar a faixa.
    resolver: CeilingResolver | None = seams.get("reembolso_ceiling_resolver")
    fonte_faixa_auto: FonteFatosFaixaAuto | None = seams.get("reembolso_fonte_faixa_auto")
    entries: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
        ("operadora.reembolso.check_coverage", check_coverage_entry),
        ("operadora.reembolso.request_documents", request_documents_entry),
        ("operadora.reembolso.analyze_request", analyze_request_entry),
        ("operadora.reembolso.notify_sla_risk", notify_sla_risk_entry),
        ("operadora.reembolso.send_reembolso_denial", send_reembolso_denial_entry),
        ("operadora.reembolso.publish_completed", publish_completed_entry),
    )
    for topic, entry in entries:
        harness.register_worker(FunctionWorker(topic, functools.partial(entry, kafka=kafka)))
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.validate_auto_criteria",
            functools.partial(
                validate_auto_criteria_entry,
                kafka=kafka,
                resolver=resolver,
                fonte_faixa_auto=fonte_faixa_auto,
            ),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.reembolso.register_auto_approval",
            functools.partial(register_auto_approval_entry, kafka=kafka, resolver=resolver),
        )
    )


__all__ = [
    "DIAS_NAO_CALCULAVEL",
    "ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO",
    "ERR_REEMBOLSO_INVALID_PROTOCOLO",
    "ORIGEM_AUTO_L2",
    "ORIGEM_HUMANA",
    "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO",
    "REEMBOLSO_BPMN_ERROR_ALLOWLIST",
    "VAR_ORIGEM_APROVACAO",
    "VAR_REEMBOLSO_AUTO_LIBERADO",
    "FatosFaixaAuto",
    "FonteFatosFaixaAuto",
    "ReembolsoAutoPagamentoBloqueadoError",
    "ReembolsoDenialInput",
    "ReembolsoDenialNotHumanError",
    "ReembolsoFaixaAutoResult",
    "ReembolsoInput",
    "analyze_request",
    "analyze_request_entry",
    "check_coverage",
    "check_coverage_entry",
    "notify_sla_risk",
    "notify_sla_risk_entry",
    "publish_completed",
    "publish_completed_entry",
    "register_auto_approval",
    "register_auto_approval_entry",
    "register_reembolso_workers",
    "request_documents",
    "request_documents_entry",
    "send_reembolso_denial",
    "send_reembolso_denial_entry",
    "validate_auto_criteria",
    "validate_auto_criteria_entry",
]
