"""Cancelamento a pedido do beneficiario PESSOA FISICA (SP-OP-CANCEL-001 v0.2.0, DL-0111).

Atras da chave `MAEZO_CANCEL_PF_V1` (default desligada; ligada so' no dev). Desligada, NADA deste modulo
roda: o Lucas e' o de antes byte a byte. Aprovado pelo dono em 15/10/2026 para uso em dev; revisao
juridica/regulatoria (RN 593/RN 412), DPO e operacao antes de producao.

O QUE FICA AQUI, num lugar so':
  * os FATOS do cancelamento, apurados dos contratos AMH JA' pinados (nenhum campo novo da AMH):
    `tina` elegibilidade (`titular`, `tipo_contratacao`, `cancelamento`, `situacao_beneficiario`) e o
    `resumo.tipo_contratacao` do `billing-status` 0.3.0. Fato, nunca veredito: quem decide e' a DMN
    `cancel_admissibility` e as pessoas das User Tasks;
  * a fonte que le esses fatos (`FonteCancelamentoAmh`), que nunca levanta por falha da dependencia;
  * o PROTOCOLO (identificador curto e estavel da instancia, sem PII);
  * os TEXTOS ao beneficiario — aprovados pelo dono para dev em 15/10/2026; revisao juridica antes de
    producao. Curtos (<= 20 palavras por frase, regua de tom do repo) e nunca prometem o que nao aconteceu.

O Maezo nao grava no Tasy (ADR-0013): "cancelado" so' sai depois que a pessoa do cadastro concluiu a
tarefa de execucao como EXECUTADO (o BPMN publica o aviso e a retomada confere o historico do motor).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal, Protocol, runtime_checkable

from maezo.ports.tina import ElegibilidadeView, TinaPort, VinculoPlano
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

#: O nome da chave (env do receptor e do simulador; Terraform `cancel_pf_v1`).
CHAVE_CANCEL_PF_V1: Final[str] = "MAEZO_CANCEL_PF_V1"

# ------------------------------------------------------------------------------------------------
# Resultados do aviso (o `resultado` que o BPMN publica em `agents.events.process_completed`)
# ------------------------------------------------------------------------------------------------
RESULTADO_EXECUTADO: Final[str] = "cancelamento_executado"
RESULTADO_INFO_SOLICITADA: Final[str] = "cancelamento_info_solicitada"
RESULTADO_MANTIDO: Final[str] = "cancelamento_mantido"
RESULTADOS_DO_CANCELAMENTO: Final[frozenset[str]] = frozenset(
    {RESULTADO_EXECUTADO, RESULTADO_INFO_SOLICITADA, RESULTADO_MANTIDO}
)

# ------------------------------------------------------------------------------------------------
# Protocolo
# ------------------------------------------------------------------------------------------------
_SEMENTE_PROTOCOLO: Final[str] = "maezo-cancel-protocolo/v1"
_PROTOCOLO: Final[re.Pattern[str]] = re.compile(r"^CAN-[0-9A-F]{4}-[0-9A-F]{4}$")


def protocolo_do_cancelamento(instance_id: str) -> str:
    """`CAN-XXXX-XXXX`, derivado do id da instancia do motor. Estavel (o mesmo pedido, o mesmo protocolo
    — inclusive no pedido repetido, que reencontra a instancia viva), curto e sem PII: e' um hash do id
    tecnico, nunca a chave de negocio nem o pseudonimo. A equipe acha o caso pelo id da instancia."""
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise ValueError("protocolo_do_cancelamento: instance_id ausente")
    digest = hashlib.sha256(f"{_SEMENTE_PROTOCOLO}:{instance_id.strip()}".encode()).hexdigest().upper()
    return f"CAN-{digest[:4]}-{digest[4:8]}"


def protocolo_valido(valor: object) -> bool:
    return isinstance(valor, str) and _PROTOCOLO.match(valor) is not None


# ------------------------------------------------------------------------------------------------
# Textos (aprovado pelo dono para dev em 15/10/2026; revisao juridica antes de producao)
# ------------------------------------------------------------------------------------------------
#: No inicio (o processo nasceu): o que acontece a seguir. NUNCA "cancelado".
TEXTO_PEDIDO_RECEBIDO: Final[str] = (
    "Recebemos o seu pedido de cancelamento. Protocolo: {protocolo}. "
    "A nossa equipe vai concluir o pedido. Quando terminar, você recebe a confirmação."
)
#: Pedido repetido (a instancia ja' estava viva): o mesmo protocolo.
TEXTO_PEDIDO_JA_REGISTRADO: Final[str] = (
    "O seu pedido de cancelamento já está com a nossa equipe. Protocolo: {protocolo}. "
    "Quando terminar, você recebe a confirmação."
)
#: Dependente pedindo: o pedido e' do titular. Nenhum processo nasce.
TEXTO_DEPENDENTE: Final[str] = (
    "O pedido de cancelamento do plano é feito pelo titular. Se quiser, posso te passar para alguém da "
    "nossa equipe."
)
TEXTO_DEPENDENTE_CURTO: Final[str] = "Quem pede o cancelamento é o titular do plano."
#: Recuo com a confirmacao pendente (so' com a chave ligada): nenhum pedido registrado e a porta
#: aberta para retomar. Sem pressao, sem retencao, sem desconto.
TEXTO_PEDIDO_NAO_REGISTRADO: Final[str] = (
    "Tudo bem, não registrei nenhum pedido de cancelamento. Se mudar de ideia, é só me avisar."
)
#: Retorno: a pessoa do cadastro concluiu "cancelamento executado no sistema" (historico conferido).
TEXTO_CANCELADO: Final[str] = (
    "Seu plano foi cancelado a partir de {data}. Protocolo: {protocolo}. "
    "Se precisar de algo, é só responder esta mensagem."
)
#: Retorno: a equipe pediu mais informacao (o caso segue aberto).
TEXTO_INFO_SOLICITADA: Final[str] = (
    "Sobre o seu pedido de cancelamento (protocolo {protocolo}): a nossa equipe precisa de mais informações. "
    "Se quiser falar com uma pessoa da equipe, é só pedir aqui."
)
#: Retorno: a equipe decidiu manter o plano. Sem motivo no texto; a pessoa explica.
TEXTO_MANTIDO: Final[str] = (
    "Sobre o seu pedido de cancelamento (protocolo {protocolo}): a nossa equipe analisou e o seu plano "
    "continua ativo. Se quiser entender, é só pedir aqui para falar com uma pessoa da equipe."
)

#: Versao dos textos acima (auditoria; sobe a cada mudanca de redacao).
TEXTOS_CANCEL_PF_VERSAO: Final[str] = "cancel-pf-v1"


def data_br(iso: object) -> str | None:
    """`AAAA-MM-DD` -> `DD/MM/AAAA`; qualquer outra forma -> `None` (nunca se inventa data)."""
    if not isinstance(iso, str):
        return None
    try:
        dia = date.fromisoformat(iso.strip())
    except ValueError:
        return None
    if len(iso.strip()) != 10:
        return None
    return dia.strftime("%d/%m/%Y")


def texto_do_retorno(resultado: str, *, protocolo: str, data_efeito_iso: object = None) -> str:
    """O texto fixo de cada retorno. Levanta `ValueError` quando falta o fato que o texto afirma (data de
    efeito do EXECUTADO, protocolo): quem chama nao envia nada em vez de inventar."""
    if not protocolo_valido(protocolo):
        raise ValueError("texto_do_retorno: protocolo fora da forma")
    if resultado == RESULTADO_EXECUTADO:
        data = data_br(data_efeito_iso)
        if data is None:
            raise ValueError("texto_do_retorno: cancelamento executado sem data de efeito valida")
        return TEXTO_CANCELADO.format(data=data, protocolo=protocolo)
    if resultado == RESULTADO_INFO_SOLICITADA:
        return TEXTO_INFO_SOLICITADA.format(protocolo=protocolo)
    if resultado == RESULTADO_MANTIDO:
        return TEXTO_MANTIDO.format(protocolo=protocolo)
    raise ValueError("texto_do_retorno: resultado fora do contrato")


# ------------------------------------------------------------------------------------------------
# Fatos do cancelamento
# ------------------------------------------------------------------------------------------------
TipoPlano = Literal["individual", "familiar", "coletivo_empresarial", "coletivo_adesao"]
TIPOS_PLANO_PF: Final[frozenset[str]] = frozenset({"individual", "familiar"})
TIPOS_PLANO: Final[frozenset[str]] = frozenset(
    {"individual", "familiar", "coletivo_empresarial", "coletivo_adesao"}
)

#: `situacao_beneficiario` que conta como vinculo ATIVO. Qualquer outra (ou ausente) nao afirma vinculo
#: ativo: o pedido vai a analise humana (catch-all da DMN), nunca e' negado.
_SITUACOES_ATIVAS: Final[tuple[str, ...]] = ("apto", "ativo")


def _plano(texto: object) -> str:
    base = unicodedata.normalize("NFKD", texto if isinstance(texto, str) else "")
    return "".join(c for c in base if not unicodedata.combining(c)).casefold().strip()


@dataclass(frozen=True, slots=True)
class FatosCancelamento:
    """Os fatos que o Lucas leva a SP-OP-CANCEL-001. `None` = a fonte nao afirma (nunca `False` por omissao
    no lugar de "nao sei", exceto onde o contrato do processo exige booleano — ali vira `False`)."""

    titular: bool | None = None
    tipo_plano: str | None = None
    vinculo_ativo: bool | None = None
    identidade_verificada: bool = False

    def como_entrada_lucas(self) -> dict[str, Any]:
        """As chaves de entrada do Lucas (`_CALLER_INPUT_FIELDS`). So' nascem as que a fonte afirmou."""
        entrada: dict[str, Any] = {"cancelamento_identidade_verificada": self.identidade_verificada is True}
        if isinstance(self.titular, bool):
            entrada["cancelamento_titular"] = self.titular
        if self.tipo_plano in TIPOS_PLANO:
            entrada["cancelamento_tipo_plano"] = self.tipo_plano
        if isinstance(self.vinculo_ativo, bool):
            entrada["cancelamento_vinculo_ativo"] = self.vinculo_ativo
        return entrada


