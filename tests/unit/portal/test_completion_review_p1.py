"""Os tres achados do review de DL-0049 (rodaquino, 21/09/2026), cada um com o seu par minimo.

O que cada um era, em uma linha:

  P1  a revalidacao acontecia antes de GRAVAR o intent, e o intent e' um INSERT sob advisory lock
      por tenant: sob contencao ele espera, e uma autoridade revogada nessa espera ainda mandava o
      efeito ao motor. Agora ha uma cerca ENTRE a claim e o efeito.
  P1  `PostgresAuditSink` aplicava o `search_path` apenas por `setup=`, hook que so' existe no
      pool que ELE cria. Com o pool emprestado do plano humano (o caso desta entrega) o hook nunca
      rodava, e todo SQL desta classe nomeia a tabela SEM schema.
  P2  o CORS credenciado era global: a origem do canal de teste alcancava todo o BFF, inclusive
      rotas que nao verificam `Origin` porque nunca precisaram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.portal.api.app import CROSS_ORIGIN_PATHS, PathScopedCORS

# --- P2: o CORS credenciado nao vaza para o resto do BFF ---------------------------------------


@dataclass
class _AppQueRegistra:
    """App ASGI minimo que anota se foi chamado e responde 204."""

    chamado: list[str] = field(default_factory=list)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any, /) -> None:
        self.chamado.append(scope.get("path", ""))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _preflight(middleware: PathScopedCORS, caminho: str) -> dict[str, str]:
    """Um preflight de verdade, e devolve os cabecalhos da resposta em minuscula."""
    capturado: dict[str, str] = {}

    async def send(mensagem: dict[str, Any]) -> None:
        if mensagem["type"] == "http.response.start":
            for nome, valor in mensagem.get("headers", []):
                capturado[nome.decode().lower()] = valor.decode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(
        {
            "type": "http",
            "method": "OPTIONS",
            "path": caminho,
            "headers": [
                (b"origin", b"https://maezo-teste-dev.austa.com.br"),
                (b"access-control-request-method", b"POST"),
            ],
        },
        receive,
        send,
    )
    return capturado


def _middleware(app: Any) -> PathScopedCORS:
    return PathScopedCORS(
        app,
        paths=CROSS_ORIGIN_PATHS,
        allow_origins=["https://maezo-teste-dev.austa.com.br"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "x-csrf-token"],
    )


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/v1/portal/session",
        "/api/v1/portal/tasks",
        "/api/v1/portal/tasks/abc123",
        "/api/v1/portal/tasks/abc123/completion",
    ],
)
async def test_as_quatro_rotas_do_canal_respondem_preflight_credenciado(caminho: str) -> None:
    """Nao-vacuidade: sem isto o recorte poderia ser 'nenhuma rota', que tambem 'nao vaza'."""
    cabecalhos = await _preflight(_middleware(_AppQueRegistra()), caminho)

    assert cabecalhos.get("access-control-allow-origin") == "https://maezo-teste-dev.austa.com.br"
    assert cabecalhos.get("access-control-allow-credentials") == "true"


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/v1/portal/decisions",
        "/api/v1/portal/cases",
        "/api/v1/portal/documents",
        "/api/v1/portal/communications",
        "/api/v1/portal/auth/callback",
        "/api/v1/portal/intake/recovery",
        "/healthz",
        "/api/v1/portal/sessions",  # prefixo PARECIDO, e nao e' o mesmo caminho
        "/api/v1/portal/tasksX",  # idem: `startswith` cru deixaria passar
    ],
)
async def test_nenhuma_outra_rota_ganha_cabecalho_de_cors(caminho: str) -> None:
    """O achado P2: a origem do canal nao pode alcancar o BFF inteiro com credencial."""
    app = _AppQueRegistra()

    cabecalhos = await _preflight(_middleware(app), caminho)

    assert not [n for n in cabecalhos if n.startswith("access-control-")], cabecalhos
    # E a requisicao seguiu para o app em vez de ser respondida pelo middleware.
    assert app.chamado == [caminho]


async def test_websocket_passa_direto() -> None:
    """`scope["type"] != "http"` nao e' assunto de CORS e nao pode virar excecao."""
    app = _AppQueRegistra()
    middleware = _middleware(app)
    enviados: list[dict[str, Any]] = []

    async def send(m: dict[str, Any]) -> None:
        enviados.append(m)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    await middleware({"type": "websocket", "path": "/api/v1/portal/tasks"}, receive, send)

    assert app.chamado == ["/api/v1/portal/tasks"]


