"""A razao append-only `pagto_obrigacao_ledger` (migracao 0034): forma da linha, porta e store.

Garantias, cada uma com o seu dono:

* **Append-only** — o banco (triggers da 0034). Esta classe nao tem metodo que faca UPDATE ou
  DELETE; mudanca de estado de uma ordem e' uma nova linha (`evento`).
* **Idempotencia por (ordem, evento)** — `UNIQUE (tenant, chave_obrigacao, ordem_pagamento_id,
  evento)` + `ON CONFLICT DO NOTHING`: a re-entrega da mesma tarefa externa nao duplica nada.
* **Leitura serializada com a escrita** — `registrar_e_ler` toma `pg_advisory_xact_lock` por
  obrigacao e por prestador ANTES de ler os indicios e gravar a propria linha, na MESMA
  transacao: duas ordens concorrentes da mesma obrigacao (ou do mesmo prestador) se serializam e
  a segunda ve a primeira. Locks sempre na mesma ordem (prestador, depois obrigacao).
* **Falha fechada** — qualquer falha de banco vira `RazaoIndisponivelError` (um `RuntimeError`):
  o detector a traduz em `INDETERMINADA`, nunca em "nao ha duplicidade". Erro de PROGRAMACAO
  (`PROGRAMMING_ERRORS`, licao NEW-12) nao e' embrulhado: sobe como incidente.

Esta camada SO' produz FATOS (o que esta' gravado). Classificar (`CONFIRMADA`/`SUSPEITA`/...) e'
da DMN `pagto_duplicidade`, avaliada pelo detector no motor (`tools/workers/pagto_duplicidade.py`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

EVENTO_REGISTRADA: Final[str] = "REGISTRADA"
EVENTO_LIBERADA: Final[str] = "LIBERADA"
EVENTOS: Final[frozenset[str]] = frozenset({EVENTO_REGISTRADA, EVENTO_LIBERADA})

ORIGEM_CONTAS: Final[str] = "contas"
ORIGEM_RECURSO: Final[str] = "recurso"
ORIGEM_OUTRO: Final[str] = "outro"
ORIGENS: Final[frozenset[str]] = frozenset({ORIGEM_CONTAS, ORIGEM_RECURSO, ORIGEM_OUTRO})

#: Versao do contrato de escrita (vai em `versao_detector`).
VERSAO_DETECTOR: Final[str] = "pagto-pr-e/v1"

#: Situacao do nivel 1 (mesma obrigacao, OUTRA ordem) — vocabulario fechado de FATO.
NIVEL1_SEM_OUTRA_ORDEM: Final[str] = "SEM_OUTRA_ORDEM"
NIVEL1_REGISTRADA_OUTRA_ORDEM: Final[str] = "REGISTRADA_OUTRA_ORDEM"
NIVEL1_LIBERADA_OUTRA_ORDEM: Final[str] = "LIBERADA_OUTRA_ORDEM"

#: Espelhos byte a byte dos CHECKs da 0034.
_TENANT_PROIBIDO_RE: Final = re.compile(r"[\s\x00-\x1f\x7f/?#]")
_SEM_ESPACO_RE: Final = re.compile(r"[\s\x00-\x1f\x7f]")
_CHAVE_RE: Final = re.compile(r"OBRIG-(contas|recurso|outro)-[0-9a-f]{64}")
PRESTADOR_ID_RE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_COMPETENCIA_RE: Final = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_INSTANCIA_RE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_VERSAO_RE: Final = re.compile(r"[a-z0-9./-]{1,64}")
#: Teto de Java long (o mesmo dominio de `valor_pagamento_cents`).
_LONG_MAX: Final[int] = 2**63 - 1


class RazaoIndisponivelError(RuntimeError):
    """A razao nao respondeu (conexao, timeout, SQL). Sempre "nao sei", nunca "nao ha nada"."""


def chave_obrigacao(tenant: str, origem: str, componentes: tuple[str, ...]) -> str:
    """`OBRIG-{origem}-{sha256}` sobre `[tenant, origem, *componentes]` (JSON canonico).

    Digest (e nao concatenacao) para que nenhum separador dentro de um componente crie duas
    obrigacoes com a mesma chave. Componentes vazios sao recusados: sem identidade nao ha chave.
    """
    if origem not in ORIGENS:
        raise ValueError("chave_obrigacao: origem fora do vocabulario")
    if not tenant or not componentes or any(not c for c in componentes):
        raise ValueError("chave_obrigacao: tenant/componente vazio")
    canonico = json.dumps([tenant, origem, *componentes], ensure_ascii=False, separators=(",", ":"))
    return f"OBRIG-{origem}-{hashlib.sha256(canonico.encode('utf-8')).hexdigest()}"


def _texto_sem_espaco(campo: str, valor: str, teto: int) -> None:
    if not valor or len(valor) > teto or _SEM_ESPACO_RE.search(valor):
        raise ValueError(f"EntradaObrigacao: {campo} fora do formato")


@dataclass(frozen=True, slots=True)
class EntradaObrigacao:
    """Uma linha da razao. Validada no construtor com os MESMOS limites dos CHECKs da 0034, para
    que um valor invalido seja recusado antes de qualquer rede."""

    tenant: str
    evento: str
    origem: str
    chave_obrigacao: str
    ordem_pagamento_id: str
    prestador_id: str
    valor_cents: int
    numero_lote_tiss: str | None = None
    competencia: str | None = None
    origem_instancia_id: str | None = None
    versao_detector: str = VERSAO_DETECTOR

    def __post_init__(self) -> None:
        if not self.tenant or len(self.tenant) > 512 or _TENANT_PROIBIDO_RE.search(self.tenant):
            raise ValueError("EntradaObrigacao: tenant fora do formato")
        if self.evento not in EVENTOS:
            raise ValueError("EntradaObrigacao: evento fora do vocabulario")
        if self.origem not in ORIGENS:
            raise ValueError("EntradaObrigacao: origem fora do vocabulario")
        if not _CHAVE_RE.fullmatch(self.chave_obrigacao) or not self.chave_obrigacao.startswith(
            f"OBRIG-{self.origem}-"
        ):
            raise ValueError("EntradaObrigacao: chave_obrigacao fora do formato")
        _texto_sem_espaco("ordem_pagamento_id", self.ordem_pagamento_id, 256)
        if not PRESTADOR_ID_RE.fullmatch(self.prestador_id):
            raise ValueError("EntradaObrigacao: prestador_id fora do formato")
        if (
            not isinstance(self.valor_cents, int)
            or isinstance(self.valor_cents, bool)
            or not 0 < self.valor_cents <= _LONG_MAX
        ):
            raise ValueError("EntradaObrigacao: valor_cents fora do dominio")
        if self.numero_lote_tiss is not None:
            _texto_sem_espaco("numero_lote_tiss", self.numero_lote_tiss, 128)
        if self.competencia is not None and not _COMPETENCIA_RE.fullmatch(self.competencia):
            raise ValueError("EntradaObrigacao: competencia fora do formato")
        if self.origem_instancia_id is not None and not _INSTANCIA_RE.fullmatch(self.origem_instancia_id):
            raise ValueError("EntradaObrigacao: origem_instancia_id fora do formato")
        if not _VERSAO_RE.fullmatch(self.versao_detector):
            raise ValueError("EntradaObrigacao: versao_detector fora do formato")


@dataclass(frozen=True, slots=True)
class IndiciosDuplicidade:
    """O que a razao mostra sobre OUTRAS ordens, lido no mesmo lock em que a linha foi gravada.

    * `nivel1_situacao` — a mesma obrigacao em outra ordem: `SEM_OUTRA_ORDEM` |
      `REGISTRADA_OUTRA_ORDEM` | `LIBERADA_OUTRA_ORDEM` (a mais grave vence);
    * `nivel1_ordem_id` — a ordem conflitante (`""` quando nenhuma);
    * `nivel2_soma_outras_cents` — soma de `valor_cents` das OUTRAS ordens registradas contra a
      MESMA instancia de origem; `None` quando a entrada nao trouxe instancia de origem;
    * `nivel3_similar_idade_dias` — idade (dias inteiros) da ordem similar MAIS RECENTE (mesmo
      prestador + competencia + valor, outra obrigacao, outro lote); `None` quando nenhuma ou
      quando a entrada nao trouxe competencia (ver `nivel3_avaliado`);
    * `nivel3_ordem_id` — a ordem similar mais recente (`""` quando nenhuma);
    * `gravou` — a linha `REGISTRADA` desta ordem foi gravada AGORA (False = ja estava).
    """

    nivel1_situacao: str
    nivel1_ordem_id: str
    nivel2_soma_outras_cents: int | None
    nivel3_avaliado: bool
    nivel3_similar_idade_dias: int | None
    nivel3_ordem_id: str
    gravou: bool


@runtime_checkable
class RazaoObrigacoesPagamento(Protocol):
    """A porta que o detector e o envoltorio de liberacao usam. So' acrescenta e le."""

    @property
    def tenant(self) -> str:
        """O tenant do schema da razao — a unica identidade de tenant que ela aceita."""
        ...

    async def registrar_e_ler(self, entrada: EntradaObrigacao) -> IndiciosDuplicidade:
        """Le os indicios de duplicidade e grava a `REGISTRADA` da ordem, na mesma transacao."""
        ...

    async def registrar_liberacao(self, entrada: EntradaObrigacao) -> bool:
        """Grava a `LIBERADA` da ordem. `True` se gravou, `False` se ja estava gravada."""
        ...