def tipo_plano_do_billing(tipo_contratacao: object) -> str | None:
    """`resumo.tipo_contratacao` (billing-status 0.3.0) -> `tipo_plano` do contrato do processo.

    `individual_familiar` vira `individual`: a AMH nao distingue os dois e a DMN `cancel_admissibility`
    os trata igual (r_pedido_l2). `desconhecido`/ausente/fora do enum -> `None`."""
    if tipo_contratacao == "individual_familiar":
        return "individual"
    if tipo_contratacao in ("coletivo_empresarial", "coletivo_adesao"):
        return str(tipo_contratacao)
    return None


def tipo_plano_do_texto(tipo_contratacao: object) -> str | None:
    """O `tipo_contratacao` do vinculo TINA (texto do Tasy, ex. "Coletivo por adesao") -> `tipo_plano`.
    Reserva, so' quando o billing-status nao disse. Texto que nao casa -> `None`."""
    texto = _plano(tipo_contratacao)
    if not texto:
        return None
    if "ades" in texto:
        return "coletivo_adesao"
    if "empresa" in texto:
        return "coletivo_empresarial"
    if "individual" in texto:
        return "individual"
    if "familiar" in texto:
        return "familiar"
    return None


def _unico(valores: Sequence[Any]) -> Any:
    """O valor comum a todos (ignorando `None`), ou `None` quando ha' divergencia ou nenhum."""
    presentes = {v for v in valores if v is not None}
    return presentes.pop() if len(presentes) == 1 else None


