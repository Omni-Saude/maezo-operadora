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

DESFECHO (DL-0078, decisao do dono de 07/10/2026). `resolver_com_desfecho` devolve, alem da identidade, o
desfecho FECHADO que decide o aviso de identidade da Helena: `reconhecido` (identidade montada),
`nao_encontrado` (a AMH respondeu com SUCESSO que NAO ha' candidato — `resultado == "nenhum"` e conjunto
vazio, via `ResolvedorDeSujeitoAmh.portable_ref_com_desfecho`) ou `indeterminado` (todo o resto: telefone
compartilhado, falha, prazo, sem base legal, perfil fora do vocabulario, resolvedor sem desfecho). So'
`nao_encontrado` autoriza dizer "nao encontrei"; o indeterminado nao diz nada.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

import structlog

from maezo.agents.helena.graph import (
    IDENTIDADE_INDETERMINADA,
    IDENTIDADE_NAO_ENCONTRADA,
    IDENTIDADE_RECONHECIDA,
    normalizar_identidade,
)
from maezo.agents.lucas.fonte_cobranca_amh import FonteDeConsentimento, ResolvedorDeSujeito
from maezo.ports.subject_resolution import SubjectProfile, SubjectResolutionPort

logger = structlog.get_logger(__name__)

#: Teto do tempo que a resolucao pode somar ao turno (resolucao + perfil, cada um limitado a 5s pelo port).
PRAZO_TOTAL_S: Final[float] = 6.0
TTL_POSITIVO_S: Final[float] = 600.0
TTL_NEGATIVO_S: Final[float] = 60.0
MAX_CONVERSAS_EM_CACHE: Final[int] = 4096


@runtime_checkable
class ResolvedorComDesfecho(Protocol):
    """O resolvedor que tambem diz POR QUE nao resolveu (`ResolvedorDeSujeitoAmh`). DL-0078."""

    async def portable_ref_com_desfecho(
        self, pseudo_id: str, *, phone_hash: str | None
    ) -> tuple[str | None, str]: ...


@dataclass(frozen=True, slots=True)
class ResultadoIdentidade:
    """A identidade (ou `None`) e o desfecho fechado (`graph.IDENTIDADE_DESFECHOS`)."""

    identidade: dict[str, Any] | None
    desfecho: str


_INDETERMINADO: Final[ResultadoIdentidade] = ResultadoIdentidade(None, IDENTIDADE_INDETERMINADA)


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
    _cache: OrderedDict[str, tuple[float, ResultadoIdentidade]] = field(
        default_factory=OrderedDict, init=False, repr=False
    )

    def _do_cache(self, conversation_id: str) -> ResultadoIdentidade | None:
        entrada = self._cache.get(conversation_id)
        if entrada is None:
            return None
        validade, resultado = entrada
        if self.relogio() >= validade:
            del self._cache[conversation_id]
            return None
        self._cache.move_to_end(conversation_id)
        return _copia(resultado)

    def _guardar(self, conversation_id: str, resultado: ResultadoIdentidade) -> None:
        ttl = TTL_POSITIVO_S if resultado.identidade is not None else TTL_NEGATIVO_S
        self._cache[conversation_id] = (self.relogio() + ttl, resultado)
        self._cache.move_to_end(conversation_id)
        while len(self._cache) > MAX_CONVERSAS_EM_CACHE:
            self._cache.popitem(last=False)

    async def _referencia(self, pseudo_id: str, phone_hash: str) -> tuple[str | None, str]:
        """A referencia e o motivo fechado. `sujeito_nao_encontrado` SO' com o `nenhum` definitivo da AMH."""
        if isinstance(self.resolvedor, ResolvedorComDesfecho):
            ref, desfecho = await self.resolvedor.portable_ref_com_desfecho(pseudo_id, phone_hash=phone_hash)
            if ref is not None and desfecho == "unico":
                return ref, "ok"
            motivos = {"nenhum": "sujeito_nao_encontrado", "multiplos": "sujeito_ambiguo"}
            return None, motivos.get(desfecho, "sujeito_nao_resolvido")
        # Resolvedor sem desfecho: nao ha' como distinguir "nenhum" de "varios" — fica indeterminado.
        ref = await self.resolvedor.portable_ref(pseudo_id, phone_hash=phone_hash)
        return ref, "ok" if ref is not None else "sujeito_nao_resolvido"

    async def _resolver(self, numero_cru: str, pseudo_id: str) -> tuple[dict[str, Any] | None, str]:
        phone_hash = self.hash_telefone(numero_cru)
        if not phone_hash:
            return None, "hash_indisponivel"
        ref, motivo = await self._referencia(pseudo_id, phone_hash)
        if ref is None:
            return None, motivo
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
        resultado = await self.resolver_com_desfecho(
            numero_cru, conversation_id=conversation_id, pseudo_id=pseudo_id
        )
        return resultado.identidade

    async def resolver_com_desfecho(
        self, numero_cru: str, *, conversation_id: str, pseudo_id: str
    ) -> ResultadoIdentidade:
        """A identidade e o desfecho fechado (DL-0078). NUNCA levanta: qualquer falha e' `indeterminado`."""
        try:
            em_cache = self._do_cache(conversation_id)
            if em_cache is not None:
                return em_cache
            try:
                async with asyncio.timeout(PRAZO_TOTAL_S):
                    identidade, motivo = await self._resolver(numero_cru, pseudo_id)
            except TimeoutError:
                identidade, motivo = None, "prazo_estourado"
            if identidade is not None:
                resultado = ResultadoIdentidade(identidade, IDENTIDADE_RECONHECIDA)
            elif motivo == "sujeito_nao_encontrado":
                resultado = ResultadoIdentidade(None, IDENTIDADE_NAO_ENCONTRADA)
            else:
                resultado = _INDETERMINADO
            self._guardar(conversation_id, resultado)
            logger.info(
                "helena_identidade_resolvida",
                conversation_id=conversation_id,
                resolvida=identidade is not None,
                motivo=motivo,
                desfecho=resultado.desfecho,
            )
            return _copia(resultado)
        except Exception as exc:
            logger.warning(
                "helena_identidade_falhou", conversation_id=conversation_id, error_type=type(exc).__name__
            )
            return _INDETERMINADO

    async def aclose(self) -> None:
        if self.aclose_fn is not None:
            await self.aclose_fn()


def _copia(resultado: ResultadoIdentidade) -> ResultadoIdentidade:
    """Copia rasa da identidade: quem recebe nunca altera o que esta' no cache."""
    identidade = dict(resultado.identidade) if resultado.identidade is not None else None
    return ResultadoIdentidade(identidade, resultado.desfecho)


__all__ = [
    "MAX_CONVERSAS_EM_CACHE",
    "PRAZO_TOTAL_S",
    "TTL_NEGATIVO_S",
    "TTL_POSITIVO_S",
    "IdentidadeHelena",
    "ResolvedorComDesfecho",
    "ResultadoIdentidade",
    "faixa_etaria",
    "identidade_do_perfil",
]
