"""O ledger append-only `prestador_status_ledger` (migracao 0032): forma da linha, porta e store.

Tres garantias, cada uma com o seu dono:

* **Append-only** — o banco (triggers da 0032). Esta classe nao tem metodo que faca UPDATE ou
  DELETE; correcao de status e' uma nova linha.
* **Idempotencia por evento de origem** — `UNIQUE (tenant, chave_evento)`. `registrar` devolve
  `False` quando a mesma chave ja existe COM O MESMO conteudo; chave igual com conteudo diferente
  e' contradicao e levanta `LedgerConflitoError` (nunca e' silenciada nem sobrescrita).
* **Falha fechada** — qualquer falha de banco vira `LedgerIndisponivelError`, e a leitura de
  situacao devolve `SituacaoLida.INDISPONIVEL`, nunca um status adivinhado.

`DESCONHECIDO` (nenhuma linha de situacao para o prestador) e `INDISPONIVEL` (a leitura falhou)
sao estados de LEITURA: nao existem no CHECK da tabela e nunca sao gravados.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform.prestador_status.projecao import Dimensao, Origem, ValorFraude, ValorSituacao

#: Espelhos byte a byte dos CHECKs da 0032.
PRESTADOR_ID_RE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_CHAVE_EVENTO_RE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,400}")
_FONTE_REF_RE: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TIPO_PRESTADOR_RE: Final = re.compile(r"[a-z_]{1,32}")
_VERSAO_RE: Final = re.compile(r"[a-z0-9./-]{1,64}")
_TENANT_PROIBIDO_RE: Final = re.compile(r"[\s\x00-\x1f\x7f/?#]")
_EVIDENCIA_PROIBIDA_RE: Final = re.compile(r"[\s\x00-\x1f\x7f]")
_CONTROLE_RE: Final = re.compile(r"[\x00-\x1f\x7f]")

_VALORES_POR_DIMENSAO: Final[dict[Dimensao, frozenset[str]]] = {
    Dimensao.SITUACAO: frozenset(v.value for v in ValorSituacao),
    Dimensao.FRAUDE: frozenset(v.value for v in ValorFraude),
}
_ORIGENS_POR_DIMENSAO: Final[dict[Dimensao, frozenset[Origem]]] = {
    Dimensao.SITUACAO: frozenset({Origem.BASELINE_AMH, Origem.CRED}),
    Dimensao.FRAUDE: frozenset({Origem.FRAUDE}),
}


class LedgerIndisponivelError(RuntimeError):
    """O ledger nao respondeu (conexao, timeout, SQL). Sempre "nao sei", nunca "nao ha nada"."""


class LedgerConflitoError(RuntimeError):
    """A mesma `chave_evento` ja existe com OUTRO conteudo: a fonte se contradisse. Nunca e'
    resolvido escolhendo uma das duas linhas."""


def _cadastral(campo: str, valor: str | None) -> None:
    if valor is not None and (not 1 <= len(valor) <= 128 or _CONTROLE_RE.search(valor)):
        raise ValueError(f"EntradaLedger: {campo} fora do formato")


@dataclass(frozen=True, slots=True)
class EntradaLedger:
    """Uma linha do ledger. Validada no construtor com os MESMOS limites dos CHECKs da 0032, para
    que um valor invalido seja recusado antes de qualquer rede."""

    tenant: str
    prestador_id: str
    origem: Origem
    dimensao: Dimensao
    valor: str
    chave_evento: str
    fonte_ref: str
    evidencia_ref: str
    versao_projecao: str
    data_efeito: date | None = None
    tipo_prestador: str | None = None
    especialidade: str | None = None
    regiao_saude: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant or len(self.tenant) > 512 or _TENANT_PROIBIDO_RE.search(self.tenant):
            raise ValueError("EntradaLedger: tenant fora do formato")
        if not PRESTADOR_ID_RE.fullmatch(self.prestador_id):
            raise ValueError("EntradaLedger: prestador_id fora do formato")
        if self.origem not in _ORIGENS_POR_DIMENSAO[self.dimensao]:
            raise ValueError("EntradaLedger: origem nao produz esta dimensao")
        if self.valor not in _VALORES_POR_DIMENSAO[self.dimensao]:
            raise ValueError("EntradaLedger: valor fora do enum da dimensao")
        if not _CHAVE_EVENTO_RE.fullmatch(self.chave_evento):
            raise ValueError("EntradaLedger: chave_evento fora do formato")
        if not _FONTE_REF_RE.fullmatch(self.fonte_ref):
            raise ValueError("EntradaLedger: fonte_ref fora do formato")
        if (
            not self.evidencia_ref
            or len(self.evidencia_ref) > 512
            or _EVIDENCIA_PROIBIDA_RE.search(self.evidencia_ref)
        ):
            raise ValueError("EntradaLedger: evidencia_ref fora do formato")
        if not _VERSAO_RE.fullmatch(self.versao_projecao):
            raise ValueError("EntradaLedger: versao_projecao fora do formato")
        if self.tipo_prestador is not None and not _TIPO_PRESTADOR_RE.fullmatch(self.tipo_prestador):
            raise ValueError("EntradaLedger: tipo_prestador fora do formato")
        _cadastral("especialidade", self.especialidade)
        _cadastral("regiao_saude", self.regiao_saude)


class SituacaoLida(StrEnum):
    """O que um LEITOR do ledger pode concluir sobre a situacao legal de um prestador."""

    CREDENCIADO = "CREDENCIADO"
    DESCREDENCIADO = "DESCREDENCIADO"
    DESCONHECIDO = "DESCONHECIDO"  # nenhuma linha de situacao: nem baseline nem CRED
    INDISPONIVEL = "INDISPONIVEL"  # a leitura falhou: nunca vira status


@dataclass(frozen=True, slots=True)
class LeituraSituacao:
    situacao: SituacaoLida
    data_efeito: date | None = None
    origem: Origem | None = None


@runtime_checkable
class LedgerStatusPrestador(Protocol):
    """A porta que o reconciliador e a importacao da baseline usam. So' acrescenta e le."""

    async def registrar(self, entrada: EntradaLedger) -> bool:
        """Grava a linha. `True` se gravou, `False` se o MESMO evento ja estava gravado."""
        ...

    async def historico(self, tenant: str, prestador_id: str) -> tuple[EntradaLedger, ...]:
        """Todas as linhas do prestador, na ordem de registro."""
        ...

    async def prestadores(self, tenant: str) -> tuple[str, ...]:
        """Os prestadores que o ledger ja conhece (universo da varredura noturna)."""
        ...