def _situacao_ativa(vinculo: VinculoPlano) -> bool | None:
    texto = _plano(vinculo.situacao_vinculo)
    if not texto:
        return None
    return any(texto.startswith(s) for s in _SITUACOES_ATIVAS)


def derivar_fatos(
    view: ElegibilidadeView | None,
    *,
    tipo_contratacao_billing: object = None,
    identidade_verificada: bool = False,
) -> FatosCancelamento:
    """Os fatos a partir do que a AMH entregou. PURA. Varios vinculos ATIVOS que divergem -> `None` no
    campo divergente (o pedido vai a analise humana; o Lucas nao escolhe um plano por conta propria)."""
    tipo = tipo_plano_do_billing(tipo_contratacao_billing)
    if view is None:
        return FatosCancelamento(tipo_plano=tipo, identidade_verificada=identidade_verificada)
    ativos = [v for v in view.vinculos if not v.cancelamento]
    titular = _unico([v.titular for v in ativos]) if ativos else None
    if titular is not None and any(v.titular is None for v in ativos):
        titular = None  # um vinculo ativo sem o fato: nao se afirma pelo outro
    if tipo is None and ativos:
        tipo = _unico([tipo_plano_do_texto(v.tipo_contratacao) for v in ativos])
    if not view.ativo or not ativos:
        vinculo_ativo: bool | None = False
    else:
        situacoes = [_situacao_ativa(v) for v in ativos]
        vinculo_ativo = True if all(s is True for s in situacoes) else (None if None in situacoes else False)
    return FatosCancelamento(
        titular=titular if isinstance(titular, bool) else None,
        tipo_plano=tipo,
        vinculo_ativo=vinculo_ativo,
        identidade_verificada=identidade_verificada,
    )


