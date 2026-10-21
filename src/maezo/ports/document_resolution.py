"""`DocumentResolutionPort` — a AMH RESOLVE pelo DOCUMENTO quando o telefone nao e' de ninguem (DL-0084).

Decisao do dono (Leonardo, 08/10/2026), RISCO ACEITO contra o alerta da revisao de seguranca do PR #700: um
numero FORA do cadastro (telefone sem candidato na AMH) pode se identificar com CPF + data de nascimento. O
Maezo manda os DOIS hashes (`amh-subject-verify-v1`, mesma chave dedicada da conferencia: o do CPF e o do
CPF + nascimento) e a AMH responde `unico` (com a referencia) ou `nenhum`. Na AMH, 5 falhas do mesmo CPF em
24 h bloqueiam o CPF (a resposta segue `nenhum`) e 429 e' o teto de 60/min por cliente; o Maezo aplica as
regras de tentativas/bloqueio/janela de 24 h do acesso.

Os tipos espelham o contrato `POST /interop/identity/v1/resolve-by-document` PEDIDO a AMH (em construcao
sobre o #212), AINDA NAO PUBLICADO; o schema e' da AMH. Os nomes dos parametros aqui sao do Maezo
(`document_hash`, `document_dob_hash`); o adaptador faz o mapeamento para os nomes do contrato.

PHI: nada aqui carrega CPF, nascimento, nome ou telefone — so' hashes com chave e a referencia opaca.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class DocumentResolution:
    """`resultado` e' `unico` (com `portable_subject_ref`) ou `nenhum` (sem referencia)."""

    resultado: str
    portable_subject_ref: str | None = None


@runtime_checkable
class DocumentResolutionPort(Protocol):
    """Resolucao pelo documento (consulta). Sucesso ou recusa fechada, nunca excecao de politica."""

    async def resolve_by_document(
        self,
        *,
        document_hash: str,
        document_dob_hash: str,
        hash_scheme: str,
        purpose_of_use: str,
        amh_tenant: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[DocumentResolution]: ...
