"""Avisos de `sinalAuditoria` do K1 do rco: expansao pelas filas do K6 e o outbox duravel (DL-0184, 2a).

O QUE ISTO E. O pacote K1 2.3.0 (DL-0183) pode devolver, numa composicao, `sinalAuditoria` nao vazio; o K6
pinado declara, por sinal, as filas a avisar e o NOME do parametro do prazo (`rco_manifesto.AvisoK6`,
exposto como `rco_pin.RcoRulesPin.avisos`). Este modulo:

1. `avisos_para(sinais, avisos_do_pin)`: funcao pura que expande os sinais em itens, um por fila. Sinal
   vazio nao gera nada; sinal nao vazio sem item no K6 levanta `SinalSemAvisoError` (um `KeyError`: erro de
   programacao, porque o pin ja recusa sinal sem destino, DL-0183 (4.4));
2. `linhas_de_aviso(...)`: as linhas do outbox de uma tarefa, com `chave_idempotencia` = sha256 de
   `tenant:task_id:composicao:sinal:fila` e a forma de cada campo conferida (`AvisoInvalidoError`);
3. `PostgresAvisosRco`: o outbox sobre a migracao 0036 (`rco_aviso_auditoria`), com insercao idempotente
   (`ON CONFLICT DO NOTHING`), reivindicacao com lease, marcacao `entregue`/`falhou` e `reprocessar_falhas`.

QUEM ESCREVE. Um unico escritor: `WorkerHarness._enfileirar_avisos_rco`, DEPOIS de completar a tarefa, com
teto de tempo proprio e FAIL-OPEN (nunca levanta, nunca atrasa nem muda a decisao). Quem publica: o relay
`maezo.platform.rco_avisos_relay` (`--once`, `--reprocessar`). O consumidor das mensagens e' de unidade
posterior (2b).

O QUE VIAJA. Linha e mensagem levam so ids que a auditoria ja usa (`tenant`, `task_id`), o nome da
composicao, o sinal, a fila e o `prazo_parametro` do pin (`montar_payload`). Nunca dado de beneficiario,
chave de negocio, linha da DMN ou texto livre; o erro gravado e' um codigo fechado (`ERROS`).

O indicador `efeito_k1_habilitado` (DL-0181 (4)(b)) NAO e' lido aqui: sombra e efeito enfileiram igual.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform.rco_manifesto import AvisoK6
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

#: O `type` da mensagem em `operadora.notifications.internal` (forma `{familia}.{acao}` da casa).
TIPO_NOTIFICACAO: Final[str] = "rco.aviso_auditoria"
TOPICO: Final[str] = "operadora.notifications.internal"
TABELA: Final[str] = "rco_aviso_auditoria"

#: Estados da linha (CHECK da 0036).
STATUS: Final[frozenset[str]] = frozenset({"pendente", "reivindicada", "entregue", "falhou"})
#: Codigos fechados de `ultimo_erro` (CHECK da 0036).
ERRO_PUBLICACAO: Final[str] = "publicacao_falhou"
ERROS: Final[frozenset[str]] = frozenset({ERRO_PUBLICACAO})
#: Desfechos de `maezo_rco_aviso_total` (= `observability.RCO_AVISO_DESFECHOS`, paridade em teste).
DESFECHOS: Final[frozenset[str]] = frozenset(
    {
        "enfileirado",
        "repetido",
        "falha_enfileirar",
        "teto_estourado",
        "nao_configurado",
        "invalido",
        "entregue",
        "falha_publicar",
        "reprocessado",
    }
)

#: Forma de cada campo (a mesma dos CHECKs da 0036).
_TOKEN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TASK_ID: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_COMPOSICAO: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]{1,120}")
_PRAZO: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_TENANT_PROIBIDO: Final[re.Pattern[str]] = re.compile(r"[\s\x00-\x1f\x7f/?#]")

#: Tetos de uma operacao do outbox (o enfileiramento tem ainda o teto total do harness).
LOCK_TIMEOUT: Final[str] = "2s"
STATEMENT_TIMEOUT: Final[str] = "3s"
_FECHAR_TIMEOUT_S: Final[float] = 2.0
DEFAULT_LOTE: Final[int] = 50
DEFAULT_LEASE_S: Final[float] = 60.0


class SinalSemAvisoError(KeyError):
    """Sinal nao vazio sem item em `avisos` do K6 pinado: erro de programacao (o pin ja o recusa)."""


class AvisoInvalidoError(ValueError):
    """Campo de uma linha de aviso fora da forma da 0036. A mensagem e' so o NOME do campo."""


class OutboxAvisosIndisponivelError(RuntimeError):
    """O outbox nao respondeu. A mensagem leva so o tipo do erro do driver (nunca a linha)."""


