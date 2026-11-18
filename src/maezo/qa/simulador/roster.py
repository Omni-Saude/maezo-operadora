"""Roster de beneficiarios reais para o simulador: lido do S3, nunca do lago (ADR-0037).

O caminho em codigo NAO consulta o lago. Quem monta o roster e' uma pessoa com acesso ao Athena,
rodando `roster.sql` (workgroup `amh-austa_operadora-analytics-dev`) e copiando o CSV do resultado,
de bucket para bucket, para `s3://<bucket do simulador>/roster/` (comando no runbook). O simulador
so' le' esse objeto (`cpf,nascimento,nome`), descarta linha invalida e roda o PREFLIGHT: cada pessoa
e' resolvida na AMH pelo mesmo contrato do fluxo de acesso (`resolve-by-document`); quem nao resolve
sai do roster desta execucao.

O roster fica na MEMORIA do processo. O que vai para o manifesto e' so' o rotulo (`s007`), a contagem
e a chave do objeto lido.

O documento da primeira linha do roster NAO esta' no codigo: e' parametro
OBRIGATORIO da execucao, na variavel `MAEZO_SIMULADOR_DOCUMENTO_PRIMEIRA_LINHA`. Sem ela, ou com um
valor que nao e' CPF valido, o simulador nao roda; a mensagem nunca repete o valor. Os SQLs recebem o
mesmo valor como parametro de execucao do Athena (`?`), montado por `python -m maezo.qa.simulador.roster`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import hashlib
import io
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import structlog

from .registro import Sujeito

logger = structlog.get_logger(__name__)

#: A variavel do documento da primeira linha do roster: essa pessoa esta' sempre no roster e
#: e' a UNICA que roda o fluxo completo de consentimento no modo `somente-leitura`. Parametro OBRIGATORIO,
#: sem valor padrao; nunca vai a argumento (`SIMULADOR_ARGS` vai para o manifesto), a log nem a excecao.
VARIAVEL_DO_DOCUMENTO = "MAEZO_SIMULADOR_DOCUMENTO_PRIMEIRA_LINHA"

_COLUNAS = ("cpf", "nascimento", "nome")
_SO_ONZE_DIGITOS = re.compile(r"[0-9]{11}")
_COMENTARIO_SQL = re.compile(r"--[^\n]*")


class RosterInvalidoError(ValueError):
    """O objeto do roster nao tem a forma `cpf,nascimento,nome`. Nunca ecoa conteudo."""


class DocumentoDaPrimeiraLinhaError(RuntimeError):
    """O documento da primeira linha do roster faltou ou nao e' CPF valido.

    A mensagem NUNCA contem o valor informado."""


def validar_documento(valor: str) -> str:
    """Os 11 digitos do documento, ou `DocumentoDaPrimeiraLinhaError`. So' digitos ASCII (sem pontuacao,
    sem espaco interno) e o digito verificador do CPF: nada alem disso chega a uma consulta."""
    from maezo.platform.webhooks.whatsapp.acesso import cpf_valido

    bruto = (valor or "").strip()
    if not _SO_ONZE_DIGITOS.fullmatch(bruto) or not cpf_valido(bruto):
        raise DocumentoDaPrimeiraLinhaError(
            f"simulador: o valor de {VARIAVEL_DO_DOCUMENTO} nao e' um CPF valido (11 digitos, so' numeros, "
            "com o digito verificador). O valor nao e' exibido; confira e rode de novo."
        )
    return bruto


def documento_da_primeira_linha(ambiente: Mapping[str, str] | None = None) -> str:
    """O documento da primeira linha do roster, lido de `VARIAVEL_DO_DOCUMENTO` e validado.

    Falta = erro, nunca um padrao."""
    valor = (os.environ if ambiente is None else ambiente).get(VARIAVEL_DO_DOCUMENTO, "")
    if not valor.strip():
        raise DocumentoDaPrimeiraLinhaError(
            f"simulador: falta o parametro obrigatorio {VARIAVEL_DO_DOCUMENTO} (o CPF, 11 digitos, da pessoa "
            "da primeira linha do roster). Sem ele o simulador nao roda; veja "
            "docs/runbooks/simulador-beneficiario.md."
        )
    return validar_documento(valor)


def consultas_do_sql(sql: str) -> list[str]:
    """As consultas de um arquivo `.sql`, sem comentarios, na ordem (separadas por `;`)."""
    sem_comentario = _COMENTARIO_SQL.sub("", sql)
    return [c.strip() for c in sem_comentario.split(";") if c.strip()]


def montar_consulta(sql: str, documento: str, *, numero: int = 1) -> tuple[str, list[str]]:
    """A consulta `numero` (1, 2...) do arquivo e os `ExecutionParameters` do Athena: um `'<documento>'`
    por `?`, na ordem. O valor so' entra como parametro de execucao, nunca no texto da consulta, e so'
    depois de `validar_documento` (11 digitos ASCII): aspas, espaco ou SQL nao passam."""
    doc = validar_documento(documento)
    consultas = consultas_do_sql(sql)
    if not 1 <= numero <= len(consultas):
        raise ValueError(f"roster: o arquivo tem {len(consultas)} consulta(s); pedida a {numero}")
    consulta = consultas[numero - 1]
    marcadores = consulta.count("?")
    if marcadores == 0:
        raise ValueError("roster: a consulta nao tem o parametro `?` do documento da primeira linha")
    return consulta, [f"'{doc}'"] * marcadores


def _data(valor: str) -> date | None:
    bruto = valor.strip()[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(bruto, formato).date()
        except ValueError:
            continue
    return None


def ler_roster_csv(texto: str, *, documento_primeira_linha: str) -> list[Sujeito]:
    """Interpreta o CSV do Athena. Linhas invalidas sao CONTADAS e descartadas, nunca ecoadas.
    `documento_primeira_linha` (obrigatorio, validado) marca a pessoa que vai para a primeira linha."""
    from maezo.platform.webhooks.whatsapp.acesso import cpf_valido

    documento = validar_documento(documento_primeira_linha)
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
        sujeitos.append(Sujeito(rotulo="", cpf=cpf, nascimento=nascimento, nome=nome, dono=cpf == documento))
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


def _principal(argv: Sequence[str] | None = None) -> int:
    """`python -m maezo.qa.simulador.roster {consulta|parametros} ARQUIVO.sql [--numero N]`.

    `consulta` imprime o texto da consulta (com `?`, sem o valor) para `--query-string`; `parametros`
    imprime os `ExecutionParameters` para `--execution-parameters`. Ambos exigem
    `MAEZO_SIMULADOR_DOCUMENTO_PRIMEIRA_LINHA` valida; o erro vai ao stderr sem o valor (codigo 2)."""
    p = argparse.ArgumentParser(prog="python -m maezo.qa.simulador.roster")
    p.add_argument("saida", choices=("consulta", "parametros"))
    p.add_argument("arquivo")
    p.add_argument("--numero", type=int, default=1)
    args = p.parse_args(argv)
    # A saida vai para `$(...)` no comando do Athena: qualquer log do processo (os imports da plataforma
    # registram metricas em stdout) e' desviado para o stderr; no stdout so' a consulta ou os parametros.
    saida = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            documento = documento_da_primeira_linha()
            consulta, parametros = montar_consulta(
                Path(args.arquivo).read_text(encoding="utf-8"), documento, numero=args.numero
            )
    except (DocumentoDaPrimeiraLinhaError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(consulta if args.saida == "consulta" else " ".join(parametros), file=saida)
    return 0


__all__ = [
    "VARIAVEL_DO_DOCUMENTO",
    "DocumentoDaPrimeiraLinhaError",
    "ResolverSujeito",
    "RosterInvalidoError",
    "consultas_do_sql",
    "documento_da_primeira_linha",
    "ler_roster_csv",
    "montar_consulta",
    "preflight",
    "validar_documento",
]


if __name__ == "__main__":
    sys.exit(_principal())
