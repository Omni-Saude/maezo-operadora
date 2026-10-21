"""`ConsentRecordPort` — o Maezo REGISTRA no lago da AMH a decisao de consentimento do beneficiario (DL-0083).

Decisao do dono (08/10/2026): o consentimento colhido no WhatsApp ("ACEITO"/"REVOGAR") e' gravado na AMH
pela operacao `consent-record` (`POST /interop/consent/v1/consents`, escopo `interop/consent.write`),
AINDA NAO PUBLICADA: o schema e' da AMH. O Maezo manda a versao do texto e o sha256 do texto EXATO que a
pessoa viu, o instante da decisao, o canal e uma chave de idempotencia deterministica; a AMH devolve a
referencia do registro (`consent_ref`) e se gravou agora (`recorded`; falso = ja' existia a mesma chave).

Escrita idempotente: repetir a mesma chave devolve o MESMO `consent_ref`. Sem retry dentro do port (e'
politica de quem chama).

PHI: so' a referencia opaca do sujeito, tokens fechados, hash do texto e carimbo de tempo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class ConsentRecordReceipt:
    consent_ref: str
    recorded: bool


@runtime_checkable
class ConsentRecordPort(Protocol):
    async def record(
        self,
        portable_subject_ref: str,
        *,
        amh_tenant: str,
        scope: str,
        decision: str,
        consent_text_version: str,
        consent_text_sha256: str,
        decided_at: str,
        channel: str,
        idempotency_key: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[ConsentRecordReceipt]: ...