def situacao_registrada(historico: tuple[EntradaLedger, ...]) -> LeituraSituacao:
    """A ultima linha de SITUACAO registrada (ordem de registro), ou `DESCONHECIDO`."""
    ultima: EntradaLedger | None = None
    for entrada in historico:
        if entrada.dimensao is Dimensao.SITUACAO:
            ultima = entrada
    if ultima is None:
        return LeituraSituacao(SituacaoLida.DESCONHECIDO)
    return LeituraSituacao(SituacaoLida(ultima.valor), ultima.data_efeito, ultima.origem)


async def ler_situacao(ledger: LedgerStatusPrestador, tenant: str, prestador_id: str) -> LeituraSituacao:
    """Leitura com falha FECHADA: qualquer erro do ledger vira `INDISPONIVEL`."""
    try:
        historico = await ledger.historico(tenant, prestador_id)
    except Exception:  # falha fechada: "nao sei" nunca vira um status
        return LeituraSituacao(SituacaoLida.INDISPONIVEL)
    return situacao_registrada(historico)


_COLUNAS: Final = (
    "tenant",
    "prestador_id",
    "origem",
    "dimensao",
    "valor",
    "data_efeito",
    "chave_evento",
    "fonte_ref",
    "evidencia_ref",
    "tipo_prestador",
    "especialidade",
    "regiao_saude",
    "versao_projecao",
)
_INSERT_SQL: Final = (
    f"INSERT INTO prestador_status_ledger ({', '.join(_COLUNAS)}) "
    f"VALUES ({', '.join(f'${i}' for i in range(1, len(_COLUNAS) + 1))}) "
    "ON CONFLICT (tenant, chave_evento) DO NOTHING RETURNING id"
)
_SELECT_CHAVE_SQL: Final = (
    f"SELECT {', '.join(_COLUNAS)} FROM prestador_status_ledger WHERE tenant = $1 AND chave_evento = $2"
)
_SELECT_HISTORICO_SQL: Final = (
    f"SELECT {', '.join(_COLUNAS)} FROM prestador_status_ledger "
    "WHERE tenant = $1 AND prestador_id = $2 ORDER BY id"
)
_SELECT_PRESTADORES_SQL: Final = (
    "SELECT DISTINCT prestador_id FROM prestador_status_ledger WHERE tenant = $1 ORDER BY prestador_id"
)