_COLUNAS: Final = (
    "tenant",
    "evento",
    "origem",
    "chave_obrigacao",
    "ordem_pagamento_id",
    "prestador_id",
    "numero_lote_tiss",
    "competencia",
    "valor_cents",
    "origem_instancia_id",
    "versao_detector",
)
_INSERT_SQL: Final = (
    f"INSERT INTO pagto_obrigacao_ledger ({', '.join(_COLUNAS)}) "
    f"VALUES ({', '.join(f'${i}' for i in range(1, len(_COLUNAS) + 1))}) "
    "ON CONFLICT (tenant, chave_obrigacao, ordem_pagamento_id, evento) DO NOTHING RETURNING id"
)
_LOCK_SQL: Final = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
#: Nivel 1: a mesma obrigacao em OUTRA ordem (a re-entrega da propria ordem nunca conta).
_NIVEL1_SQL: Final = (
    "SELECT ordem_pagamento_id, evento FROM pagto_obrigacao_ledger "
    "WHERE tenant = $1 AND chave_obrigacao = $2 AND ordem_pagamento_id <> $3 ORDER BY id"
)
#: Nivel 2: o que OUTRAS ordens ja comprometeram contra a mesma instancia de origem (uma linha
#: REGISTRADA por ordem — a UNIQUE garante; LIBERADA nao soma de novo).
_NIVEL2_SQL: Final = (
    "SELECT COALESCE(SUM(valor_cents), 0)::bigint FROM pagto_obrigacao_ledger "
    "WHERE tenant = $1 AND evento = 'REGISTRADA' AND origem_instancia_id = $2 "
    "AND ordem_pagamento_id <> $3"
)
#: Nivel 3: mesmo prestador + competencia + valor, OUTRA obrigacao, OUTRO lote (lote ausente de
#: qualquer lado conta como "outro" — desconhecido nunca descarta o indicio). A idade e' contada
#: pelo relogio do BANCO (`clock_timestamp()`), nunca pelo do worker. Janela: DMN.
_NIVEL3_SQL: Final = (
    "SELECT ordem_pagamento_id, "
    "floor(extract(epoch FROM (clock_timestamp() - registrado_em)) / 86400)::bigint AS idade_dias "
    "FROM pagto_obrigacao_ledger "
    "WHERE tenant = $1 AND evento = 'REGISTRADA' AND prestador_id = $2 AND competencia = $3 "
    "AND valor_cents = $4 AND ordem_pagamento_id <> $5 AND chave_obrigacao <> $6 "
    "AND (numero_lote_tiss IS NULL OR $7::text IS NULL OR numero_lote_tiss <> $7::text) "
    "ORDER BY registrado_em DESC, id DESC LIMIT 1"
)


