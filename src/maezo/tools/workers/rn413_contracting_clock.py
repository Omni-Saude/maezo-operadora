"""Relogio-sombra da contratacao eletronica (E-SF-07: TB-E-09-a, EP-REG-32b, EP-REG-23c, TB-E-14-a).

Base normativa (so' referencia, sem copia): `docs/compliance/legal/textos-base/RN-413-2016.md`, Art. 5º
(§§2º, 5º, 6º e 7º).

UM relogio so': funcao pura sobre o registro de eventos-candidatos (`commercial_t0_ledger`). Sem timer
proprio, sem BPMN, sem DMN. NAO e' registrado no runtime de workers (nada montado).

* Inicio: o evento-candidato MAIS CEDO com dados. Qual evento e' o inicio legal nao e' decidido aqui.
* Contagem em dias corridos, SEM pausa: suspensoes ficam registradas, mas nao descontam nada ate' haver
  ratificacao; lembrete de recuperacao nunca suspende.
* Alarmes internos viram item de escalonamento para PESSOA, com grupo responsavel obrigatorio.
* No dia de corte, o caso ganha a marca `possivel_vigencia_automatica`: e' uma HIPOTESE que pede pessoa,
  nao uma conclusao juridica. Nada aqui afirma que um contrato esta' vigente.
* Nunca comando adverso: nada rescinde, nega, cobra ou bloqueia. Nenhuma data-limite e' exibida a cliente.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal

from maezo.gateway.commercial_t0_ledger import TIPOS_DE_INICIO, TIPOS_DE_SUSPENSAO, EventoCandidato

# --- Limiares: operacional, relogio-sombra, NAO e' prazo legal. Unico lugar destes numeros. -------------
#: Alarmes internos (dias corridos desde o inicio do relogio-sombra).
DIA_ALARME_1: Final[int] = 15
DIA_ALARME_2: Final[int] = 20
DIA_ALARME_3: Final[int] = 23
#: Corte operacional: a partir dele o caso vai a pessoa com a hipotese `possivel_vigencia_automatica`.
DIA_CORTE: Final[int] = 25
DIAS_DE_ALARME: Final[tuple[int, ...]] = (DIA_ALARME_1, DIA_ALARME_2, DIA_ALARME_3)
#: A partir deste dia a recuperacao de abandono fica bloqueada (so' leitura para a E-SF-16).
DIA_BLOQUEIO_DA_RECUPERACAO: Final[int] = DIA_ALARME_2

#: Estagios da contratacao que tem teto nomeado (o alarme de estouro so' existe para eles).
ESTAGIOS_COM_TETO: Final[frozenset[str]] = frozenset({"cadastro", "aceite"})

_ORIGEM_CANONICA: Final = object()


class RelogioRecusado(ValueError):  # noqa: N818
    """Recusa com codigo fixo; nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EstadoDoRelogio:
    """Estado interno (staff-only). Sem campo de data-limite: nada daqui e' exibivel a cliente."""

    proponente_digest: str
    inicio: datetime | None
    dias_corridos: int | None
    alarmes_atingidos: tuple[int, ...]
    possivel_vigencia_automatica: bool
    recuperacao_bloqueada: bool
    suspensoes_registradas: int
    _origem: object = field(repr=False, compare=False)


def estado_do_relogio(
    eventos: Sequence[EventoCandidato], *, proponente_digest: str, agora: datetime
) -> EstadoDoRelogio:
    """O relogio canonico. Sem evento de inicio, nao ha' contagem (estado sem inicio, nunca inventado)."""
    if type(agora) is not datetime or agora.utcoffset() != timedelta(0):
        raise RelogioRecusado("relogio_instante_invalido")
    proprios = [e for e in eventos if type(e) is EventoCandidato and e.proponente_digest == proponente_digest]
    inicios = [e.instante for e in proprios if e.tipo in TIPOS_DE_INICIO]
    suspensoes = sum(1 for e in proprios if e.tipo in TIPOS_DE_SUSPENSAO)
    if not inicios:
        return EstadoDoRelogio(proponente_digest, None, None, (), False, False, suspensoes, _ORIGEM_CANONICA)
    inicio = min(inicios)
    dias = (agora.date() - inicio.date()).days
    return EstadoDoRelogio(
        proponente_digest=proponente_digest,
        inicio=inicio,
        dias_corridos=dias,
        alarmes_atingidos=tuple(d for d in DIAS_DE_ALARME if dias >= d),
        possivel_vigencia_automatica=dias >= DIA_CORTE,
        recuperacao_bloqueada=dias >= DIA_BLOQUEIO_DA_RECUPERACAO,
        suspensoes_registradas=suspensoes,
        _origem=_ORIGEM_CANONICA,
    )


def exigir_relogio_canonico(estado: object) -> EstadoDoRelogio:
    """Todo consumidor le' so' o estado deste relogio; estado montado por outro lugar e' recusado."""
    if type(estado) is not EstadoDoRelogio or estado._origem is not _ORIGEM_CANONICA:
        raise RelogioRecusado("SECOND_CLOCK_FORBIDDEN")
    return estado


@dataclass(frozen=True, slots=True)
class ItemDeEscalonamento:
    """Pedido de atencao humana. Nao e' comando: nao rescinde, nao nega, nao cobra, nao bloqueia."""

    proponente_digest: str
    motivo: Literal["alarme_relogio_sombra", "possivel_vigencia_automatica"]
    dia: int
    grupo_responsavel: str
    estagio: str
    acao: Literal["escalation_requested"] = "escalation_requested"


def _papel(valor: object) -> str:
    if type(valor) is not str or not valor or len(valor) > 64 or not valor.replace("_", "").isalnum():
        raise RelogioRecusado("RESPONSIBLE_QUEUE_REQUIRED")
    if not valor.isascii() or valor != valor.lower():
        raise RelogioRecusado("RESPONSIBLE_QUEUE_REQUIRED")
    return valor


def itens_de_escalonamento(
    estado: EstadoDoRelogio, *, estagio: str, grupo_responsavel: str
) -> tuple[ItemDeEscalonamento, ...]:
    """Um item por alarme atingido e, no corte, o item da hipotese. Sem inicio, nenhum item (sem contagem)."""
    estado = exigir_relogio_canonico(estado)
    if type(estagio) is not str or estagio not in ESTAGIOS_COM_TETO:
        raise RelogioRecusado("OVERRUN_WITHOUT_NAMED_CEILING")
    grupo = _papel(grupo_responsavel)
    if estado.dias_corridos is None:
        return ()
    itens = [
        ItemDeEscalonamento(estado.proponente_digest, "alarme_relogio_sombra", d, grupo, estagio)
        for d in estado.alarmes_atingidos
    ]
    if estado.possivel_vigencia_automatica:
        itens.append(
            ItemDeEscalonamento(
                estado.proponente_digest, "possivel_vigencia_automatica", DIA_CORTE, grupo, estagio
            )
        )
    return tuple(itens)