@dataclass(frozen=True, slots=True)
class ItemAviso:
    """Um destino de um sinal: a fila e o nome do parametro do prazo, do K6 pinado."""

    sinal: str
    fila: str
    prazo_parametro: str


@dataclass(frozen=True, slots=True)
class LinhaAviso:
    """Uma linha do outbox (uma por aviso e fila)."""

    tenant: str
    chave_idempotencia: str
    task_id: str
    composicao: str
    sinal: str
    fila: str
    prazo_parametro: str


@dataclass(frozen=True, slots=True)
class AvisoReivindicado:
    """Uma linha reivindicada pelo relay, com o que a mensagem leva."""

    id: int
    tentativas: int
    linha: LinhaAviso


def avisos_para(sinais: Iterable[str], avisos_do_pin: Mapping[str, AvisoK6]) -> tuple[ItemAviso, ...]:
    """Expande os sinais em itens, um por fila do K6, na ordem dos sinais e das filas, sem repeticao.

    Sinal vazio (`""` ou so espacos) nao gera nada. Sinal nao vazio sem item em `avisos_do_pin` levanta
    `SinalSemAvisoError`: o pin recusa pacote com sinal sem destino (DL-0183 (4.4)), entao chegar aqui e'
    defeito de quem chamou, nunca dado do caso. Os destinos sao DADO do pin; nada e' escrito no codigo.
    """
    itens: list[ItemAviso] = []
    vistos: set[tuple[str, str]] = set()
    for sinal in sinais:
        if not sinal.strip():
            continue
        aviso = avisos_do_pin.get(sinal)
        if aviso is None:
            raise SinalSemAvisoError(sinal)
        for fila in aviso.filas:
            if (sinal, fila) in vistos:
                continue
            vistos.add((sinal, fila))
            itens.append(ItemAviso(sinal=sinal, fila=fila, prazo_parametro=aviso.prazo_parametro))
    return tuple(itens)


def chave_idempotencia(tenant: str, task_id: str, composicao: str, sinal: str, fila: str) -> str:
    """sha256 de `tenant:task_id:composicao:sinal:fila`: a re-entrega da tarefa cai na mesma chave."""
    return hashlib.sha256(f"{tenant}:{task_id}:{composicao}:{sinal}:{fila}".encode()).hexdigest()


def _conferir(campo: str, valor: str, forma: re.Pattern[str]) -> None:
    if not isinstance(valor, str) or forma.fullmatch(valor) is None:
        raise AvisoInvalidoError(campo)


def linhas_de_aviso(
    *,
    tenant: str,
    task_id: str,
    composicoes: Iterable[tuple[str, Sequence[str]]],
    avisos_do_pin: Mapping[str, AvisoK6],
) -> tuple[LinhaAviso, ...]:
    """As linhas do outbox de uma tarefa: para cada (composicao, sinais), `avisos_para` e uma linha por item.

    Confere a forma de cada campo contra os CHECKs da 0036 (`AvisoInvalidoError` com o nome do campo).
    """
    if not isinstance(tenant, str) or not tenant or len(tenant) > 512 or _TENANT_PROIBIDO.search(tenant):
        raise AvisoInvalidoError("tenant")
    _conferir("task_id", task_id, _TASK_ID)
    linhas: list[LinhaAviso] = []
    for composicao, sinais in composicoes:
        itens = avisos_para(sinais, avisos_do_pin)
        if not itens:
            continue
        _conferir("composicao", composicao, _COMPOSICAO)
        for item in itens:
            _conferir("sinal", item.sinal, _TOKEN)
            _conferir("fila", item.fila, _TOKEN)
            _conferir("prazo_parametro", item.prazo_parametro, _PRAZO)
            linhas.append(
                LinhaAviso(
                    tenant=tenant,
                    chave_idempotencia=chave_idempotencia(tenant, task_id, composicao, item.sinal, item.fila),
                    task_id=task_id,
                    composicao=composicao,
                    sinal=item.sinal,
                    fila=item.fila,
                    prazo_parametro=item.prazo_parametro,
                )
            )
    return tuple(linhas)


def montar_payload(linha: LinhaAviso) -> dict[str, str]:
    """A mensagem de `operadora.notifications.internal`: so ids, o sinal, a fila e o parametro do prazo."""
    return {
        "type": TIPO_NOTIFICACAO,
        "tenant_id": linha.tenant,
        "task_id": linha.task_id,
        "composicao": linha.composicao,
        "sinal": linha.sinal,
        "fila": linha.fila,
        "prazo_parametro": linha.prazo_parametro,
        "chave_idempotencia": linha.chave_idempotencia,
    }


