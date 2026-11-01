"""Importacao UNICA da baseline da rede atestada pela AMH (MPTT P3, analise §2 item (i)).

A baseline e' a lista da rede existente — status, data de efeito e atributos cadastrais —
produzida e ASSINADA pela equipe de rede da AMH (analise §8 linha P3; decisao do dono D-T1(A)).
Sem ela, so' quem passou pelo CRED do Maezo tem status; todo o resto fica `DESCONHECIDO`.

**O que esta ferramenta exige (e por que recusa).** Um arquivo JSON no formato
`maezo-prestador-baseline/v1`:

    {
      "formato": "maezo-prestador-baseline/v1",
      "tenant": "<tenant>",
      "data_referencia": "YYYY-MM-DD",
      "prestadores": [
        {"prestador_id": "...", "situacao": "CREDENCIADO" | "DESCREDENCIADO",
         "data_efeito": "YYYY-MM-DD" | null,
         "tipo_prestador": "..." | null, "especialidade": "..." | null, "regiao_saude": "..." | null}
      ],
      "atestacao": {
        "atestado": true,
        "emissor": "<equipe da AMH que atesta>",
        "responsavel": "<quem assinou>",
        "data_assinatura": "YYYY-MM-DD",
        "evidencia_ref": "<referencia ao documento assinado, guardado pela AMH>",
        "conteudo_sha256": "<sha256 do conteudo canonico — ver `digest_conteudo`>"
      }
    }

Recusa (nada e' gravado) quando: a atestacao falta, `atestado` nao e' `true`, um campo da
atestacao esta vazio ou e' marcador de pendencia (PENDENTE/TODO/TBD/...), o `conteudo_sha256` nao
bate com o conteudo (adulteracao depois da assinatura), o tenant difere do da execucao, a linha
traz chave fora do conjunto FECHADO acima (nenhum lugar onde caiba dado de beneficiario — CPF,
nome, carteirinha), o status esta fora de {CREDENCIADO, DESCREDENCIADO}, ha prestador repetido,
ou uma data nao e' ISO.

**A assinatura e' humana.** Esta ferramenta VERIFICA que os campos de atestacao existem e que o
digest amarra o conteudo; ela nao assina, nao preenche campo de sign-off e nao e' uma verificacao
criptografica de PKI (o documento assinado fica com a AMH e e' apontado por `evidencia_ref`).

**Precedencia.** A baseline e' semente unica: um prestador que JA tem linha de situacao no ledger
(de outra baseline ou de um terminal do CRED) NAO recebe a linha da baseline — `CONFLITO`
reportado, nunca sobrescrito (a correcao vem do CRED, por terminal humano).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

from maezo.platform.prestador_status.ledger import (
    PRESTADOR_ID_RE,
    EntradaLedger,
    LedgerStatusPrestador,
)
from maezo.platform.prestador_status.projecao import (
    VERSAO_PROJECAO,
    Dimensao,
    Origem,
    ValorSituacao,
)

FORMATO_BASELINE: Final = "maezo-prestador-baseline/v1"

_CHAVES_RAIZ: Final = frozenset({"formato", "tenant", "data_referencia", "prestadores", "atestacao"})
_CHAVES_LINHA: Final = frozenset(
    {"prestador_id", "situacao", "data_efeito", "tipo_prestador", "especialidade", "regiao_saude"}
)
_CHAVES_ATESTACAO: Final = frozenset(
    {"atestado", "emissor", "responsavel", "data_assinatura", "evidencia_ref", "conteudo_sha256"}
)
_CAMPOS_TEXTO_ATESTACAO: Final = ("emissor", "responsavel", "data_assinatura", "evidencia_ref")
#: Marcadores de pendencia: um campo assim NAO e' uma assinatura (molde ADR-0007/erasure plan).
_PENDENCIA_RE: Final = re.compile(r"(?i)\b(pendente|todo|tbd|xxx|fixme|preencher|a definir)\b|^<.*>$")
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")


class BaselineRecusadaError(ValueError):
    """A baseline nao pode ser importada. Nada foi gravado."""


@dataclass(frozen=True, slots=True)
class LinhaBaseline:
    prestador_id: str
    situacao: ValorSituacao
    data_efeito: date | None
    tipo_prestador: str | None
    especialidade: str | None
    regiao_saude: str | None


@dataclass(frozen=True, slots=True)
class BaselineAtestada:
    tenant: str
    data_referencia: date
    conteudo_sha256: str
    evidencia_ref: str
    linhas: tuple[LinhaBaseline, ...]


def digest_conteudo(tenant: str, data_referencia: str, prestadores: Sequence[Mapping[str, Any]]) -> str:
    """sha256 do conteudo canonico (JSON com chaves ordenadas, sem espacos, UTF-8). E' o valor que
    o signatario da AMH confere e copia em `atestacao.conteudo_sha256`."""
    canonico = json.dumps(
        {"tenant": tenant, "data_referencia": data_referencia, "prestadores": list(prestadores)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _data_obrigatoria(valor: Any, onde: str) -> date:
    if not isinstance(valor, str) or len(valor) != 10:
        raise BaselineRecusadaError(f"{onde}: data ISO YYYY-MM-DD exigida")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise BaselineRecusadaError(f"{onde}: data ISO YYYY-MM-DD exigida") from None


def _data_opcional(valor: Any, onde: str) -> date | None:
    return None if valor is None else _data_obrigatoria(valor, onde)


def _texto_opcional(valor: Any, onde: str) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str) or not valor.strip():
        raise BaselineRecusadaError(f"{onde}: texto nao-vazio ou null exigido")
    return valor


def _validar_atestacao(bruta: Any, digest_calculado: str) -> tuple[str, str]:
    if not isinstance(bruta, dict):
        raise BaselineRecusadaError("atestacao ausente: baseline sem assinatura da AMH nao entra")
    if set(bruta) != _CHAVES_ATESTACAO:
        raise BaselineRecusadaError(
            f"atestacao: chaves exatas exigidas {sorted(_CHAVES_ATESTACAO)}, recebidas {sorted(bruta)}"
        )
    if bruta["atestado"] is not True:
        raise BaselineRecusadaError("atestacao: 'atestado' nao e' true — baseline nao atestada")
    for campo in _CAMPOS_TEXTO_ATESTACAO:
        valor = bruta[campo]
        if not isinstance(valor, str) or not valor.strip() or _PENDENCIA_RE.search(valor.strip()):
            raise BaselineRecusadaError(f"atestacao: '{campo}' vazio ou pendente — assinatura humana ausente")
    _data_obrigatoria(bruta["data_assinatura"], "atestacao.data_assinatura")
    declarado = bruta["conteudo_sha256"]
    if not isinstance(declarado, str) or not _SHA256_RE.fullmatch(declarado):
        raise BaselineRecusadaError("atestacao: 'conteudo_sha256' nao e' sha256 hex")
    if declarado != digest_calculado:
        raise BaselineRecusadaError(
            "atestacao: 'conteudo_sha256' nao bate com o conteudo — adulterada ou outra"
        )
    return declarado, str(bruta["evidencia_ref"]).strip()


def validar_baseline(bruta: Any, *, tenant: str) -> BaselineAtestada:
    """Valida TUDO antes de qualquer gravacao. Levanta `BaselineRecusadaError` na primeira falha."""
    if not isinstance(bruta, dict):
        raise BaselineRecusadaError("baseline: objeto JSON exigido")
    if set(bruta) != _CHAVES_RAIZ:
        raise BaselineRecusadaError(f"baseline: chaves exatas exigidas {sorted(_CHAVES_RAIZ)}")
    if bruta["formato"] != FORMATO_BASELINE:
        raise BaselineRecusadaError(f"baseline: formato {bruta['formato']!r} desconhecido")
    if bruta["tenant"] != tenant:
        raise BaselineRecusadaError("baseline: tenant do arquivo difere do tenant da execucao")
    data_referencia = _data_obrigatoria(bruta["data_referencia"], "data_referencia")
    prestadores = bruta["prestadores"]
    if not isinstance(prestadores, list) or not prestadores:
        raise BaselineRecusadaError("baseline: 'prestadores' tem de ser lista nao-vazia")
    digest = digest_conteudo(tenant, bruta["data_referencia"], prestadores)
    declarado, evidencia_ref = _validar_atestacao(bruta["atestacao"], digest)

    linhas: list[LinhaBaseline] = []
    vistos: set[str] = set()
    for indice, linha in enumerate(prestadores):
        onde = f"prestadores[{indice}]"
        if not isinstance(linha, dict):
            raise BaselineRecusadaError(f"{onde}: objeto exigido")
        extras = set(linha) - _CHAVES_LINHA
        if extras:
            # Conjunto FECHADO: nenhuma chave onde caiba dado de beneficiario (CPF, nome, carteira).
            raise BaselineRecusadaError(f"{onde}: chaves fora do formato {sorted(extras)}")
        prestador_id = linha.get("prestador_id")
        if not isinstance(prestador_id, str) or not PRESTADOR_ID_RE.fullmatch(prestador_id):
            raise BaselineRecusadaError(f"{onde}: prestador_id fora do formato")
        if prestador_id in vistos:
            raise BaselineRecusadaError(f"{onde}: prestador_id repetido")
        vistos.add(prestador_id)
        try:
            situacao = ValorSituacao(str(linha.get("situacao")))
        except ValueError:
            raise BaselineRecusadaError(
                f"{onde}: situacao fora de {[v.value for v in ValorSituacao]}"
            ) from None
        linhas.append(
            LinhaBaseline(
                prestador_id=prestador_id,
                situacao=situacao,
                data_efeito=_data_opcional(linha.get("data_efeito"), f"{onde}.data_efeito"),
                tipo_prestador=_texto_opcional(linha.get("tipo_prestador"), f"{onde}.tipo_prestador"),
                especialidade=_texto_opcional(linha.get("especialidade"), f"{onde}.especialidade"),
                regiao_saude=_texto_opcional(linha.get("regiao_saude"), f"{onde}.regiao_saude"),
            )
        )
    baseline = BaselineAtestada(
        tenant=tenant,
        data_referencia=data_referencia,
        conteudo_sha256=declarado,
        evidencia_ref=evidencia_ref,
        linhas=tuple(linhas),
    )
    # Forma de cada linha do ledger tambem e' validada AQUI, antes de qualquer gravacao: uma linha
    # que o banco recusaria derruba a importacao inteira, nunca a deixa pela metade.
    for linha_ok in baseline.linhas:
        try:
            entrada_baseline(baseline, linha_ok)
        except ValueError as exc:
            raise BaselineRecusadaError(f"prestador {linha_ok.prestador_id}: {exc}") from None
    return baseline


def entrada_baseline(baseline: BaselineAtestada, linha: LinhaBaseline) -> EntradaLedger:
    return EntradaLedger(
        tenant=baseline.tenant,
        prestador_id=linha.prestador_id,
        origem=Origem.BASELINE_AMH,
        dimensao=Dimensao.SITUACAO,
        valor=linha.situacao.value,
        data_efeito=linha.data_efeito,
        chave_evento=f"{Origem.BASELINE_AMH.value}:{baseline.conteudo_sha256}:{linha.prestador_id}",
        fonte_ref=baseline.conteudo_sha256,
        evidencia_ref=baseline.evidencia_ref,
        versao_projecao=VERSAO_PROJECAO,
        tipo_prestador=linha.tipo_prestador,
        especialidade=linha.especialidade,
        regiao_saude=linha.regiao_saude,
    )


class ResultadoLinha(StrEnum):
    GRAVADO = "GRAVADO"
    JA_REGISTRADO = "JA_REGISTRADO"
    CONFLITO = "CONFLITO"  # o prestador ja tem situacao no ledger: a baseline nao sobrescreve


@dataclass(frozen=True, slots=True)
class RelatorioImportacao:
    conteudo_sha256: str
    resultados: tuple[tuple[str, ResultadoLinha], ...]

    def contagem(self) -> dict[str, int]:
        saida = {r.value: 0 for r in ResultadoLinha}
        for _, resultado in self.resultados:
            saida[resultado.value] += 1
        return saida


async def importar_baseline(baseline: BaselineAtestada, ledger: LedgerStatusPrestador) -> RelatorioImportacao:
    """Grava a baseline JA validada. Idempotente: reimportar o mesmo arquivo da `JA_REGISTRADO`.

    Erros do ledger (`LedgerIndisponivelError`, `LedgerConflitoError`) propagam — quem chama
    reporta a falha; as linhas ja gravadas ficam (cada uma e' um fato atestado e idempotente)."""
    resultados: list[tuple[str, ResultadoLinha]] = []
    for linha in baseline.linhas:
        entrada = entrada_baseline(baseline, linha)
        historico = await ledger.historico(baseline.tenant, linha.prestador_id)
        ja_tem_esta = any(e.chave_evento == entrada.chave_evento for e in historico)
        outra_situacao = any(
            e.dimensao is Dimensao.SITUACAO and e.chave_evento != entrada.chave_evento for e in historico
        )
        if outra_situacao and not ja_tem_esta:
            resultados.append((linha.prestador_id, ResultadoLinha.CONFLITO))
            continue
        gravou = await ledger.registrar(entrada)
        resultados.append(
            (linha.prestador_id, ResultadoLinha.GRAVADO if gravou else ResultadoLinha.JA_REGISTRADO)
        )
    return RelatorioImportacao(baseline.conteudo_sha256, tuple(resultados))


__all__ = [
    "FORMATO_BASELINE",
    "BaselineAtestada",
    "BaselineRecusadaError",
    "LinhaBaseline",
    "RelatorioImportacao",
    "ResultadoLinha",
    "digest_conteudo",
    "entrada_baseline",
    "importar_baseline",
    "validar_baseline",
]