def _params(entrada: EntradaLedger) -> tuple[Any, ...]:
    return (
        entrada.tenant,
        entrada.prestador_id,
        entrada.origem.value,
        entrada.dimensao.value,
        entrada.valor,
        entrada.data_efeito,
        entrada.chave_evento,
        entrada.fonte_ref,
        entrada.evidencia_ref,
        entrada.tipo_prestador,
        entrada.especialidade,
        entrada.regiao_saude,
        entrada.versao_projecao,
    )


def _entrada(row: Any) -> EntradaLedger:
    return EntradaLedger(
        tenant=row["tenant"],
        prestador_id=row["prestador_id"],
        origem=Origem(row["origem"]),
        dimensao=Dimensao(row["dimensao"]),
        valor=row["valor"],
        data_efeito=row["data_efeito"],
        chave_evento=row["chave_evento"],
        fonte_ref=row["fonte_ref"],
        evidencia_ref=row["evidencia_ref"],
        tipo_prestador=row["tipo_prestador"],
        especialidade=row["especialidade"],
        regiao_saude=row["regiao_saude"],
        versao_projecao=row["versao_projecao"],
    )


Conectar = Callable[[], Awaitable[Any]]


class PostgresLedgerStatusPrestador:
    """`LedgerStatusPrestador` sobre a 0032, via asyncpg, no schema do tenant (DL-0017).

    Uma conexao por operacao (o volume e' o de uma varredura noturna, nao o de um caminho
    quente). `conectar` e' injetavel para os testes; o padrao abre `asyncpg.connect(dsn)`.
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

    def _exige_tenant(self, tenant: str) -> None:
        if tenant != self._tenant:
            # Uma linha de outro tenant no schema deste: recusa de programacao, antes da rede.
            raise ValueError("PostgresLedgerStatusPrestador: tenant da operacao difere do store")

    async def _abrir(self) -> Any:
        conn = await self._conectar()
        await conn.execute(f'SET search_path TO "{self._schema}", public')
        return conn

    async def registrar(self, entrada: EntradaLedger) -> bool:
        self._exige_tenant(entrada.tenant)
        try:
            conn = await self._abrir()
            try:
                async with conn.transaction():
                    gravado = await conn.fetchval(_INSERT_SQL, *_params(entrada))
                    if gravado is not None:
                        return True
                    existente = await conn.fetchrow(_SELECT_CHAVE_SQL, entrada.tenant, entrada.chave_evento)
            finally:
                await conn.close()
        except Exception as exc:
            raise LedgerIndisponivelError(
                f"prestador_status_ledger indisponivel: {type(exc).__name__}"
            ) from exc
        if existente is None or _entrada(existente) != entrada:
            raise LedgerConflitoError(f"chave_evento {entrada.chave_evento!r} ja gravada com outro conteudo")
        return False

    async def historico(self, tenant: str, prestador_id: str) -> tuple[EntradaLedger, ...]:
        self._exige_tenant(tenant)
        try:
            conn = await self._abrir()
            try:
                rows = await conn.fetch(_SELECT_HISTORICO_SQL, tenant, prestador_id)
            finally:
                await conn.close()
        except Exception as exc:
            raise LedgerIndisponivelError(
                f"prestador_status_ledger indisponivel: {type(exc).__name__}"
            ) from exc
        return tuple(_entrada(row) for row in rows)

    async def prestadores(self, tenant: str) -> tuple[str, ...]:
        self._exige_tenant(tenant)
        try:
            conn = await self._abrir()
            try:
                rows = await conn.fetch(_SELECT_PRESTADORES_SQL, tenant)
            finally:
                await conn.close()
        except Exception as exc:
            raise LedgerIndisponivelError(
                f"prestador_status_ledger indisponivel: {type(exc).__name__}"
            ) from exc
        return tuple(str(row["prestador_id"]) for row in rows)


__all__ = [
    "PRESTADOR_ID_RE",
    "EntradaLedger",
    "LedgerConflitoError",
    "LedgerIndisponivelError",
    "LedgerStatusPrestador",
    "LeituraSituacao",
    "PostgresLedgerStatusPrestador",
    "SituacaoLida",
    "ler_situacao",
    "situacao_registrada",
]