def chave_de_particao(linha: LinhaAviso) -> str:
    """`{tenant}|{task_id}`: os avisos de uma tarefa na mesma particao (forma de `partition_key`)."""
    return f"{linha.tenant}|{linha.task_id}"


# ---------------------------------------------------------------------------------------------------
# Banco (migracao 0036)
# ---------------------------------------------------------------------------------------------------

COLUNAS: Final[tuple[str, ...]] = (
    "tenant",
    "chave_idempotencia",
    "task_id",
    "composicao",
    "sinal",
    "fila",
    "prazo_parametro",
)

#: Uma instrucao para o lote da tarefa; devolve so as linhas que entraram (as repetidas caem no conflito).
_ENFILEIRAR_SQL: Final[str] = (
    f"INSERT INTO {TABELA} ({', '.join(COLUNAS)}) "
    "SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::text[], "
    "$7::text[]) "
    "ON CONFLICT ON CONSTRAINT rco_aviso_auditoria_idempotencia DO NOTHING RETURNING id"
)
#: Reivindica as pendentes e as de lease vencido, mais antigas primeiro, sem bloquear outro relay.
_REIVINDICAR_SQL: Final[str] = (
    f"UPDATE {TABELA} SET status = 'reivindicada', reivindicada_por = $2, "
    "reivindicacao_expira_em = clock_timestamp() + make_interval(secs => $3), "
    "atualizado_em = clock_timestamp() "
    f"WHERE id IN (SELECT id FROM {TABELA} WHERE status = 'pendente' "
    "OR (status = 'reivindicada' AND reivindicacao_expira_em < clock_timestamp()) "
    "ORDER BY criado_em, id LIMIT $1 FOR UPDATE SKIP LOCKED) "
    f"RETURNING id, tentativas, {', '.join(COLUNAS)}"
)
_MARCAR_ENTREGUE_SQL: Final[str] = (
    f"UPDATE {TABELA} SET status = 'entregue', entregue_em = clock_timestamp(), tentativas = tentativas + 1, "
    "reivindicada_por = NULL, reivindicacao_expira_em = NULL, atualizado_em = clock_timestamp() "
    "WHERE id = ANY($1::bigint[]) AND status = 'reivindicada' AND reivindicada_por = $2 RETURNING id"
)
_MARCAR_FALHOU_SQL: Final[str] = (
    f"UPDATE {TABELA} SET status = 'falhou', ultimo_erro = $3, tentativas = tentativas + 1, "
    "reivindicada_por = NULL, reivindicacao_expira_em = NULL, atualizado_em = clock_timestamp() "
    "WHERE id = ANY($1::bigint[]) AND status = 'reivindicada' AND reivindicada_por = $2 RETURNING id"
)
#: Devolve a `pendente` o resto de um lote interrompido (nao conta tentativa: nada foi publicado).
_LIBERAR_SQL: Final[str] = (
    f"UPDATE {TABELA} SET status = 'pendente', reivindicada_por = NULL, reivindicacao_expira_em = NULL, "
    "atualizado_em = clock_timestamp() "
    "WHERE id = ANY($1::bigint[]) AND status = 'reivindicada' AND reivindicada_por = $2 RETURNING id"
)
#: `--reprocessar`: as que falharam voltam a `pendente` (o codigo e as tentativas ficam, como historico).
_REPROCESSAR_SQL: Final[str] = (
    f"UPDATE {TABELA} SET status = 'pendente', atualizado_em = clock_timestamp() "
    "WHERE status = 'falhou' RETURNING id"
)
_CONTAR_SQL: Final[str] = f"SELECT status, count(*) AS n FROM {TABELA} GROUP BY status"

Conectar = Callable[[], Awaitable[Any]]


class OutboxAvisosRco(Protocol):
    """A porta que o harness usa: grava as linhas de uma tarefa e devolve quantas entraram."""

    @property
    def tenant(self) -> str: ...

    async def enfileirar(self, linhas: Sequence[LinhaAviso]) -> int: ...


async def _fechar(conn: Any) -> None:
    """Fecha a conexao com limite; se travar (ou o chamador cancelou), `terminate()` derruba o socket."""
    try:
        await asyncio.wait_for(conn.close(), _FECHAR_TIMEOUT_S)
    except asyncio.CancelledError:
        conn.terminate()
        raise
    except Exception:
        conn.terminate()  # fechar e' higiene: a falha de fechar nunca vira falha do outbox