def _params(entrada: EntradaObrigacao) -> tuple[Any, ...]:
    return (
        entrada.tenant,
        entrada.evento,
        entrada.origem,
        entrada.chave_obrigacao,
        entrada.ordem_pagamento_id,
        entrada.prestador_id,
        entrada.numero_lote_tiss,
        entrada.competencia,
        entrada.valor_cents,
        entrada.origem_instancia_id,
        entrada.versao_detector,
    )


def _indisponivel(exc: BaseException) -> str:
    return f"pagto_obrigacao_ledger indisponivel: {type(exc).__name__}"


def _lock_obrigacao(entrada: EntradaObrigacao) -> str:
    return f"pagto-obrigacao|{entrada.tenant}|{entrada.chave_obrigacao}"


def _situacao_nivel1(rows: list[Any]) -> tuple[str, str]:
    situacao, ordem = NIVEL1_SEM_OUTRA_ORDEM, ""
    for row in rows:
        if row["evento"] == EVENTO_LIBERADA:
            return NIVEL1_LIBERADA_OUTRA_ORDEM, str(row["ordem_pagamento_id"])
        if situacao == NIVEL1_SEM_OUTRA_ORDEM:
            situacao, ordem = NIVEL1_REGISTRADA_OUTRA_ORDEM, str(row["ordem_pagamento_id"])
    return situacao, ordem


