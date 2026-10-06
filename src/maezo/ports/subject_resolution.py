"""`SubjectResolutionPort` — com quem o Maezo fala, resolvido pela AMH a partir do telefone (hash).

ADR-0037 XRD-05: so' a AMH liga telefone a sujeito. O Maezo manda o HASH do telefone (nunca o numero) e
recebe um CONJUNTO de `portable_subject_ref` opacos, mais o perfil minimo de cada um quando precisa.
Os tipos espelham o contrato `subject-resolution` PROPOSTO em `Omni-Saude/amh-data-platform` (branch
`proposta/maezo-cobranca-e-identidade-v1`, AINDA NAO PUBLICADO); o schema e' da AMH.

REGRAS DE DESENHO (as mesmas do contrato):
  * a resolucao devolve um CONJUNTO. Titular e dependentes dividem aparelho: mais de um candidato e'
    caso de PERGUNTAR, nunca de escolher o primeiro;
  * `nenhum` e' resposta normal: e' o caminho degradado, em que o agente segue sem identidade;
  * campo ausente e' estado valido (`None`); nunca e' completado por quem consome;
  * idade em anos/meses, nunca data de nascimento; SEM sexo (a triagem decide por populacao e idade, e a
    cerca de pureza dos ports barra nome de campo com dado pessoal: minimizacao, nao contorno).

O CONTATO e' sempre um PSEUDONIMO chaveado, nunca o numero. O parametro se chama `contact_pseudonym`
por isso: o nome diz o que o valor e'. Desde a decisao do dono de 06/10/2026 o valor ligado e' o hash
do esquema `amh-phone-lookup-v1` (HMAC-SHA256 com chave DEDICADA, `gateway/amh_interop.py`), e nao o
`hk1_` do Maezo, que nao significa nada para a AMH. No contrato da AMH o campo do corpo segue
`phone_hash` (nome dela); o adaptador faz o mapeamento.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True)
class SubjectCandidate:
    portable_subject_ref: str
    relacao: str  # titular | dependente | desconhecida
    vigencia_ativa: bool | None


@dataclass(frozen=True, slots=True)
class SubjectResolution:
    resultado: str  # unico | multiplos | nenhum
    candidatos: tuple[SubjectCandidate, ...]
    campos_ausentes: frozenset[str]


@dataclass(frozen=True, slots=True)
class PlanSummary:
    ativo: bool | None
    vigencia_inicio: str | None
    vigencia_fim: str | None
    carencia_vigente: bool | None


@dataclass(frozen=True, slots=True)
class SubjectProfile:
    portable_subject_ref: str
    idade_anos: int | None
    idade_meses: int | None
    plano: PlanSummary
    titular_ref: str | None
    fonte_atualizada_em: str
    campos_ausentes: frozenset[str]


@runtime_checkable
class SubjectResolutionPort(Protocol):
    """Resolucao e perfil minimo, read-only. Sucesso ou recusa fechada, nunca excecao de politica."""

    async def resolve_by_phone(
        self,
        contact_pseudonym: str,
        *,
        amh_tenant: str,
        hash_scheme: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectResolution]: ...

    async def get_profile(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SubjectProfile]: ...