class PostgresAvisosRco:
    """O outbox `rco_aviso_auditoria` (0036) via asyncpg, no schema do tenant (DL-0017).

    Uma conexao por operacao (mesma razao de `rco_registro.PostgresRegistroDecisoesRco`: o worker pode
    rodar cada tarefa num loop proprio). Erro de banco vira `OutboxAvisosIndisponivelError` so com o tipo.
    """

    __slots__ = ("_conectar", "_schema", "_tenant")

    def __init__(self, *, dsn: str, tenant: str, conectar: Conectar | None = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        if conectar is None:
            import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

            plain = normalize_dsn(dsn)

            async def _padrao() -> Any:
                return await asyncpg.connect(plain, timeout=5.0)

            conectar = _padrao
        self._conectar = conectar

    @property
    def tenant(self) -> str:
        return self._tenant

    async def _em_transacao(self, corpo: Callable[[Any], Awaitable[Any]]) -> Any:
        try:
            conn = await self._conectar()
            try:
                await conn.execute(f'SET search_path TO "{self._schema}", public')
                async with conn.transaction(isolation="read_committed"):
                    await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                    await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    return await corpo(conn)
            finally:
                await _fechar(conn)
        except PROGRAMMING_ERRORS:
            raise  # bug do proprio codigo: sobe com a pilha
        except Exception as exc:
            # So o TIPO: a mensagem do asyncpg pode trazer "Failing row contains ..." com a linha.
            raise OutboxAvisosIndisponivelError(f"{TABELA} indisponivel: {type(exc).__name__}") from None

    async def enfileirar(self, linhas: Sequence[LinhaAviso]) -> int:
        """Grava as linhas de uma tarefa numa transacao; devolve quantas ENTRARAM (as ja gravadas, nao)."""
        if not linhas:
            return 0
        if any(linha.tenant != self._tenant for linha in linhas):
            raise ValueError("PostgresAvisosRco: tenant da linha difere do store")
        colunas = [[getattr(linha, c) for linha in linhas] for c in COLUNAS]

        async def corpo(conn: Any) -> int:
            return len(await conn.fetch(_ENFILEIRAR_SQL, *colunas))

        return int(await self._em_transacao(corpo))

    async def reivindicar(
        self, *, reivindicada_por: str, lote: int = DEFAULT_LOTE, lease_s: float = DEFAULT_LEASE_S
    ) -> list[AvisoReivindicado]:
        async def corpo(conn: Any) -> list[Any]:
            return list(await conn.fetch(_REIVINDICAR_SQL, int(lote), reivindicada_por, float(lease_s)))

        rows = await self._em_transacao(corpo)
        out = [
            AvisoReivindicado(
                id=int(r["id"]),
                tentativas=int(r["tentativas"]),
                linha=LinhaAviso(**{c: str(r[c]) for c in COLUNAS}),
            )
            for r in rows
        ]
        out.sort(key=lambda a: a.id)
        return out

    async def _marcar(self, sql: str, ids: Sequence[int], *args: Any) -> int:
        if not ids:
            return 0

        async def corpo(conn: Any) -> int:
            return len(await conn.fetch(sql, [int(i) for i in ids], *args))

        return int(await self._em_transacao(corpo))

    async def marcar_entregue(self, ids: Sequence[int], *, reivindicada_por: str) -> int:
        return await self._marcar(_MARCAR_ENTREGUE_SQL, ids, reivindicada_por)

    async def marcar_falhou(self, ids: Sequence[int], *, reivindicada_por: str, erro: str) -> int:
        if erro not in ERROS:
            raise ValueError("erro fora do vocabulario fechado")
        return await self._marcar(_MARCAR_FALHOU_SQL, ids, reivindicada_por, erro)

    async def liberar(self, ids: Sequence[int], *, reivindicada_por: str) -> int:
        return await self._marcar(_LIBERAR_SQL, ids, reivindicada_por)

    async def reprocessar_falhas(self) -> int:
        async def corpo(conn: Any) -> int:
            return len(await conn.fetch(_REPROCESSAR_SQL))

        return int(await self._em_transacao(corpo))

    async def contar_por_status(self) -> dict[str, int]:
        async def corpo(conn: Any) -> dict[str, int]:
            return {str(r["status"]): int(r["n"]) for r in await conn.fetch(_CONTAR_SQL)}

        result: dict[str, int] = await self._em_transacao(corpo)
        return result


__all__ = [
    "COLUNAS",
    "DESFECHOS",
    "ERROS",
    "ERRO_PUBLICACAO",
    "STATUS",
    "TABELA",
    "TIPO_NOTIFICACAO",
    "TOPICO",
    "AvisoInvalidoError",
    "AvisoReivindicado",
    "ItemAviso",
    "LinhaAviso",
    "OutboxAvisosIndisponivelError",
    "OutboxAvisosRco",
    "PostgresAvisosRco",
    "SinalSemAvisoError",
    "avisos_para",
    "chave_de_particao",
    "chave_idempotencia",
    "linhas_de_aviso",
    "montar_payload",
]
