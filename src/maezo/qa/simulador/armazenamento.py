"""Onde o simulador le' o roster e grava a execucao: o bucket proprio no S3 (nada no disco local).

`s3://<bucket>/roster/...`        o CSV que a pessoa copiou do resultado do Athena (7 dias)
`s3://<bucket>/runs/<run_id>/...` manifest.json, transcripts.jsonl, checks.jsonl, judge.jsonl,
                                  summary.json, report.md (30 dias)

O bucket cifra com a CMK propria por padrao (Terraform `qa-simulador.tf`); o cliente nao escolhe
chave. O cliente S3 e' o `boto3` da imagem (vem com o extra `anthropic[bedrock]`), importado so'
aqui e so' quando o armazenamento S3 e' de fato construido.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

PREFIXO_ROSTER = "roster/"
PREFIXO_RUNS = "runs/"


class Armazenamento(Protocol):
    async def gravar(self, chave: str, conteudo: str, *, tipo: str) -> None: ...

    async def ler(self, chave: str) -> str: ...

    async def listar(self, prefixo: str) -> list[tuple[str, float]]: ...


def jsonl(linhas: Iterable[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(linha, ensure_ascii=False, default=str) + "\n" for linha in linhas)


def chave_da_execucao(run_id: str, nome: str) -> str:
    if not run_id or "/" in run_id or ".." in run_id:
        raise ValueError("armazenamento: run_id invalido")
    return f"{PREFIXO_RUNS}{run_id}/{nome}"


class ArmazenamentoS3:
    """S3 com o cliente sincrono do boto3 em thread (o volume e' pequeno: dezenas de objetos)."""

    def __init__(self, bucket: str, *, regiao: str, cliente: Any = None) -> None:
        if not bucket:
            raise ValueError("armazenamento: bucket ausente (MAEZO_SIMULADOR_BUCKET)")
        self._bucket = bucket
        if cliente is None:
            import boto3  # type: ignore[import-untyped]  # extra [bedrock] da imagem

            cliente = boto3.client("s3", region_name=regiao)
        self._cliente = cliente

    @property
    def bucket(self) -> str:
        return self._bucket

    async def gravar(self, chave: str, conteudo: str, *, tipo: str) -> None:
        await asyncio.to_thread(
            self._cliente.put_object,
            Bucket=self._bucket,
            Key=chave,
            Body=conteudo.encode("utf-8"),
            ContentType=tipo,
        )

    async def ler(self, chave: str) -> str:
        resposta = await asyncio.to_thread(self._cliente.get_object, Bucket=self._bucket, Key=chave)
        corpo: bytes = await asyncio.to_thread(resposta["Body"].read)
        return corpo.decode("utf-8")

    async def listar(self, prefixo: str) -> list[tuple[str, float]]:
        """(chave, epoch da ultima modificacao) de todos os objetos sob o prefixo."""
        itens: list[tuple[str, float]] = []
        token: str | None = None
        while True:
            argumentos: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefixo}
            if token:
                argumentos["ContinuationToken"] = token
            pagina = await asyncio.to_thread(self._cliente.list_objects_v2, **argumentos)
            for objeto in pagina.get("Contents", []):
                itens.append((str(objeto["Key"]), objeto["LastModified"].timestamp()))
            if not pagina.get("IsTruncated"):
                return itens
            token = pagina.get("NextContinuationToken")


class ArmazenamentoEmMemoria:
    """Para teste: o mesmo contrato, num dicionario."""

    def __init__(self) -> None:
        self.objetos: dict[str, str] = {}
        self._relogio = 0.0

    async def gravar(self, chave: str, conteudo: str, *, tipo: str) -> None:
        self.objetos[chave] = conteudo

    async def ler(self, chave: str) -> str:
        return self.objetos[chave]

    async def listar(self, prefixo: str) -> list[tuple[str, float]]:
        self._relogio += 1
        return [(k, float(i)) for i, k in enumerate(sorted(self.objetos)) if k.startswith(prefixo)]


async def roster_mais_recente(armazenamento: Armazenamento) -> str:
    """A chave do CSV mais recente sob `roster/`. Sem roster, erro claro (o runbook diz como gerar)."""
    candidatos = [(k, t) for k, t in await armazenamento.listar(PREFIXO_ROSTER) if k.endswith(".csv")]
    if not candidatos:
        raise FileNotFoundError("simulador: nenhum roster em roster/ — gere com roster.sql (runbook)")
    return max(candidatos, key=lambda par: par[1])[0]


__all__ = [
    "PREFIXO_ROSTER",
    "PREFIXO_RUNS",
    "Armazenamento",
    "ArmazenamentoEmMemoria",
    "ArmazenamentoS3",
    "chave_da_execucao",
    "jsonl",
    "roster_mais_recente",
]
