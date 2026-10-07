"""Identidade do beneficiario na Helena: QUEM escreve, pelos contratos da AMH, so' o basico (DL-0077).

Decisao do dono de 06/10/2026: a Helena tambem passa a saber quem e' a pessoa que escreve, pelos
MESMOS contratos e executores governados que a fonte de cobranca do Lucas usa (DL-0075,
`gateway/amh_interop.py`): `subject-resolution` (telefone -> `portable_subject_ref`) e o perfil minimo
(faixa etaria, plano ativo, vigencia, carencia, titular). DESLIGADA por padrao
(`MAEZO_HELENA_IDENTIDADE_AMH`) e, mesmo ligada, inerte ate' a AMH publicar e o pin imutavel registrar os
OpenAPI (XRG-2/XRG-3): sem pin/segredo/URL o receptor recusa servir (`service.py`).

O QUE E', E O QUE NAO E'. E' CONTEXTO pseudonimo (Zona Geral, ADR-0006): a referencia opaca, a faixa
etaria GROSSA (nunca idade exata nem nascimento) e os fatos de plano do contrato — o vocabulario fechado
de `agents/helena/graph.py::IDENTIDADE_CHAVES`. NAO e' insumo de decisao: nada aqui classifica, tabela,
escala ou redige; a triagem segue decidindo so' pelo que a pessoa DISSE (populacao e idade da extracao),
a DMN e as red flags. Nunca telefone, CPF ou nome.

FAIL-CLOSED. So' conta a resolucao de candidato UNICO (`ResolvedorDeSujeitoAmh`): telefone compartilhado,
sem candidato, hash indisponivel, sem base legal, recusa do port, perfil de outro sujeito, resposta fora
do vocabulario ou estouro de prazo viram `None` — a Helena de sempre. `resolver` NUNCA levanta e nunca
bloqueia o turno alem de `PRAZO_TOTAL_S`.

CACHE. Por conversa (`conversation_id` keyed), em memoria do processo, com validade curta: um resultado
positivo vale `TTL_POSITIVO_S`; um negativo, `TTL_NEGATIVO_S` (para uma indisponibilidade da AMH nao ser
martelada a cada mensagem, mas se recuperar logo). Limitado em tamanho. Nada e' persistido e o hash do
telefone nunca e' guardado (so' o resultado).

LOG. So' `conversation_id` (keyed), o motivo (token fechado) e booleanos. Nunca o hash, a referencia, o
numero nem o perfil.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

from maezo.agents.helena.graph import normalizar_identidade
from maezo.agents.lucas.fonte_cobranca_amh import FonteDeConsentimento, ResolvedorDeSujeito
from maezo.ports.subject_resolution import SubjectProfile, SubjectResolutionPort

logger = structlog.get_logger(__name__)

#: Teto do tempo que a resolucao pode somar ao turno (resolucao + perfil, cada um limitado a 5s pelo port).
PRAZO_TOTAL_S: Final[float] = 6.0
TTL_POSITIVO_S: Final[float] = 600.0
TTL_NEGATIVO_S: Final[float] = 60.0
MAX_CONVERSAS_EM_CACHE: Final[int] = 4096


def _inteiro(valor: object) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def faixa_etaria(idade_anos: int | None, idade_meses: int | None) -> str | None:
    """A faixa GROSSA (`IDENTIDADE_FAIXAS_ETARIAS`) ou `None` quando o contrato nao traz idade valida.

    Menos de 24 meses e' `lactente`; ate' 11 anos `crianca`; ate' 17 `adolescente`; ate' 59 `adulto`;
    depois `idoso`. A idade exata fica na AMH: aqui so' a faixa, e so' como contexto.
    """
    meses = _inteiro(idade_meses)
    if meses is not None and 0 <= meses < 24:
        return "lactente"
    anos = _inteiro(idade_anos)
    if anos is None and meses is not None and meses >= 0:
        anos = meses // 12
    if anos is None or anos < 0 or anos > 130:
        return None
    if anos < 12:
        return "crianca"
    if anos < 18:
        return "adolescente"
    if anos < 60:
        return "adulto"
    return "idoso"


def identidade_do_perfil(perfil: SubjectProfile) -> dict[str, Any] | None:
    """O perfil do contrato -> a identidade de vocabulario fechado, ou `None` se algo nao fecha."""
    plano = perfil.plano
    return normalizar_identidade(
        {
            "portable_subject_ref": perfil.portable_subject_ref,
            "faixa_etaria": faixa_etaria(perfil.idade_anos, perfil.idade_meses),
            "plano_ativo": plano.ativo,
            "vigencia_inicio": plano.vigencia_inicio,
            "vigencia_fim": plano.vigencia_fim,
            "carencia_vigente": plano.carencia_vigente,
            "titular_ref": perfil.titular_ref,
        }
    )


@dataclass
class IdentidadeHelena:
    """Construida UMA vez no boot do receptor, so' com `helena_identidade_amh` ligada (`service.py`)."""

    port: SubjectResolutionPort
    resolvedor: ResolvedorDeSujeito
    #: Base legal fixa de execucao de contrato (decisao do dono; NAO e' consentimento).
    consentimento: FonteDeConsentimento
    purpose_of_use: str
    #: numero cru -> hash `amh-phone-lookup-v1` (`AmhPhoneLookupHasher`); `None` para numero fora do padrao.
    hash_telefone: Callable[[str], str | None] = field(repr=False)
    #: Drenagem dos executores do gateway.
    aclose_fn: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)
    relogio: Callable[[], float] = field(default=time.monotonic, repr=False)
    _cache: OrderedDict[str, tuple[float, dict[str, Any] | None]] = field(
        default_factory=OrderedDict, init=False, repr=False
    )

    def _do_cache(self, conversation_id: str) -> tuple[bool, dict[str, Any] | None]:
        entrada = self._cache.get(conversation_id)
        if entrada is None:
            return False, None
        validade, identidade = entrada
        if self.relogio() >= validade:
            del self._cache[conversation_id]
            return False, None
        self._cache.move_to_end(conversation_id)
        return True, dict(identidade) if identidade is not None else None

    def _guardar(self, conversation_id: str, identidade: dict[str, Any] | None) -> None:
        ttl = TTL_POSITIVO_S if identidade is not None else TTL_NEGATIVO_S
        self._cache[conversation_id] = (self.relogio() + ttl, identidade)
        self._cache.move_to_end(conversation_id)
        while len(self._cache) > MAX_CONVERSAS_EM_CACHE:
            self._cache.popitem(last=False)

    async def _resolver(self, numero_cru: str, pseudo_id: str) -> tuple[dict[str, Any] | None, str]:
        phone_hash = self.hash_telefone(numero_cru)
        if not phone_hash:
            return None, "hash_indisponivel"
        ref = await self.resolvedor.portable_ref(pseudo_id, phone_hash=phone_hash)
        if ref is None:
            return None, "sujeito_nao_resolvido"
        base_legal = await self.consentimento.decisao(ref, self.purpose_of_use)
        if base_legal is None:
            return None, "sem_base_legal"
        resultado = await self.port.get_profile(
            ref, purpose_of_use=self.purpose_of_use, consent_decision_ref=base_legal
        )
        if not resultado.succeeded or resultado.value is None:
            return None, "perfil_indisponivel"
        if resultado.value.portable_subject_ref != ref:
            return None, "perfil_de_outro_sujeito"
        identidade = identidade_do_perfil(resultado.value)
        if identidade is None:
            return None, "perfil_fora_do_vocabulario"
        return identidade, "ok"

    async def resolver(
        self, numero_cru: str, *, conversation_id: str, pseudo_id: str
    ) -> dict[str, Any] | None:
        """A identidade da pessoa que escreve, ou `None`. NUNCA levanta."""
        try:
            achou, em_cache = self._do_cache(conversation_id)
            if achou:
                return em_cache
            try:
                async with asyncio.timeout(PRAZO_TOTAL_S):
                    identidade, motivo = await self._resolver(numero_cru, pseudo_id)
            except TimeoutError:
                identidade, motivo = None, "prazo_estourado"
            self._guardar(conversation_id, identidade)
            logger.info(
                "helena_identidade_resolvida",
                conversation_id=conversation_id,
                resolvida=identidade is not None,
                motivo=motivo,
            )
            return dict(identidade) if identidade is not None else None
        except Exception as exc:
            logger.warning(
                "helena_identidade_falhou", conversation_id=conversation_id, error_type=type(exc).__name__
            )
            return None

    async def aclose(self) -> None:
        if self.aclose_fn is not None:
            await self.aclose_fn()


__all__ = [
    "MAX_CONVERSAS_EM_CACHE",
    "PRAZO_TOTAL_S",
    "TTL_NEGATIVO_S",
    "TTL_POSITIVO_S",
    "IdentidadeHelena",
    "faixa_etaria",
    "identidade_do_perfil",
]
