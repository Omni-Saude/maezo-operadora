"""Reconciliacao dos avisos de `sinalAuditoria`: auditoria duravel x outbox `rco_aviso_auditoria` (DL-0186).

POR QUE EXISTE. O harness grava os avisos no outbox DEPOIS de completar a tarefa, fail-open (DL-0185 (5)): o
outbox fora, o teto estourado, a conclusao incerta ou a queda do processo entre `complete` e a insercao deixam
um aviso sem linha. A auditoria, gravada ANTES de completar, leva `sinaisAuditoria` na entrada de
`dmn_versions` de cada decisao avaliada do grupo; e por ela que o aviso perdido aparece. DL-0185 deixou a
ferramenta para a unidade 2b; e esta.

O QUE FAZ, SO LEITURA. Para uma janela `[desde, ate)` de um tenant:

1. le da cadeia de auditoria (`audit_chain` x `audit_emit_dedup`, migracoes 0002/0005) os registros de
   conclusao (`decision = 'COMPLETE'`) cuja `dmn_versions` tem alguma `sinaisAuditoria`, com a `dedup_key`
   `{tenant}:{task_id}` do harness (`WorkerHarness._audit_dedup_key`);
2. recalcula, com o manifesto e os `avisos` do PIN de bordo, as linhas que o harness teria gravado: a
   composicao de cada decisao (`ManifestoK1.composicao_de`), `rco_avisos.avisos_para` e a
   `chave_idempotencia` de `tenant:task_id:composicao:sinal:fila`. DL-0187: SO a composicao em
   `modo=efeito` espera linha; o modo e lido do registro DURAVEL, a chave `composicaoK1.modo` da mesma entrada
   de `dmn_versions` (gravada por `_audit_ctx.registrar_composicao`). `modo=sombra` nao espera nada e entra
   na contagem `suprimidos_sombra`; entrada sem `composicaoK1` (auditoria anterior ao DL-0187, quando os dois
   modos enfileiravam) segue esperada; `composicaoK1` com modo fora de `MODOS` vira `modo_fora_do_vocabulario`
   e nao espera linha (nunca palpite);
3. le do outbox as chaves que existem (`SELECT`, nunca escreve) e devolve o relatorio.

O RELATORIO e so ids e codigos fechados (`CODIGOS`): `ausente_no_outbox` (o aviso esperado nao tem linha),
`composicao_desconhecida` (decisao com `sinaisAuditoria` fora de toda composicao do manifesto pinado),
`sinal_sem_aviso` (sinal que o K6 pinado nao endereca), `modo_fora_do_vocabulario` (DL-0187: `composicaoK1`
sem modo valido), `chave_auditoria_fora_da_forma` (registro cuja
`dedup_key` nao e `{tenant}:{task_id}`), `aviso_falhou` (a linha esperada existe mas terminou `falhou`; leva o
`ultimo_erro` fechado do outbox, `publicacao_falhou` ou `fila_sem_grupo`: o aviso NAO saiu e espera o
operador). Mais as contagens por `status` das linhas encontradas. Nada do caso, nenhuma linha da DMN, nenhum
texto livre.

LIMITES, ditos: (a) o esperado e calculado com o pin de AGORA; uma auditoria gravada sob outro pin pode
divergir (`composicao_desconhecida`/`sinal_sem_aviso` aparecem entao como codigo, nunca como palpite);
(b) a janela precisa caber na retencao de `audit_emit_dedup` (varredura por idade, 0005); (c) a juncao por
`record_hash` nao tem indice proprio: a janela deve ser curta; (d) o CONSUMO nao e reconciliado: uma linha
`entregue` do outbox prova a publicacao, nao a linha `rco_aviso_grupo` (0037) do inbox (fora do escopo).
Reenfileirar o ausente ou o falhado NAO e feito aqui (a ferramenta so le); e ato de operador.

    python -m maezo.platform.rco_avisos_reconciliacao --desde 2026-11-18T00:00:00Z --ate 2026-11-19T00:00:00Z

SAIDA: JSON no stdout; codigo 0 = nenhuma divergencia, 1 = alguma divergencia (inclusive `aviso_falhou`), 2 =
banco ou pin indisponivel.
O indicador `efeito_k1_habilitado` NAO e lido aqui.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform.rco_avisos import ERROS, TABELA, avisos_para, chave_idempotencia
from maezo.platform.rco_manifesto import AvisoK6, ComposicaoK1
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

#: A chave da entrada de `dmn_versions` (= `_audit_ctx.CHAVE_SINAIS_AUDITORIA`, paridade em teste).
CHAVE_SINAIS: Final[str] = "sinaisAuditoria"
#: `harness.AUDIT_DECISION_COMPLETE` (paridade em teste): so conclusoes enfileiram aviso.
DECISAO_CONCLUSAO: Final[str] = "COMPLETE"
#: DL-0187: a chave com o resumo da composicao (= `_audit_ctx.CHAVE_COMPOSICAO_K1`, paridade em teste) e os
#: modos (= `_audit_ctx.MODOS_COMPOSICAO`). So `efeito` espera linha no outbox.
CHAVE_COMPOSICAO: Final[str] = "composicaoK1"
MODO_SOMBRA: Final[str] = "sombra"
MODO_EFEITO: Final[str] = "efeito"
MODOS: Final[frozenset[str]] = frozenset({MODO_SOMBRA, MODO_EFEITO})

AUSENTE_NO_OUTBOX: Final[str] = "ausente_no_outbox"
COMPOSICAO_DESCONHECIDA: Final[str] = "composicao_desconhecida"
SINAL_SEM_AVISO: Final[str] = "sinal_sem_aviso"
CHAVE_AUDITORIA_FORA_DA_FORMA: Final[str] = "chave_auditoria_fora_da_forma"
#: DL-0186: a linha esperada existe mas terminou `falhou` (o aviso nao saiu); leva o `ultimo_erro` fechado.
AVISO_FALHOU: Final[str] = "aviso_falhou"
#: DL-0187: `composicaoK1` presente sem modo do vocabulario: nada esperado, a divergencia aparece.
MODO_FORA_DO_VOCABULARIO: Final[str] = "modo_fora_do_vocabulario"
#: Codigos FECHADOS do relatorio.
CODIGOS: Final[frozenset[str]] = frozenset(
    {
        AUSENTE_NO_OUTBOX,
        COMPOSICAO_DESCONHECIDA,
        SINAL_SEM_AVISO,
        CHAVE_AUDITORIA_FORA_DA_FORMA,
        AVISO_FALHOU,
        MODO_FORA_DO_VOCABULARIO,
    }
)


@dataclass(frozen=True, slots=True)
class RegistroAuditado:
    """Um registro de conclusao da cadeia: a `dedup_key` do harness e o `dmn_versions` gravado."""

    dedup_key: str
    dmn_versions: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AvisoEsperado:
    """Uma linha que o harness teria gravado no outbox para a tarefa."""

    task_id: str
    composicao: str
    sinal: str
    fila: str
    chave_idempotencia: str


@dataclass(frozen=True, slots=True)
class Divergencia:
    """Um item do relatorio: o codigo fechado e so ids (campo ausente = vazio)."""

    codigo: str
    task_id: str = ""
    composicao: str = ""
    decisao: str = ""
    sinal: str = ""
    fila: str = ""
    chave_idempotencia: str = ""
    #: So em `aviso_falhou`: o codigo fechado de `rco_aviso_auditoria.ultimo_erro` (`rco_avisos.ERROS`).
    ultimo_erro: str = ""

    def como_dict(self) -> dict[str, str]:
        return {k: v for k, v in _campos(self).items() if v}


def _campos(d: Divergencia) -> dict[str, str]:
    return {
        "codigo": d.codigo,
        "task_id": d.task_id,
        "composicao": d.composicao,
        "decisao": d.decisao,
        "sinal": d.sinal,
        "fila": d.fila,
        "chave_idempotencia": d.chave_idempotencia,
        "ultimo_erro": d.ultimo_erro,
    }


@dataclass(frozen=True, slots=True)
class Relatorio:
    """O resultado: registros lidos, avisos esperados, linhas por status e divergencias (codigo fechado)."""

    registros: int
    esperados: int
    encontrados_por_status: Mapping[str, int]
    divergencias: tuple[Divergencia, ...] = field(default_factory=tuple)
    #: DL-0187: (tarefa, composicao) auditadas em `modo=sombra` com sinal: nenhuma linha esperada.
    suprimidos_sombra: int = 0

    @property
    def ok(self) -> bool:
        return not self.divergencias

    def como_dict(self) -> dict[str, Any]:
        por_codigo: dict[str, int] = {}
        for d in self.divergencias:
            por_codigo[d.codigo] = por_codigo.get(d.codigo, 0) + 1
        return {
            "registros": self.registros,
            "esperados": self.esperados,
            "encontrados_por_status": dict(sorted(self.encontrados_por_status.items())),
            "divergencias_por_codigo": dict(sorted(por_codigo.items())),
            "divergencias": [d.como_dict() for d in self.divergencias],
            "suprimidos_sombra": self.suprimidos_sombra,
        }


def _task_id(tenant: str, dedup_key: str) -> str | None:
    prefixo = f"{tenant}:"
    if not dedup_key.startswith(prefixo):
        return None
    task_id = dedup_key[len(prefixo) :]
    # `{tenant}:refuse:{task_id}` e outro namespace (recusa), nao conclusao.
    if not task_id or task_id.startswith("refuse:"):
        return None
    return task_id


def _modo(entrada: Mapping[str, Any]) -> str | None:
    """O modo de `composicaoK1` da entrada; `efeito` quando a chave falta (auditoria anterior ao DL-0187, em
    que os dois modos enfileiravam); `None` quando a chave existe sem modo do vocabulario."""
    resumo = entrada.get(CHAVE_COMPOSICAO)
    if resumo is None:
        return MODO_EFEITO
    modo = resumo.get("modo") if isinstance(resumo, Mapping) else None
    return modo if isinstance(modo, str) and modo in MODOS else None


def esperados_da_auditoria(
    registros: Iterable[RegistroAuditado],
    *,
    tenant: str,
    composicoes: Sequence[ComposicaoK1],
    avisos_do_pin: Mapping[str, AvisoK6],
) -> tuple[list[AvisoEsperado], list[Divergencia]]:
    """Funcao pura: as linhas esperadas (sem repeticao) e as divergencias de auditoria (codigo fechado).

    Cada decisao com `sinaisAuditoria` da a composicao pelo manifesto; como o harness grava a UNIAO dos sinais
    em cada decisao do grupo, a mesma (tarefa, composicao) aparece uma vez so no esperado. DL-0187: so
    `modo=efeito` (ou entrada sem `composicaoK1`) espera linha; ver `suprimidos_sombra`.
    """
    esperados, divergencias, _ = _esperados(
        registros, tenant=tenant, composicoes=composicoes, avisos_do_pin=avisos_do_pin
    )
    return esperados, divergencias


def _esperados(
    registros: Iterable[RegistroAuditado],
    *,
    tenant: str,
    composicoes: Sequence[ComposicaoK1],
    avisos_do_pin: Mapping[str, AvisoK6],
) -> tuple[list[AvisoEsperado], list[Divergencia], int]:
    por_decisao = {d: c for c in composicoes for d in c.decisoes}
    esperados: dict[str, AvisoEsperado] = {}
    divergencias: list[Divergencia] = []
    vistos: set[tuple[str, str, str]] = set()
    suprimidos: set[tuple[str, str]] = set()
    fora_do_vocabulario: set[tuple[str, str]] = set()
    for registro in registros:
        task_id = _task_id(tenant, registro.dedup_key)
        if task_id is None:
            divergencias.append(Divergencia(CHAVE_AUDITORIA_FORA_DA_FORMA))
            continue
        for decisao in sorted(registro.dmn_versions):
            entrada = registro.dmn_versions[decisao]
            if not isinstance(entrada, Mapping):
                continue
            sinais = entrada.get(CHAVE_SINAIS)
            if not isinstance(sinais, list) or not sinais:
                continue
            grupo = por_decisao.get(decisao)
            if grupo is None:
                divergencias.append(Divergencia(COMPOSICAO_DESCONHECIDA, task_id=task_id, decisao=decisao))
                continue
            modo = _modo(entrada)
            if modo is None:
                if (task_id, grupo.nome) not in fora_do_vocabulario:
                    fora_do_vocabulario.add((task_id, grupo.nome))
                    divergencias.append(
                        Divergencia(
                            MODO_FORA_DO_VOCABULARIO, task_id=task_id, composicao=grupo.nome, decisao=decisao
                        )
                    )
                continue
            if modo == MODO_SOMBRA:
                suprimidos.add((task_id, grupo.nome))
                continue
            for sinal in sinais:
                if not isinstance(sinal, str) or (task_id, grupo.nome, sinal) in vistos:
                    continue
                vistos.add((task_id, grupo.nome, sinal))
                try:
                    itens = avisos_para([sinal], avisos_do_pin)
                except KeyError:  # `SinalSemAvisoError`: o pin de agora nao endereca este sinal
                    divergencias.append(
                        Divergencia(SINAL_SEM_AVISO, task_id=task_id, composicao=grupo.nome, sinal=sinal)
                    )
                    continue
                for item in itens:
                    chave = chave_idempotencia(tenant, task_id, grupo.nome, item.sinal, item.fila)
                    esperados.setdefault(
                        chave, AvisoEsperado(task_id, grupo.nome, item.sinal, item.fila, chave)
                    )
    return list(esperados.values()), divergencias, len(suprimidos)


def reconciliar(
    registros: Sequence[RegistroAuditado],
    *,
    tenant: str,
    composicoes: Sequence[ComposicaoK1],
    avisos_do_pin: Mapping[str, AvisoK6],
    status_no_outbox: Mapping[str, str],
    erros_no_outbox: Mapping[str, str] | None = None,
) -> Relatorio:
    """Funcao pura: compara o esperado com `status_no_outbox` (chave -> status) e monta o relatorio.

    Linha esperada em `falhou` vira `aviso_falhou` com o `ultimo_erro` de `erros_no_outbox` (chave -> codigo);
    codigo fora de `rco_avisos.ERROS` (ou ausente) vai vazio, nunca o texto lido.
    """
    erros = erros_no_outbox or {}
    esperados, divergencias, suprimidos = _esperados(
        registros, tenant=tenant, composicoes=composicoes, avisos_do_pin=avisos_do_pin
    )
    por_status: dict[str, int] = {}
    for aviso in esperados:
        status = status_no_outbox.get(aviso.chave_idempotencia)
        if status is None:
            divergencias.append(
                Divergencia(
                    AUSENTE_NO_OUTBOX,
                    task_id=aviso.task_id,
                    composicao=aviso.composicao,
                    sinal=aviso.sinal,
                    fila=aviso.fila,
                    chave_idempotencia=aviso.chave_idempotencia,
                )
            )
        else:
            por_status[status] = por_status.get(status, 0) + 1
            if status == "falhou":
                erro = erros.get(aviso.chave_idempotencia, "")
                divergencias.append(
                    Divergencia(
                        AVISO_FALHOU,
                        task_id=aviso.task_id,
                        composicao=aviso.composicao,
                        sinal=aviso.sinal,
                        fila=aviso.fila,
                        chave_idempotencia=aviso.chave_idempotencia,
                        ultimo_erro=erro if erro in ERROS else "",
                    )
                )
    return Relatorio(
        registros=len(registros),
        esperados=len(esperados),
        encontrados_por_status=por_status,
        divergencias=tuple(divergencias),
        suprimidos_sombra=suprimidos,
    )


# ---------------------------------------------------------------------------------------------------
# Banco (so leitura)
# ---------------------------------------------------------------------------------------------------

#: Conclusoes da janela com `sinaisAuditoria` em alguma entrada; a `dedup_key` vem da reivindicacao (0005).
_AUDITORIA_SQL: Final[str] = (
    "SELECT d.dedup_key, c.dmn_versions FROM audit_chain c "
    "JOIN audit_emit_dedup d ON d.tenant = c.tenant_id AND d.record_hash = c.record_hash "
    "WHERE c.tenant_id = $1 AND c.timestamp >= $2 AND c.timestamp < $3 AND c.decision = $4 "
    "AND jsonb_path_exists(c.dmn_versions, '$.*.sinaisAuditoria') "
    "ORDER BY c.timestamp, d.dedup_key"
)
_OUTBOX_SQL: Final[str] = (
    f"SELECT chave_idempotencia, status FROM {TABELA} "  # nosec B608 # fonte: constante do modulo
    "WHERE tenant = $1 AND chave_idempotencia = ANY($2::text[])"
)
#: DL-0186: o codigo fechado das linhas `falhou` (CHECK da 0036/0037).
_ERROS_SQL: Final[str] = (
    f"SELECT chave_idempotencia, ultimo_erro FROM {TABELA} "  # nosec B608 # fonte: constante do modulo
    "WHERE tenant = $1 AND chave_idempotencia = ANY($2::text[]) AND status = 'falhou'"
)
STATEMENT_TIMEOUT: Final[str] = "30s"


class ReconciliacaoIndisponivelError(RuntimeError):
    """O banco nao respondeu. A mensagem leva so o tipo do erro do driver."""


class FonteReconciliacao(Protocol):
    async def ler_auditoria(self, desde: datetime, ate: datetime) -> list[RegistroAuditado]: ...

    async def status_no_outbox(self, chaves: Sequence[str]) -> dict[str, str]: ...

    async def erros_no_outbox(self, chaves: Sequence[str]) -> dict[str, str]: ...


def _jsonb(valor: Any) -> Mapping[str, Any]:
    if isinstance(valor, str):
        valor = json.loads(valor)
    return valor if isinstance(valor, Mapping) else {}


class PostgresFonteReconciliacao:
    """Leitura da cadeia e do outbox no schema do tenant, em transacao SO LEITURA (`readonly=True`)."""

    __slots__ = ("_conectar", "_schema", "_tenant")

    def __init__(self, *, dsn: str, tenant: str, conectar: Any | None = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        if conectar is None:
            import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

            plain = normalize_dsn(dsn)

            async def _padrao() -> Any:
                return await asyncpg.connect(plain, timeout=5.0)

            conectar = _padrao
        self._conectar = conectar

    async def _ler(self, sql: str, *args: Any) -> list[Any]:
        try:
            conn = await self._conectar()
            try:
                await conn.execute(f'SET search_path TO "{self._schema}", public')
                async with conn.transaction(readonly=True):
                    await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    return list(await conn.fetch(sql, *args))
            finally:
                await conn.close()
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            raise ReconciliacaoIndisponivelError(
                f"reconciliacao indisponivel: {type(exc).__name__}"
            ) from None

    async def ler_auditoria(self, desde: datetime, ate: datetime) -> list[RegistroAuditado]:
        rows = await self._ler(_AUDITORIA_SQL, self._tenant, desde, ate, DECISAO_CONCLUSAO)
        return [RegistroAuditado(str(r["dedup_key"]), _jsonb(r["dmn_versions"])) for r in rows]

    async def status_no_outbox(self, chaves: Sequence[str]) -> dict[str, str]:
        if not chaves:
            return {}
        rows = await self._ler(_OUTBOX_SQL, self._tenant, list(chaves))
        return {str(r["chave_idempotencia"]): str(r["status"]) for r in rows}

    async def erros_no_outbox(self, chaves: Sequence[str]) -> dict[str, str]:
        if not chaves:
            return {}
        rows = await self._ler(_ERROS_SQL, self._tenant, list(chaves))
        return {str(r["chave_idempotencia"]): str(r["ultimo_erro"] or "") for r in rows}


async def executar(
    fonte: FonteReconciliacao,
    *,
    tenant: str,
    desde: datetime,
    ate: datetime,
    composicoes: Sequence[ComposicaoK1],
    avisos_do_pin: Mapping[str, AvisoK6],
) -> Relatorio:
    """Le a janela, calcula o esperado, le SO as chaves esperadas no outbox e reconcilia."""
    registros = await fonte.ler_auditoria(desde, ate)
    esperados, _ = esperados_da_auditoria(
        registros, tenant=tenant, composicoes=composicoes, avisos_do_pin=avisos_do_pin
    )
    chaves = [e.chave_idempotencia for e in esperados]
    status = await fonte.status_no_outbox(chaves)
    erros = await fonte.erros_no_outbox(chaves) if "falhou" in status.values() else {}
    return reconciliar(
        registros,
        tenant=tenant,
        composicoes=composicoes,
        avisos_do_pin=avisos_do_pin,
        status_no_outbox=status,
        erros_no_outbox=erros,
    )


class ReconciliacaoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    tenant_id: str = Field(default="amh", alias="TENANT_ID")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")


def _instante(texto: str) -> datetime:
    valor = datetime.fromisoformat(texto)
    if valor.tzinfo is None:
        raise argparse.ArgumentTypeError("instante sem fuso (use, por exemplo, 2026-11-18T00:00:00+00:00)")
    return valor


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m maezo.platform.rco_avisos_reconciliacao",
        description=(
            "So leitura: compara sinaisAuditoria da auditoria duravel com o outbox rco_aviso_auditoria numa "
            "janela [desde, ate) e lista os avisos ausentes (so ids e codigos fechados)."
        ),
    )
    parser.add_argument("--desde", required=True, type=_instante, help="inicio da janela (ISO 8601 com fuso)")
    parser.add_argument("--ate", required=True, type=_instante, help="fim da janela, exclusivo (ISO 8601)")
    return parser


async def main(argv: list[str] | None = None) -> int:
    """Entrada do processo. Codigo de saida: 0 sem divergencia, 1 com divergencia, 2 indisponivel."""
    args = build_arg_parser().parse_args(argv)
    if args.ate <= args.desde:
        print(json.dumps({"erro": "janela_vazia"}))
        return 2
    settings = ReconciliacaoSettings()
    if not settings.database_url:
        print(json.dumps({"erro": "database_url_ausente"}))
        return 2
    try:
        from maezo.tools.workers import rco_pin

        pin = rco_pin.rco_rules_pin()
    except Exception as exc:
        print(json.dumps({"erro": "pin_indisponivel", "tipo": type(exc).__name__}))
        return 2
    fonte = PostgresFonteReconciliacao(dsn=settings.database_url, tenant=settings.tenant_id)
    try:
        relatorio = await executar(
            fonte,
            tenant=settings.tenant_id,
            desde=args.desde,
            ate=args.ate,
            composicoes=pin.composicoes,
            avisos_do_pin=pin.avisos,
        )
    except ReconciliacaoIndisponivelError as exc:
        print(json.dumps({"erro": "banco_indisponivel", "tipo": str(exc).rsplit(" ", 1)[-1]}))
        return 2
    print(json.dumps(relatorio.como_dict(), sort_keys=True))
    return 0 if relatorio.ok else 1


if __name__ == "__main__":  # pragma: no cover - entrada do processo
    sys.exit(asyncio.run(main()))


__all__ = [
    "AUSENTE_NO_OUTBOX",
    "CHAVE_AUDITORIA_FORA_DA_FORMA",
    "CHAVE_COMPOSICAO",
    "CHAVE_SINAIS",
    "CODIGOS",
    "COMPOSICAO_DESCONHECIDA",
    "DECISAO_CONCLUSAO",
    "MODOS",
    "MODO_EFEITO",
    "MODO_FORA_DO_VOCABULARIO",
    "MODO_SOMBRA",
    "SINAL_SEM_AVISO",
    "AvisoEsperado",
    "Divergencia",
    "FonteReconciliacao",
    "PostgresFonteReconciliacao",
    "ReconciliacaoIndisponivelError",
    "ReconciliacaoSettings",
    "RegistroAuditado",
    "Relatorio",
    "build_arg_parser",
    "esperados_da_auditoria",
    "executar",
    "main",
    "reconciliar",
]
