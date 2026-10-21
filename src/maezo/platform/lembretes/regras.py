"""Regras PURAS do lembrete: quem entra, qual competencia, e a janela de horario. Sem I/O, sem relogio.

O que decide que a pessoa recebe (todas valem; a primeira que falha da' o motivo):
  * o pagador e' a PROPRIA pessoa fisica (`pagador_tipo == pessoa_fisica`) e a cobranca nao e' da
    administradora de um coletivo por adesao. Pessoa juridica, adesao e `desconhecido` NUNCA recebem: o
    boleto e a mensalidade nao sao da pessoa;
  * ha' uma competencia `em_aberto` (nao paga, nao cancelada, nao vencida) com vencimento entre hoje e
    hoje + N dias, e com valor total na forma do contrato.

Ficam de fora daqui (sao do `job`): acesso verificado e consentimento, opt-out, supressao, ja' lembrado,
allowlist, janela de 24 h da Meta, limite por execucao.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Final

from maezo.agents.lucas.fonte_cobranca import FatosCobranca

HORA_INICIO: Final[int] = 8
HORA_FIM: Final[int] = 20  # exclusivo: 19:59 ainda envia, 20:00 nao

MOTIVO_PAGADOR_NAO_E_PESSOA_FISICA: Final[str] = "pagador_nao_e_pessoa_fisica"
MOTIVO_COBRANCA_DA_ADMINISTRADORA: Final[str] = "cobranca_da_administradora"
MOTIVO_SEM_COMPETENCIA_NA_JANELA: Final[str] = "sem_competencia_na_janela"


def fuso_do_brasil() -> tzinfo:
    """America/Sao_Paulo. Sem horario de verao desde 2019, entao UTC-3 fixo e' o mesmo resultado quando a
    imagem nao traz o banco de fusos (python:slim sem `tzdata`)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Sao_Paulo")
    except Exception:
        return timezone(timedelta(hours=-3), "America/Sao_Paulo")


def hoje_no_brasil(agora: datetime) -> date:
    return agora.astimezone(fuso_do_brasil()).date()


def dentro_do_horario(agora: datetime) -> bool:
    """Horario de silencio: so' entre 08:00 e 20:00 (America/Sao_Paulo)."""
    hora = agora.astimezone(fuso_do_brasil()).hour
    return HORA_INICIO <= hora < HORA_FIM


@dataclass(frozen=True, slots=True)
class CompetenciaEscolhida:
    competencia: str
    valor_total: str
    vencimento: date


def _data(iso: str | None) -> date | None:
    if iso is None:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


def escolher_competencia(
    fatos: FatosCobranca, *, hoje: date, dias_antecedencia: int
) -> tuple[CompetenciaEscolhida | None, str | None]:
    """`(competencia, None)` quando ha' o que lembrar, ou `(None, motivo)`.

    Varias competencias na janela: a de vencimento mais proximo (as outras ficam para as proximas
    execucoes, uma mensagem por pessoa por vez).
    """
    if fatos.cobranca_administradora:
        return None, MOTIVO_COBRANCA_DA_ADMINISTRADORA
    if fatos.pagador_tipo != "pessoa_fisica":
        return None, MOTIVO_PAGADOR_NAO_E_PESSOA_FISICA
    limite = hoje + timedelta(days=dias_antecedencia)
    candidatas: list[CompetenciaEscolhida] = []
    for c in fatos.competencias:
        venc = _data(c.vencimento)
        if c.situacao != "em_aberto" or venc is None or c.valor_total is None:
            continue
        if hoje <= venc <= limite:
            candidatas.append(CompetenciaEscolhida(c.competencia, c.valor_total, venc))
    if not candidatas:
        return None, MOTIVO_SEM_COMPETENCIA_NA_JANELA
    return min(candidatas, key=lambda x: (x.vencimento, x.competencia)), None


def agora_utc() -> datetime:
    return datetime.now(UTC)