def test_a_lista_de_rotas_e_a_que_a_pagina_chama() -> None:
    """Cerca de intencao: se alguem acrescentar um caminho aqui, tem de ser deliberado."""
    assert CROSS_ORIGIN_PATHS == ("/api/v1/portal/session", "/api/v1/portal/tasks")


# --- P1 (2): o `search_path` num pool EMPRESTADO ------------------------------------------------


@dataclass
class _ConexaoQueAnota:
    """Conexao asyncpg minima: guarda os SQLs executados, na ordem."""

    sqls: list[str] = field(default_factory=list)

    async def execute(self, sql: str, *args: Any) -> None:
        self.sqls.append(sql)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sqls.append(sql)
        return "audit_chain"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sqls.append(sql)
        return None

    def transaction(self) -> Any:
        conexao = self

        class _Tx:
            async def __aenter__(self) -> None:
                conexao.sqls.append("BEGIN")

            async def __aexit__(self, *_: Any) -> None:
                conexao.sqls.append("COMMIT")

        return _Tx()


@dataclass
class _PoolEmprestado:
    conexao: _ConexaoQueAnota

    def acquire(self) -> Any:
        conexao = self.conexao

        class _Acquire:
            async def __aenter__(self) -> _ConexaoQueAnota:
                return conexao

            async def __aexit__(self, *_: Any) -> None:
                return None

        return _Acquire()


async def test_pool_emprestado_fixa_o_schema_na_transacao() -> None:
    """O achado: com pool de outro dono, NENHUM `search_path` era aplicado.

    `SET LOCAL` e nao `SET`: a conexao volta para um pool que outro codigo esta usando, e o
    escopo de transacao e' o que torna seguro mexer nela.
    """
    from maezo.gateway.audit_postgres import PostgresAuditSink

    conexao = _ConexaoQueAnota()
    sink = PostgresAuditSink("", "amh", pool=_PoolEmprestado(conexao))  # type: ignore[arg-type]

    await sink.check_ready()

    fixacoes = [s for s in conexao.sqls if "search_path" in s]
    assert fixacoes, f"nenhum search_path aplicado; sqls={conexao.sqls}"
    assert all(s.startswith("SET LOCAL ") for s in fixacoes), fixacoes
    # Dentro da transacao, e antes de qualquer consulta.
    assert conexao.sqls.index("BEGIN") < conexao.sqls.index(fixacoes[0])
    assert conexao.sqls.index(fixacoes[0]) < max(i for i, s in enumerate(conexao.sqls) if "audit_chain" in s)


async def test_pool_proprio_nao_fixa_de_novo() -> None:
    """O par minimo: com pool proprio o `setup=` ja fez isso a cada acquire — repetir e' round trip."""
    from maezo.gateway.audit_postgres import PostgresAuditSink

    conexao = _ConexaoQueAnota()
    sink = PostgresAuditSink("postgresql://x/y", "amh")
    sink._pool = _PoolEmprestado(conexao)  # type: ignore[assignment]

    await sink.check_ready()

    assert [s for s in conexao.sqls if "SET LOCAL" in s] == []


# --- P1 (1): a cerca ENTRE a claim de intent e o efeito -----------------------------------------


def _vencido() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=1)


def _valido() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)