Conectar = Callable[[], Awaitable[Any]]


class PostgresRazaoObrigacoesPagamento:
    """`RazaoObrigacoesPagamento` sobre a 0034, via asyncpg, no schema do tenant (DL-0017).

    Uma conexao por operacao: o worker sincrono roda cada tarefa numa thread com `asyncio.run`
    proprio (loop novo por despacho), e uma conexao/pool asyncpg fica presa ao loop que a criou
    (mesma razao de `FreshSinkAuditEmitter`). `conectar` e' injetavel para os testes; o padrao
    abre `asyncpg.connect(dsn)`.
    """

    __slots__ = ("_conectar", "_schema", "_tenant")

    def __init__(self, *, dsn: str, tenant: str, conectar: Conectar | None = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        if conectar is None:
            import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

            plain = normalize_dsn(dsn)

            async def _padrao() -> Any:
                return await asyncpg.connect(plain)

            conectar = _padrao
        self._conectar = conectar

    @property
    def tenant(self) -> str:
        """O tenant do schema deste store — a unica identidade de tenant que a razao aceita."""
        return self._tenant

    def _exige_tenant(self, entrada: EntradaObrigacao) -> None:
        if entrada.tenant != self._tenant:
            # Uma linha de outro tenant no schema deste: recusa de programacao, antes da rede.
            raise ValueError("PostgresRazaoObrigacoesPagamento: tenant da entrada difere do store")

    async def _abrir(self) -> Any:
        conn = await self._conectar()
        await conn.execute(f'SET search_path TO "{self._schema}", public')
        return conn

    async def registrar_e_ler(self, entrada: EntradaObrigacao) -> IndiciosDuplicidade:
        self._exige_tenant(entrada)
        if entrada.evento != EVENTO_REGISTRADA:
            raise ValueError("registrar_e_ler: so' grava o evento REGISTRADA")
        try:
            conn = await self._abrir()
            try:
                async with conn.transaction():
                    # Ordem FIXA (prestador, depois obrigacao): nenhum ciclo de espera possivel.
                    await conn.execute(_LOCK_SQL, f"pagto-prestador|{entrada.tenant}|{entrada.prestador_id}")
                    await conn.execute(_LOCK_SQL, _lock_obrigacao(entrada))
                    nivel1 = await conn.fetch(
                        _NIVEL1_SQL, entrada.tenant, entrada.chave_obrigacao, entrada.ordem_pagamento_id
                    )
                    soma: int | None = None
                    if entrada.origem_instancia_id is not None:
                        soma = int(
                            await conn.fetchval(
                                _NIVEL2_SQL,
                                entrada.tenant,
                                entrada.origem_instancia_id,
                                entrada.ordem_pagamento_id,
                            )
                        )
                    similar = None
                    if entrada.competencia is not None:
                        similar = await conn.fetchrow(
                            _NIVEL3_SQL,
                            entrada.tenant,
                            entrada.prestador_id,
                            entrada.competencia,
                            entrada.valor_cents,
                            entrada.ordem_pagamento_id,
                            entrada.chave_obrigacao,
                            entrada.numero_lote_tiss,
                        )
                    gravado = await conn.fetchval(_INSERT_SQL, *_params(entrada))
            finally:
                await conn.close()
        except PROGRAMMING_ERRORS:
            raise  # bug do proprio codigo vira incidente visivel, nunca um "nao sei" silencioso
        except Exception as exc:  # falha fechada: qualquer falha de banco e' "nao sei"
            raise RazaoIndisponivelError(_indisponivel(exc)) from exc
        situacao, ordem1 = _situacao_nivel1(list(nivel1))
        return IndiciosDuplicidade(
            nivel1_situacao=situacao,
            nivel1_ordem_id=ordem1,
            nivel2_soma_outras_cents=soma,
            nivel3_avaliado=entrada.competencia is not None,
            nivel3_similar_idade_dias=None if similar is None else int(similar["idade_dias"]),
            nivel3_ordem_id="" if similar is None else str(similar["ordem_pagamento_id"]),
            gravou=gravado is not None,
        )

    async def registrar_liberacao(self, entrada: EntradaObrigacao) -> bool:
        self._exige_tenant(entrada)
        if entrada.evento != EVENTO_LIBERADA:
            raise ValueError("registrar_liberacao: so' grava o evento LIBERADA")
        try:
            conn = await self._abrir()
            try:
                gravado = await conn.fetchval(_INSERT_SQL, *_params(entrada))
            finally:
                await conn.close()
        except PROGRAMMING_ERRORS:
            raise  # bug do proprio codigo vira incidente visivel, nunca um "nao sei" silencioso
        except Exception as exc:  # falha fechada: qualquer falha de banco e' "nao sei"
            raise RazaoIndisponivelError(_indisponivel(exc)) from exc
        return gravado is not None


__all__ = [
    "EVENTOS",
    "EVENTO_LIBERADA",
    "EVENTO_REGISTRADA",
    "NIVEL1_LIBERADA_OUTRA_ORDEM",
    "NIVEL1_REGISTRADA_OUTRA_ORDEM",
    "NIVEL1_SEM_OUTRA_ORDEM",
    "ORIGENS",
    "ORIGEM_CONTAS",
    "ORIGEM_OUTRO",
    "ORIGEM_RECURSO",
    "PRESTADOR_ID_RE",
    "VERSAO_DETECTOR",
    "EntradaObrigacao",
    "IndiciosDuplicidade",
    "PostgresRazaoObrigacoesPagamento",
    "RazaoIndisponivelError",
    "RazaoObrigacoesPagamento",
    "chave_obrigacao",
]
