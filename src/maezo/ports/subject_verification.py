"""`SubjectVerificationPort` — a AMH confere se quem escreve e' o beneficiario, por HASH de fator (DL-0083).

Decisao do dono (08/10/2026): antes de qualquer agente, quem escreve prova que e' o beneficiario com o CPF
(e, quando o telefone nao identifica uma pessoa so', tambem o nascimento). O Maezo NUNCA manda o CPF nem a
data: manda so' o HASH do fator (`amh-subject-verify-v1`, HMAC-SHA256 com chave DEDICADA), e a AMH responde
se ha' correspondencia e, quando ha', o `portable_subject_ref` opaco. Quem liga o fator a uma pessoa e' so' a
AMH (mesma regra do telefone, ADR-0037 XRD-05).

Os tipos espelham o contrato `subject-verification` PROPOSTO pela AMH (`POST
/interop/subject-verification/v1/subjects/verify`, escopo `interop/subject.verify`), AINDA NAO PUBLICADO; o
schema e' da AMH. A resposta e' `verificado` (sim/nao) e a referencia opaca so' quando `verificado`.

PHI: nada aqui carrega CPF, nascimento, nome ou telefone — so' o hash do fator e a referencia opaca.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class SubjectVerification:
    """O veredito da AMH. `portable_subject_ref` so' existe quando `verificado` e' verdadeiro."""

    verificado: bool
    portable_subject_ref: str | None


@runtime_checkable
class SubjectVerificationPort(Protocol):
    """Verificacao por fator (consulta). Sucesso ou recusa fechada, nunca excecao de politica."""

    async def verify(
        self,
        verification_hash: str,
        *,
        amh_tenant: str,
        hash_scheme: str,
        factor: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectVerification]: ...
