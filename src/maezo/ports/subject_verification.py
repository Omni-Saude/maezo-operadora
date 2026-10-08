"""`SubjectVerificationPort` — a AMH CONFERE se o fator digitado e' de uma pessoa JA' candidata (DL-0083).

Decisao do dono (08/10/2026): antes de qualquer agente, quem escreve prova que e' o beneficiario com o CPF
(e, quando o telefone e' de mais de uma pessoa, tambem o nascimento). O Maezo NUNCA manda o CPF nem a data:
manda so' o HASH do fator (`amh-subject-verify-v1`, HMAC-SHA256 com chave DEDICADA).

CONFERE, NAO RESOLVE (revisao de seguranca do PR #700, contrato coordenado com a AMH #212). O Maezo SEMPRE
manda a referencia CANDIDATA (a que o telefone ja' resolveu) e a AMH responde so' um booleano: o fator
confere com ESSA pessoa ou nao. A AMH nunca devolve uma referencia que o Maezo nao mandou — um CPF digitado
nunca "descobre" quem e' a pessoa, e um telefone desconhecido nunca entra pelo caminho do documento.

Os tipos espelham o contrato `subject-verification` PROPOSTO pela AMH (`POST
/interop/identity/v1/subjects/{ref}/verify`, escopo `interop/subject.verify`), AINDA NAO PUBLICADO; o
schema e' da AMH.

PHI: nada aqui carrega CPF, nascimento, nome ou telefone — so' o hash do fator e a referencia opaca.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class SubjectVerification:
    """O veredito da AMH para a referencia PERGUNTADA (o eco tem de ser a mesma)."""

    portable_subject_ref: str
    confere: bool


@runtime_checkable
class SubjectVerificationPort(Protocol):
    """Conferencia por fator (consulta). Sucesso ou recusa fechada, nunca excecao de politica."""

    async def verify(
        self,
        portable_subject_ref: str,
        *,
        fator: str,
        verification_hash: str,
        hash_scheme: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectVerification]: ...
