"""O pacote de comportamento de um agente, fora da imagem (ADR-0067, fase 3).

O QUE E'. Um artefato imutavel por versao — um JSON canonico com os arquivos de `spec/agents/<id>/pacote/`
— identificado pelo sha256 dos proprios bytes. Cada ambiente tem um PONTEIRO (`ativo.json`) que diz qual
sha256 vale ali. Trocar o comportamento do agente e' publicar um pacote novo e mover o ponteiro: sem build
e sem deploy do Maezo. Voltar e' mover o ponteiro de volta. (Ratificacao Q-6, PLANS §0.8: excecao ao
`MAEZO_SPEC_DIR` "somente por bundle imutavel com digest permitido".)

ONDE MORA. `s3://<bucket>/<prefixo>/<agente>/pacotes/<sha256>.json` e
`s3://<bucket>/<prefixo>/<agente>/<ambiente>/ativo.json`, com `MAEZO_PACOTES_URI=s3://<bucket>/<prefixo>` e
`MAEZO_PACOTES_AMBIENTE=<ambiente>` na task. Quem escreve e' so' a esteira (IAM); quem le' confere o hash.

COMO E' LIDO. `CarregadorDePacote.atual()` devolve o pacote em cache e, passado o `ttl_s`, o relê — o turno
seguinte ja' usa a versao nova. Falha (rede, hash, validacao do agente) NUNCA derruba o turno: fica o
ultimo pacote bom e a falha vai ao log. Sem `MAEZO_PACOTES_URI`, nao ha' carregador e o agente usa o que a
plataforma passa (o comportamento de antes, e o de todo teste que nao liga o pacote).

Faz parte do SDK do runtime (`maezo.contratos.sdk`): e' por aqui que um agente le' o proprio pacote.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import structlog

from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

FORMATO: Final[str] = "maezo-pacote-v1"
ENV_URI: Final[str] = "MAEZO_PACOTES_URI"
ENV_AMBIENTE: Final[str] = "MAEZO_PACOTES_AMBIENTE"


class PacoteInvalidoError(ValueError):
    """O pacote nao serve: hash, formato, agente ou conteudo. A mensagem nunca traz o conteudo."""


@dataclass(frozen=True, slots=True)
class PacoteDoAgente:
    agente: str
    versao: str
    sha256: str
    arquivos: Mapping[str, str] = field(repr=False)


def canonico(documento: Mapping[str, Any]) -> bytes:
    """Os bytes canonicos de um documento de pacote: o sha256 e' sempre deles."""
    return json.dumps(documento, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def montar(agente: str, versao: str, diretorio: Path) -> tuple[bytes, PacoteDoAgente]:
    """Monta o pacote de `diretorio` (todos os arquivos, texto UTF-8, caminhos relativos posix)."""
    if not diretorio.is_dir():
        raise PacoteInvalidoError(f"{diretorio}: diretorio do pacote nao existe")
    arquivos = {
        p.relative_to(diretorio).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(diretorio.rglob("*"))
        if p.is_file()
    }
    if not arquivos:
        raise PacoteInvalidoError(f"{diretorio}: pacote vazio")
    conteudo = canonico({"formato": FORMATO, "agente": agente, "versao": versao, "arquivos": arquivos})
    sha = hashlib.sha256(conteudo).hexdigest()
    return conteudo, PacoteDoAgente(agente=agente, versao=versao, sha256=sha, arquivos=arquivos)


def ler(conteudo: bytes, *, sha256_esperado: str, agente: str) -> PacoteDoAgente:
    """Le' e confere um pacote: o sha256 dos bytes tem de ser o do ponteiro, e o agente o esperado."""
    sha = hashlib.sha256(conteudo).hexdigest()
    if sha != sha256_esperado:
        raise PacoteInvalidoError(f"pacote de {agente}: sha256 {sha} nao e' o do ponteiro {sha256_esperado}")
    try:
        documento = json.loads(conteudo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PacoteInvalidoError(f"pacote de {agente}: nao e' JSON ({type(exc).__name__})") from None
    if not isinstance(documento, dict) or documento.get("formato") != FORMATO:
        raise PacoteInvalidoError(f"pacote de {agente}: formato desconhecido")
    if documento.get("agente") != agente:
        raise PacoteInvalidoError(f"pacote de {agente}: e' de outro agente ({documento.get('agente')!r})")
    arquivos = documento.get("arquivos")
    if not isinstance(arquivos, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in arquivos.items()
    ):
        raise PacoteInvalidoError(f"pacote de {agente}: arquivos fora da forma")
    return PacoteDoAgente(
        agente=agente, versao=str(documento.get("versao", "")), sha256=sha, arquivos=arquivos
    )


def _separar_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"{ENV_URI} tem de ser s3://bucket/prefixo")
    bucket, _, prefixo = uri[len("s3://") :].partition("/")
    if not bucket:
        raise ValueError(f"{ENV_URI} sem bucket")
    return bucket, prefixo.strip("/")


def _leitor_s3() -> Callable[[str, str], bytes]:
    cliente: list[Any] = []

    def ler_objeto(bucket: str, chave: str) -> bytes:
        if not cliente:
            import boto3  # type: ignore[import-untyped]  # import tardio: so' quem liga o pacote paga

            cliente.append(boto3.client("s3"))
        resposta = cliente[0].get_object(Bucket=bucket, Key=chave)
        return bytes(resposta["Body"].read())

    return ler_objeto


class CarregadorDePacote:
    """O pacote ATIVO de um agente num ambiente, com cache por `ttl_s` e o ultimo bom em caso de falha."""

    def __init__(
        self,
        agente: str,
        *,
        uri: str,
        ambiente: str,
        ttl_s: float = 60.0,
        validar: Callable[[PacoteDoAgente], object] | None = None,
        ler_objeto: Callable[[str, str], bytes] | None = None,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.agente = agente
        self._bucket, prefixo = _separar_uri(uri)
        base = f"{prefixo}/{agente}" if prefixo else agente
        self._chave_ponteiro = f"{base}/{ambiente}/ativo.json"
        self._base_pacotes = f"{base}/pacotes"
        self._ttl_s = ttl_s
        self._validar = validar
        self._ler_objeto = ler_objeto or _leitor_s3()
        self._relogio = relogio
        self._pacote: PacoteDoAgente | None = None
        self._lido_em: float | None = None
        self._trava = threading.Lock()

    def atual(self) -> PacoteDoAgente | None:
        """O pacote ativo. Relê o ponteiro passado o TTL; falha = o ultimo bom (ou `None` se nunca houve)."""
        agora = self._relogio()
        if self._lido_em is not None and agora - self._lido_em < self._ttl_s:
            return self._pacote
        with self._trava:
            if self._lido_em is not None and self._relogio() - self._lido_em < self._ttl_s:
                return self._pacote
            try:
                self._pacote = self._recarregar()
            except PROGRAMMING_ERRORS:
                raise
            except Exception as exc:  # rede (ClientError do S3), hash, validacao: fica o ultimo bom
                logger.error(
                    "pacote_do_agente_nao_carregado",
                    agente=self.agente,
                    error_type=type(exc).__name__,
                    detalhe=str(exc)[:200],
                    mantido=self._pacote.versao if self._pacote else None,
                )
            self._lido_em = self._relogio()
            return self._pacote

    def _recarregar(self) -> PacoteDoAgente:
        ponteiro = json.loads(self._ler_objeto(self._bucket, self._chave_ponteiro).decode("utf-8"))
        sha = ponteiro.get("sha256") if isinstance(ponteiro, dict) else None
        if not isinstance(sha, str) or len(sha) != 64:
            raise PacoteInvalidoError(f"ponteiro de {self.agente} sem sha256 valido")
        if self._pacote is not None and self._pacote.sha256 == sha:
            return self._pacote
        conteudo = self._ler_objeto(self._bucket, f"{self._base_pacotes}/{sha}.json")
        pacote = ler(conteudo, sha256_esperado=sha, agente=self.agente)
        if self._validar is not None:
            self._validar(pacote)  # o agente recusa o que nao entende: fica o ultimo bom
        logger.info("pacote_do_agente_carregado", agente=self.agente, versao=pacote.versao, sha256=sha)
        return pacote


def carregador_do_ambiente(
    agente: str, *, validar: Callable[[PacoteDoAgente], object] | None = None
) -> CarregadorDePacote | None:
    """O carregador configurado pela task (`MAEZO_PACOTES_URI` + `MAEZO_PACOTES_AMBIENTE`), ou `None`.

    URI sem ambiente e' erro de configuracao (levanta): um pacote sem ambiente nao sabe qual ponteiro ler."""
    uri = os.environ.get(ENV_URI, "").strip()
    if not uri:
        return None
    ambiente = os.environ.get(ENV_AMBIENTE, "").strip()
    if not ambiente:
        raise ValueError(f"{ENV_URI} definido sem {ENV_AMBIENTE}")
    return CarregadorDePacote(agente, uri=uri, ambiente=ambiente, validar=validar)