@dataclass(frozen=True, slots=True)
class FatosIndisponiveis:
    """A fonte nao respondeu. `motivo` e' token de classe, nunca texto livre."""

    motivo: str


@runtime_checkable
class FonteCancelamento(Protocol):
    """Porta dos fatos do cancelamento. Nunca levanta por falha da dependencia."""

    async def fatos(
        self, pseudo_id: str, *, phone_hash: str | None, tipo_contratacao_billing: object = None
    ) -> FatosCancelamento | FatosIndisponiveis: ...


class _Resolvedor(Protocol):
    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None: ...


class _Consentimento(Protocol):
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None: ...


#: Prazo da leitura de elegibilidade (o mesmo das consultas do plano da Helena).
PRAZO_TINA_S: Final[float] = 5.0


class FonteCancelamentoAmh:
    """Elegibilidade TINA do sujeito resolvido, pelo MESMO resolvedor da cobranca do Lucas. A identidade so'
    conta como verificada com o resolvedor do acesso (`identidade_verificada=True`, DL-0083)."""

    def __init__(
        self,
        *,
        tina: TinaPort,
        resolvedor: _Resolvedor,
        consentimento: _Consentimento,
        purpose_of_use: str,
        timeout_seconds: float = PRAZO_TINA_S,
    ) -> None:
        if not purpose_of_use:
            raise ValueError("fonte_cancelamento_amh: finalidade ausente")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("fonte_cancelamento_amh: timeout_seconds invalido")
        self._tina = tina
        self._resolvedor = resolvedor
        self._consentimento = consentimento
        self._purpose = purpose_of_use
        self._timeout = float(timeout_seconds)
        self._verificada = getattr(resolvedor, "identidade_verificada", False) is True

    async def fatos(
        self, pseudo_id: str, *, phone_hash: str | None, tipo_contratacao_billing: object = None
    ) -> FatosCancelamento | FatosIndisponiveis:
        if not pseudo_id:
            return FatosIndisponiveis("pseudo_id_ausente")
        try:
            ref = await self._resolvedor.portable_ref(pseudo_id, phone_hash=phone_hash)
            if not ref:
                return FatosIndisponiveis("sujeito_nao_resolvido")
            base = await self._consentimento.decisao(ref, self._purpose)
            if not base:
                return FatosIndisponiveis("sem_base_legal")
            leitura = await self._tina.get_elegibilidade(
                ref,
                purpose_of_use=self._purpose,
                consent_decision_ref=base,
                timeout_seconds=self._timeout,
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return FatosIndisponiveis("fonte_indisponivel")
        view = leitura.value if leitura.succeeded else None
        if view is None or view.portable_subject_ref != ref:
            return FatosIndisponiveis("fonte_indisponivel")
        return derivar_fatos(
            view, tipo_contratacao_billing=tipo_contratacao_billing, identidade_verificada=self._verificada
        )


__all__ = [
    "CHAVE_CANCEL_PF_V1",
    "RESULTADOS_DO_CANCELAMENTO",
    "RESULTADO_EXECUTADO",
    "RESULTADO_INFO_SOLICITADA",
    "RESULTADO_MANTIDO",
    "TEXTOS_CANCEL_PF_VERSAO",
    "TEXTO_CANCELADO",
    "TEXTO_DEPENDENTE",
    "TEXTO_DEPENDENTE_CURTO",
    "TEXTO_INFO_SOLICITADA",
    "TEXTO_MANTIDO",
    "TEXTO_PEDIDO_JA_REGISTRADO",
    "TEXTO_PEDIDO_NAO_REGISTRADO",
    "TEXTO_PEDIDO_RECEBIDO",
    "TIPOS_PLANO_PF",
    "FatosCancelamento",
    "FatosIndisponiveis",
    "FonteCancelamento",
    "FonteCancelamentoAmh",
    "data_br",
    "derivar_fatos",
    "protocolo_do_cancelamento",
    "protocolo_valido",
    "texto_do_retorno",
    "tipo_plano_do_billing",
    "tipo_plano_do_texto",
]
