"""LIMITE DE FALHAS POR CPF, somando todas as conversas (D-04 do red team; decisao do dono em 15/10/2026).

O defeito medido (13/10/2026): o bloqueio de data de nascimento valia por CONVERSA. Nove datas erradas
contra o
mesmo CPF, espalhadas por tres numeros, e o quarto numero verificou com a data certa: cada conversa so' gastou
tres tentativas, e quem tem varios numeros tem tentativas sem fim.

A REGRA (decisao do dono): 3 falhas de verificacao por CPF em 6 horas, de qualquer conversa. Passado o
limite, o
CPF fica bloqueado ate' o fim da janela, para TODO mundo — inclusive para o dono do CPF, que e' o custo
aceito: quem erra a data de outra pessoa 3 vezes a trava por 6 h. O bloqueio abre atendimento humano.

O QUE CONTA como falha: um CPF VALIDO com o fator (data de nascimento) que nao confere. NAO contam formato
invalido, CPF com digito errado (recusa local, sem AMH), nem falha de infraestrutura (AMH fora). Um SUCESSO
NAO
zera o contador, pelo mesmo motivo da janela por conversa (#700): quem acerta o proprio documento nao pode
limpar as tentativas feitas contra o de outra pessoa. A janela e' FIXA, aberta pela primeira falha.

O QUE FICA NO BANCO: so' uma chave DERIVADA do hash de verificacao (nunca o CPF, nunca o hash que a AMH
recebe: se a tabela vazasse, nao serviria para consultar a AMH), a contagem, dois carimbos e se a janela
ANTERIOR terminou bloqueada. A linha some sozinha uma janela depois de a sua passar (`expurgar`): a janela
seguinte precisa saber se a anterior bloqueou. Falha de banco PROPAGA: o turno falha e a Meta reentrega —
nunca "sem limite".

RISCO ACEITO (DL-0109, revisao do #785): quem conhece o CPF de alguem pode re-travar a verificacao dessa
pessoa indefinidamente (3 datas erradas a cada 6 h, de qualquer numero, inclusive pelo caminho de
documento). Mitigacao: o bloqueio abre atendente. ALARME: o MESMO CPF bloqueado em duas janelas seguidas
(`registrar_falha` devolve `True`) vira o log `acesso_cpf_bloqueio_reincidente` + a metrica
`maezo_acesso_cpf_bloqueio_reincidente_total`, com uma MARCA curta (8 hex da chave, que ja' deriva do HMAC
de verificacao) — nunca o CPF nem o hash.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant

#: A decisao do dono, em dois numeros. Mudar qualquer um e' decisao de produto (DL), nao ajuste de codigo.
MAX_FALHAS_POR_CPF: Final[int] = 3
JANELA_POR_CPF: Final[timedelta] = timedelta(hours=6)

TABELA: Final[str] = "acesso_falhas_por_cpf"
_ROTULO_DA_CHAVE: Final[bytes] = b"acesso-limite-cpf:v1:"
_PREFIXO_HEX: Final[int] = 64
#: A marca do alarme: so' o comeco da chave (32 bits), o bastante para correlacionar dois alarmes do mesmo
#: CPF no log, pouco para servir de chave de qualquer coisa.
_TAMANHO_DA_MARCA: Final[int] = 8


def marca_do_alarme(chave: str) -> str:
    """A marca curta que o alarme de re-bloqueio leva ao log: os 8 primeiros hex da chave derivada."""
    return chave[:_TAMANHO_DA_MARCA]


def chave_do_cpf(hash_de_verificacao: str) -> str:
    """A chave da linha: SHA-256 do hash de verificacao com um rotulo de proposito.

    Derivada, nao o proprio hash: o hash de verificacao e' o que a AMH recebe para resolver um documento, e
    guarda-lo aqui daria a quem lesse a tabela uma chave de consulta. Esta derivacao so' serve a este
    limite."""
    return hashlib.sha256(_ROTULO_DA_CHAVE + hash_de_verificacao.encode("utf-8")).hexdigest()


class LimitePorCpf(Protocol):
    """O que o servico de acesso precisa: perguntar se o CPF esta' bloqueado e registrar uma falha."""

    async def bloqueado_ate(self, chave: str, agora: datetime) -> datetime | None:
        """O fim do bloqueio, se o CPF esta' bloqueado AGORA; `None` se pode tentar."""
        ...

    async def registrar_falha(self, chave: str, agora: datetime) -> bool:
        """Soma uma falha. `True` so' quando ESTA falha bloqueou o CPF (chegou ao limite) e a janela
        anterior tambem terminou bloqueada: o alarme de re-bloqueio (revisao do #785)."""
        ...

    async def expurgar(self, agora: datetime) -> int:
        """Apaga as linhas cuja janela ja' passou (so' servem enquanto a janela vale). Devolve quantas."""
        ...


