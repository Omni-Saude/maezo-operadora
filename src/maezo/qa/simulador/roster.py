"""Roster de beneficiarios reais para o simulador: lido do S3, nunca do lago (ADR-0037).

O caminho em codigo NAO consulta o lago. Quem monta o roster e' uma pessoa com acesso ao Athena,
rodando `roster.sql` (workgroup `amh-austa_operadora-analytics-dev`) e copiando o CSV do resultado,
de bucket para bucket, para `s3://<bucket do simulador>/roster/` (comando no runbook). O simulador
so' le' esse objeto (`cpf,nascimento,nome`), descarta linha invalida e roda o PREFLIGHT: cada pessoa
e' resolvida na AMH pelo mesmo contrato do fluxo de acesso (`resolve-by-document`); quem nao resolve
sai do roster desta execucao.

O roster fica na MEMORIA do processo. O que vai para o manifesto e' so' o rotulo (`s007`), a contagem
e a chave do objeto lido.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import date, datetime

import structlog

from maezo.platform.webhooks.whatsapp.acesso import cpf_valido

from .registro import Sujeito

logger = structlog.get_logger(__name__)

#: O CPF do dono (decisao do dono): esta' sempre no roster e e' o UNICO que roda o fluxo completo
#: de consentimento no modo `somente-leitura`.
CPF_DO_DONO = "42853296890"

_COLUNAS = ("cpf", "nascimento", "nome")


class RosterInvalidoError(ValueError):
    """O objeto do roster nao tem a forma `cpf,nascimento,nome`. Nunca ecoa conteudo."""


def _data(valor: str) -> date | None:
    bruto = valor.strip()[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(bruto, formato).date()
        except ValueError:
            continue
    return None


def ler_roster_csv(texto: str) -> list[Sujeito]:
    """Interpreta o CSV do Athena. Linhas invalidas sao CONTADAS e descartadas, nunca ecoadas."""
    leitor = csv.DictReader(io.StringIO(texto))
    cabecalho = {c.strip().lower() for c in (leitor.fieldnames or [])}
    if not set(_COLUNAS) <= cabecalho:
        raise RosterInvalidoError("roster: o cabecalho tem de conter cpf, nascimento e nome")
    vistos: set[str] = set()
    sujeitos: list[Sujeito] = []
    descartadas = 0
    for linha in leitor:
        normal = {k.strip().lower(): (v or "") for k, v in linha.items() if k is not None}
        cpf = re.sub(r"\D", "", normal.get("cpf", "")).zfill(11)
        nascimento = _data(normal.get("nascimento", ""))
        nome = " ".join(normal.get("nome", "").split())
        if not cpf_valido(cpf) or nascimento is None or not nome or cpf in vistos:
            descartadas += 1
            continue
        vistos.add(cpf)
        sujeitos.append(
            Sujeito(rotulo="", cpf=cpf, nascimento=nascimento, nome=nome, dono=cpf == CPF_DO_DONO)
        )
    # Ordem estavel e nao reveladora: o dono primeiro, os demais por hash do CPF (nunca pelo CPF).
    sujeitos.sort(key=lambda s: (not s.dono, hashlib.sha256(s.cpf.encode()).hexdigest()))
    rotulados = [replace(s, rotulo=f"s{i:03d}") for i, s in enumerate(sujeitos)]
    logger.info("simulador_roster_lido", validos=len(rotulados), descartadas=descartadas)
    return rotulados


ResolverSujeito = Callable[[Sujeito], Awaitable[str | None]]


async def preflight(
    sujeitos: Sequence[Sujeito],
    resolver: ResolverSujeito,
    *,
    limite: int | None = None,
    intervalo_s: float = 1.1,
) -> tuple[list[Sujeito], int]:
    """Resolve cada pessoa na AMH; quem nao resolve (ou da' erro) sai. Para ao juntar `limite`
    resolvidos (o dono, primeiro do roster, sempre e' tentado). Ritmo de `intervalo_s` entre
    chamadas: o contrato tem teto de 60/min por cliente. Devolve (resolvidos, descartados)."""
    resolvidos: list[Sujeito] = []
    descartados = 0
    for i, sujeito in enumerate(sujeitos):
        if limite is not None and len(resolvidos) >= limite:
            break
        if i:
            await asyncio.sleep(intervalo_s)
        try:
            ref = await resolver(sujeito)
        except Exception as exc:  # um erro de uma pessoa nao derruba o preflight inteiro
            logger.warning("simulador_preflight_erro", rotulo=sujeito.rotulo, erro=type(exc).__name__)
            ref = None
        if ref:
            resolvidos.append(replace(sujeito, ref=ref))
        else:
            descartados += 1
    logger.info("simulador_preflight", resolvidos=len(resolvidos), descartados=descartados)
    return resolvidos, descartados


__all__ = ["CPF_DO_DONO", "ResolverSujeito", "RosterInvalidoError", "ler_roster_csv", "preflight"]
