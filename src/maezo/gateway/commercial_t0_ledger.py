"""Registro append-only dos eventos-candidatos ao inicio do relogio de contratacao (E-SF-07, EP-REG-32a).

Base normativa (so' referencia, sem copia): `docs/compliance/legal/textos-base/RN-413-2016.md`, Art. 5º
(caput e §§2º, 5º, 6º e 7º).

O que guarda, por proponente: o TIPO do evento e o INSTANTE observado no servidor. Zero conteudo: nem
valores do formulario, nem dado de saude, nem a propria referencia do proponente (guarda-se so' o digest
SHA-256 dela, porque a referencia do contrato comercial e' texto livre). Qual evento e' o inicio LEGAL do
prazo nao e' decidido aqui (depende de ratificacao juridica): o registro so' guarda os candidatos.

Append-only: o primeiro evento de cada tipo "de inicio" fica; repeti-lo nao o substitui, e tentar gravar
outro instante para o mesmo par (proponente, tipo) recusa com `t0_ledger_immutable`. Suspensoes (pedido de
documentacao, pericia ou entrevista) ficam registradas com a causa em classe fechada; NAO pausam nada.

Nada montado: este modulo nao e' importado pelos comandos de admissao, nao tem migracao e nao tem
persistencia de producao. `RegistroEmMemoriaSoParaTeste` existe so' para os testes; persistencia duravel,
categoria de retencao e ligacao aos comandos sao linha propria.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from maezo.domain.rn413_contracting_clock import (
    TIPOS_DE_EVENTO,
    TIPOS_DE_INICIO,
    TIPOS_DE_SUSPENSAO,
    EventoCandidato,
    TipoDeEvento,
)

__all__ = [
    "TIPOS_DE_EVENTO",
    "TIPOS_DE_INICIO",
    "TIPOS_DE_SUSPENSAO",
    "EventoCandidato",
    "PersistenciaDoRegistro",
    "RegistroEmMemoriaSoParaTeste",
    "T0LedgerRecusado",
    "TipoDeEvento",
    "digest_do_proponente",
    "registrar_evento_candidato",
]


class T0LedgerRecusado(ValueError):  # noqa: N818
    """Recusa com codigo fixo; nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def digest_do_proponente(prospect_ref: object) -> str:
    """Digest SHA-256 da referencia do proponente. A referencia em si nunca e' guardada."""
    if type(prospect_ref) is not str or not prospect_ref or len(prospect_ref) > 128:
        raise T0LedgerRecusado("t0_ledger_ref_invalida")
    return hashlib.sha256(prospect_ref.encode("utf-8")).hexdigest()


def _validar(evento: object) -> EventoCandidato:
    if type(evento) is not EventoCandidato:
        raise T0LedgerRecusado("t0_ledger_evento_invalido")
    d = evento.proponente_digest
    if type(d) is not str or len(d) != 64 or any(c not in "0123456789abcdef" for c in d):
        raise T0LedgerRecusado("t0_ledger_evento_invalido")
    if type(evento.tipo) is not str or evento.tipo not in TIPOS_DE_EVENTO:
        raise T0LedgerRecusado("t0_ledger_evento_invalido")
    instante = evento.instante
    if type(instante) is not datetime or instante.utcoffset() != timedelta(0):
        raise T0LedgerRecusado("t0_ledger_evento_invalido")
    return evento


class PersistenciaDoRegistro(Protocol):
    """Porta de persistencia. Nenhuma implementacao de producao existe (linha propria, com migracao)."""

    def anexar(self, evento: EventoCandidato) -> None: ...

    def eventos(self, proponente_digest: str) -> tuple[EventoCandidato, ...]: ...


class RegistroEmMemoriaSoParaTeste:
    """Implementacao em memoria SO' para teste: append-only, sem atualizacao nem remocao."""

    def __init__(self) -> None:
        self._eventos: list[EventoCandidato] = []

    def anexar(self, evento: EventoCandidato) -> None:
        evento = _validar(evento)
        if evento.tipo in TIPOS_DE_INICIO:
            for anterior in self._eventos:
                if (anterior.proponente_digest, anterior.tipo) == (evento.proponente_digest, evento.tipo):
                    if anterior.instante != evento.instante:
                        raise T0LedgerRecusado("t0_ledger_immutable")
                    return  # mesma entrega repetida: idempotente
        self._eventos.append(evento)

    def eventos(self, proponente_digest: str) -> tuple[EventoCandidato, ...]:
        return tuple(e for e in self._eventos if e.proponente_digest == proponente_digest)


def registrar_evento_candidato(
    persistencia: PersistenciaDoRegistro,
    *,
    prospect_ref: str,
    tipo: str,
    relogio_do_servidor: Callable[[], datetime],
) -> EventoCandidato:
    """Grava o evento com o instante do servidor. Para tipo de inicio ja' registrado, devolve o PRIMEIRO."""
    digest = digest_do_proponente(prospect_ref)
    if type(tipo) is not str or tipo not in TIPOS_DE_EVENTO:
        raise T0LedgerRecusado("t0_ledger_evento_invalido")
    if tipo in TIPOS_DE_INICIO:
        for anterior in persistencia.eventos(digest):
            if anterior.tipo == tipo:
                return anterior  # o primeiro fica; o novo nao o substitui
    evento = _validar(EventoCandidato(proponente_digest=digest, tipo=tipo, instante=relogio_do_servidor()))
    persistencia.anexar(evento)
    return evento
