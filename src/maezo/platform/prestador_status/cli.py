"""CLI do ledger de status do prestador: `python -m maezo.platform.prestador_status <subcomando>`.

Subcomandos:

* `digest-baseline --arquivo F` — imprime o sha256 do conteudo canonico (o valor que o
  signatario da AMH confere e copia em `atestacao.conteudo_sha256`). So' le o arquivo.
* `validar-baseline --arquivo F` — valida forma + atestacao, sem banco. Nao grava.
* `importar-baseline --arquivo F` — grava a baseline atestada no ledger. **Exige a bandeira.**
* `reconciliar [--cred-chave BK]... [--fraude-chave BK]... [--prestador P]... [--varrer]` —
  projeta terminais CRED/FRAUDE no ledger (evento = chaves/prestadores; noturno = `--varrer`).
  **Exige a bandeira.**

Com `MAEZO_PRESTADOR_STATUS_LEDGER` desligada (padrao), `importar-baseline` e `reconciliar`
recusam ANTES de abrir banco ou motor (codigo 2). Tenant, motor e banco vem de
`WorkerRuntimeSettings` (`TENANT_ID`, `CIBSEVEN_BASE_URL`, `DATABASE_URL`); o token do motor vem
do cofre (`build_worker_credential_view`, AF-14). O transporte e' o seam GATED de
`tool_registry.build_cibseven_seam` — esta raiz nao constroi transporte cru.

Codigos de saida: 0 ok; 2 recusa (bandeira, baseline recusada, configuracao); 3 alguma leitura
`INDISPONIVEL` (sinal fail-closed: nada foi gravado para aquele seletor).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from maezo.platform.prestador_status.baseline import (
    BaselineRecusadaError,
    digest_conteudo,
    importar_baseline,
    validar_baseline,
)
from maezo.platform.prestador_status.ledger import (
    LedgerConflitoError,
    LedgerIndisponivelError,
    LedgerStatusPrestador,
    PostgresLedgerStatusPrestador,
)
from maezo.platform.prestador_status.reconciliador import (
    HistoricInstanceResolvingTransport,
    ReconciliadorStatusPrestador,
    Resultado,
    ResultadoReconciliacao,
)

SAIDA_OK: Final = 0
SAIDA_RECUSA: Final = 2
SAIDA_INDISPONIVEL: Final = 3

FabricaLedger = Callable[[Any], LedgerStatusPrestador]
# A Protocol da costura do #1167 chega RE-EXPORTADA pelo reconciliador: um unico modulo deste
# pacote importa `maezo.tools.mcp_cibseven.transport` (fan-in do chokepoint, SC-06 / values.yaml).
FabricaTransporte = Callable[[Any], HistoricInstanceResolvingTransport]


class BandeiraDesligadaError(RuntimeError):
    """`MAEZO_PRESTADOR_STATUS_LEDGER` desligada: nada que grava pode rodar."""


def exige_bandeira(settings: Any) -> None:
    """A UNICA porta para os subcomandos que gravam. Chamada antes de qualquer fabrica."""
    if getattr(settings, "prestador_status_ledger", False) is not True:
        raise BandeiraDesligadaError(
            "MAEZO_PRESTADOR_STATUS_LEDGER desligada: ledger de status do prestador e' DARK — nada gravado"
        )


def ledger_padrao(settings: Any) -> LedgerStatusPrestador:
    if not settings.database_url:
        raise BandeiraDesligadaError("DATABASE_URL ausente: sem banco nao ha ledger")
    return PostgresLedgerStatusPrestador(dsn=settings.database_url, tenant=settings.tenant_id)


def transporte_padrao(settings: Any) -> HistoricInstanceResolvingTransport:
    """O seam GATED do motor, construido SO' pelo registry (cerca §8.1), com o token do cofre."""
    from maezo.gateway.tool_registry import (
        agent_credential,
        build_cibseven_seam,
        build_worker_credential_view,
        build_worker_seam_context,
        effect_seams_gated,
    )

    seam = build_worker_seam_context(tenant=settings.tenant_id)
    transport = build_cibseven_seam(
        seam=seam,
        base_url=settings.cibseven_base_url,
        auth_token=agent_credential(build_worker_credential_view(settings=settings), "cibseven_auth_token"),
        timeout=settings.client_timeout_s,
    )
    gated, detail = effect_seams_gated({"cibseven": transport})
    if not gated:
        raise RuntimeError(f"prestador_status: seam do motor nao gated — recusando: {detail}")
    if not isinstance(transport, HistoricInstanceResolvingTransport):
        raise RuntimeError("prestador_status: seam do motor sem a leitura de historico do PR #1167")
    return transport


async def _fechar(transport: Any) -> None:
    inner = getattr(transport, "inner", None)
    fechar = getattr(inner, "close", None)
    if fechar is not None:
        await fechar()