class LimitePorCpfEmMemoria:
    """Para teste e para o desenvolvimento local. A janela e a regra sao as da producao."""

    def __init__(self, *, max_falhas: int = MAX_FALHAS_POR_CPF, janela: timedelta = JANELA_POR_CPF) -> None:
        self._max = max_falhas
        self._janela = janela
        #: chave -> (falhas, inicio da janela, a janela anterior terminou bloqueada)
        self._linhas: dict[str, tuple[int, datetime, bool]] = {}

    def __repr__(self) -> str:
        return "LimitePorCpfEmMemoria(<redacted>)"

    async def bloqueado_ate(self, chave: str, agora: datetime) -> datetime | None:
        linha = self._linhas.get(chave)
        if linha is None:
            return None
        falhas, inicio, _ = linha
        fim = inicio + self._janela
        return fim if falhas >= self._max and agora < fim else None

    async def registrar_falha(self, chave: str, agora: datetime) -> bool:
        linha = self._linhas.get(chave)
        if linha is None:
            novo = (1, agora, False)
        elif agora >= linha[1] + self._janela:
            # Janela nova: lembra se a anterior terminou bloqueada e se esta comeca logo depois dela.
            seguida = linha[0] >= self._max and agora < linha[1] + 2 * self._janela
            novo = (1, agora, seguida)
        else:
            novo = (linha[0] + 1, linha[1], linha[2])
        self._linhas[chave] = novo
        return novo[0] == self._max and novo[2]

    async def expurgar(self, agora: datetime) -> int:
        vencidas = [c for c, (_, inicio, _) in self._linhas.items() if agora >= inicio + 2 * self._janela]
        for chave in vencidas:
            del self._linhas[chave]
        return len(vencidas)


# A atualizacao e' UMA instrucao atomica: duas conversas falhando ao mesmo tempo nao perdem contagem. Janela
# vencida reabre com 1 e o carimbo novo, e grava se a anterior terminou bloqueada (falhas >= $5) e se esta
# comeca dentro de uma janela depois dela; dentro da janela soma 1 e mantem o carimbo (a janela e' fixa).
# Os SETs leem a linha ANTIGA (semantica do UPDATE), entao a ordem entre eles nao importa.
_REGISTRAR_SQL: Final[str] = f"""
INSERT INTO {TABELA} (tenant, chave, falhas, janela_inicio, ultima_em, janela_anterior_bloqueada)
VALUES ($1, $2, 1, $3, $3, false)
ON CONFLICT (tenant, chave) DO UPDATE SET
    falhas = CASE WHEN {TABELA}.janela_inicio + $4::interval <= $3 THEN 1 ELSE {TABELA}.falhas + 1 END,
    janela_inicio = CASE
        WHEN {TABELA}.janela_inicio + $4::interval <= $3 THEN $3
        ELSE {TABELA}.janela_inicio
    END,
    janela_anterior_bloqueada = CASE
        WHEN {TABELA}.janela_inicio + $4::interval <= $3
            THEN {TABELA}.falhas >= $5 AND {TABELA}.janela_inicio + 2 * $4::interval > $3
        ELSE {TABELA}.janela_anterior_bloqueada
    END,
    ultima_em = $3
RETURNING falhas, janela_anterior_bloqueada
"""
_LER_SQL: Final[str] = f"SELECT falhas, janela_inicio FROM {TABELA} WHERE tenant = $1 AND chave = $2"
# Uma janela a mais: a seguinte precisa ler se esta bloqueou (o alarme de re-bloqueio).
_EXPURGO_SQL: Final[str] = (
    f"DELETE FROM {TABELA} WHERE tenant = $1 AND janela_inicio + 2 * $3::interval <= $2"
)


class LimitePorCpfStoreError(RuntimeError):
    """Falha do store. A mensagem e' sempre um token de classe, nunca dado de beneficiario."""


class PostgresLimitePorCpf:
    """`LimitePorCpf` sobre asyncpg, por tenant (`search_path` pinado em todo acquire)."""

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        pool: Any = None,
        max_falhas: int = MAX_FALHAS_POR_CPF,
        janela: timedelta = JANELA_POR_CPF,
    ) -> None:
        if max_falhas < 1 or janela <= timedelta(0):
            raise ValueError("limite por cpf: max_falhas >= 1 e janela positiva")
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool
        self._max = max_falhas
        self._janela = janela

    def __repr__(self) -> str:
        return "PostgresLimitePorCpf(<redacted>)"

    @staticmethod
    def _conferir(chave: str) -> None:
        if len(chave) != _PREFIXO_HEX or any(c not in "0123456789abcdef" for c in chave):
            raise LimitePorCpfStoreError("limite por cpf: chave fora do formato")

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def bloqueado_ate(self, chave: str, agora: datetime) -> datetime | None:
        self._conferir(chave)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            linha = await conn.fetchrow(_LER_SQL, self._tenant, chave)
        if linha is None:
            return None
        fim = linha["janela_inicio"] + self._janela
        return fim if int(linha["falhas"]) >= self._max and agora < fim else None

    async def registrar_falha(self, chave: str, agora: datetime) -> bool:
        self._conferir(chave)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            linha = await conn.fetchrow(_REGISTRAR_SQL, self._tenant, chave, agora, self._janela, self._max)
        if linha is None:  # pragma: no cover - INSERT ... RETURNING sempre devolve a linha
            return False
        return int(linha["falhas"]) == self._max and bool(linha["janela_anterior_bloqueada"])

    async def expurgar(self, agora: datetime) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(_EXPURGO_SQL, self._tenant, agora, self._janela)
        # asyncpg devolve "DELETE <n>"
        try:
            return int(str(status).rsplit(" ", 1)[-1])
        except ValueError:
            return 0


__all__ = [
    "JANELA_POR_CPF",
    "MAX_FALHAS_POR_CPF",
    "LimitePorCpf",
    "LimitePorCpfEmMemoria",
    "LimitePorCpfStoreError",
    "PostgresLimitePorCpf",
    "chave_do_cpf",
    "marca_do_alarme",
]