def _ler_json(caminho: str) -> Any:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _resultado_json(r: ResultadoReconciliacao) -> dict[str, Any]:
    return {
        "origem": r.origem.value,
        "seletor": r.seletor,
        "resultado": r.resultado.value,
        "motivo": r.motivo,
        "valor": r.entrada.valor if r.entrada is not None else None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m maezo.platform.prestador_status")
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome in ("digest-baseline", "validar-baseline", "importar-baseline"):
        p = sub.add_parser(nome)
        p.add_argument("--arquivo", required=True)
    rec = sub.add_parser("reconciliar")
    rec.add_argument("--cred-chave", action="append", default=[])
    rec.add_argument("--fraude-chave", action="append", default=[])
    rec.add_argument("--prestador", action="append", default=[])
    rec.add_argument("--varrer", action="store_true")
    return parser


async def _reconciliar(
    args: argparse.Namespace, settings: Any, ledger: LedgerStatusPrestador, transport: Any
) -> int:
    reconciliador = ReconciliadorStatusPrestador(
        transport=transport, ledger=ledger, tenant=settings.tenant_id
    )
    resultados: list[ResultadoReconciliacao] = []
    for chave in args.cred_chave:
        resultados.append(await reconciliador.reconciliar_cred_por_chave(chave))
    for chave in args.fraude_chave:
        resultados.append(await reconciliador.reconciliar_fraude_por_chave(chave))
    for prestador in args.prestador:
        resultados.extend(await reconciliador.reconciliar_prestador(prestador))
    if args.varrer:
        resultados.extend(await reconciliador.varrer())
    print(json.dumps([_resultado_json(r) for r in resultados], ensure_ascii=False))
    if any(r.resultado is Resultado.INDISPONIVEL for r in resultados):
        return SAIDA_INDISPONIVEL
    return SAIDA_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Any = None,
    fabrica_ledger: FabricaLedger | None = None,
    fabrica_transporte: FabricaTransporte | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if settings is None:
        from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

        settings = WorkerRuntimeSettings()
    tenant = settings.tenant_id

    if args.comando == "digest-baseline":
        bruta = _ler_json(args.arquivo)
        if not isinstance(bruta, dict):
            print("baseline: objeto JSON exigido", file=sys.stderr)
            return SAIDA_RECUSA
        print(
            digest_conteudo(
                str(bruta.get("tenant")), str(bruta.get("data_referencia")), bruta.get("prestadores") or []
            )
        )
        return SAIDA_OK

    if args.comando == "validar-baseline":
        try:
            baseline = validar_baseline(_ler_json(args.arquivo), tenant=tenant)
        except BaselineRecusadaError as exc:
            print(f"baseline RECUSADA: {exc}", file=sys.stderr)
            return SAIDA_RECUSA
        print(json.dumps({"conteudo_sha256": baseline.conteudo_sha256, "prestadores": len(baseline.linhas)}))
        return SAIDA_OK

    # Daqui para baixo tudo GRAVA: a bandeira e' conferida antes de qualquer fabrica.
    try:
        exige_bandeira(settings)
    except BandeiraDesligadaError as exc:
        print(str(exc), file=sys.stderr)
        return SAIDA_RECUSA

    if args.comando == "importar-baseline":
        try:
            baseline = validar_baseline(_ler_json(args.arquivo), tenant=tenant)
        except BaselineRecusadaError as exc:
            print(f"baseline RECUSADA: {exc}", file=sys.stderr)
            return SAIDA_RECUSA
        try:
            ledger = (fabrica_ledger or ledger_padrao)(settings)
            relatorio = asyncio.run(importar_baseline(baseline, ledger))
        except BandeiraDesligadaError as exc:
            print(str(exc), file=sys.stderr)
            return SAIDA_RECUSA
        except (LedgerIndisponivelError, LedgerConflitoError) as exc:
            print(f"ledger INDISPONIVEL: {type(exc).__name__}", file=sys.stderr)
            return SAIDA_INDISPONIVEL
        print(json.dumps({"conteudo_sha256": relatorio.conteudo_sha256, "contagem": relatorio.contagem()}))
        return SAIDA_OK

    # reconciliar
    try:
        ledger = (fabrica_ledger or ledger_padrao)(settings)
    except BandeiraDesligadaError as exc:
        print(str(exc), file=sys.stderr)
        return SAIDA_RECUSA
    transport = (fabrica_transporte or transporte_padrao)(settings)

    async def _rodar() -> int:
        try:
            return await _reconciliar(args, settings, ledger, transport)
        finally:
            await _fechar(transport)

    return asyncio.run(_rodar())


__all__ = [
    "SAIDA_INDISPONIVEL",
    "SAIDA_OK",
    "SAIDA_RECUSA",
    "BandeiraDesligadaError",
    "exige_bandeira",
    "ledger_padrao",
    "main",
    "transporte_padrao",
]
