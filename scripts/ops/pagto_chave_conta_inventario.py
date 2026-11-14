#!/usr/bin/env python3
"""Inventario SOMENTE-LEITURA das ordens PAGTO sob a chave lote do handoff CONTAS (opcao A, 2026-11-01).

Decisao do dono de 2026-11-01: a chave PAGTO cunhada por `operadora.contas.handoff_pagamento` passa a
carregar a conta quando CONTAS rodou na forma guia/conta — com o prestador, DEC-30 D13:
`PAGTO-{t}-C-{prestador}-{guia}-{conta}` —, dentro da janela unica de migracao de chaves do ADR-0068
(forma registrada pelo ADR-0074). As ordens ja' criadas sob a forma lote
(`PAGTO-{t}-{lote}-{prestador}`) precisam de um plano — este script e' a parte mecanica dele:

* classifica cada ordem PAGTO exportada do historico do motor (`--pagto`);
* para cada ordem lote de uma conta conhecida, propoe a chave por conta (a linha de ALIAS que o job
  da janela INSERE — nunca reescreve a chave de instancia viva nem apaga claim, ADR-0068 §3 item 3);
* cruza com as instancias de CONTAS exportadas (`--contas`, opcional) e lista as contas guia/conta
  cujo handoff devolveu "ja existe" para uma ordem que NAO e' delas — os pagamentos perdidos, para
  reemissao HUMANA.

NAO TOCA O MOTOR NEM O BANCO: le dois arquivos JSON e escreve o relatorio em stdout. Nao decide nada:
reemitir uma ordem e' ato humano (PAGTO tem `UT_AnaliseAdmissibilidade` obrigatoria, I-PAGTO-1).
Executar o plano pertence a janela do ADR-0068 (`docs/runbooks/pagto-chave-por-conta-migracao.md`).

Formato de entrada (lista JSON; gerada por leitura do historico do motor, ver o runbook):
  --pagto  [{"id", "businessKey", "state", "variables": {"tenant_id", "numero_lote_tiss",
             "prestador_id", "numero_guia_tiss", "numero_conta"?}}]
  --contas [{"id", "businessKey", "variables": {"tenant_id", "prestador_id", "numero_guia_tiss",
             "numero_conta", "pagto_instance_id", "pagto_already_existed"}}]

Uso: python scripts/ops/pagto_chave_conta_inventario.py --pagto pagto.json [--contas contas.json] [--estrito]
`--estrito` sai 2 quando ha item que exige humano (indeterminada, prestador ou guia com '-',
pagamento perdido).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maezo.domain.chave_de_negocio import (
    BusinessKeyComponentError,
    contas_adjudica_por_conta,
    pagto_business_key_por_conta,
)
from maezo.platform.keys import key_segment

#: Disposicoes de uma ordem PAGTO. As tres ultimas exigem humano.
FORA_DE_ESCOPO = "fora_de_escopo"  # forma ordem (Andre) ou guia+glosa (RECURSO): nao e' desta migracao
JA_POR_CONTA = "ja_por_conta"  # nasceu na forma por conta (fase escrita_alvo)
FORMA_LOTE = "forma_lote"  # CONTAS rodou na forma lote: a chave lote E' a identidade; fica
ALIAS_POR_CONTA = "alias_por_conta"  # ordem lote de UMA conta conhecida: alias insert-only na janela
ALIAS_BLOQUEADO_GUIA_COM_HIFEN = "alias_bloqueado_guia_com_hifen"  # R-101: a chave por conta recusa
ALIAS_BLOQUEADO_PRESTADOR_COM_HIFEN = "alias_bloqueado_prestador_com_hifen"  # DEC-30 D13: idem
INDETERMINADA = "indeterminada"  # semeada antes da conta entrar na semente: reconciliacao humana

EXIGE_HUMANO: frozenset[str] = frozenset(
    {ALIAS_BLOQUEADO_GUIA_COM_HIFEN, ALIAS_BLOQUEADO_PRESTADOR_COM_HIFEN, INDETERMINADA}
)


def _chave_lote(variaveis: Mapping[str, Any]) -> str:
    """A chave lote que `contas._pagto_business_key` cunha (mesma normalizacao `key_segment`)."""
    tenant = key_segment(variaveis.get("tenant_id"))
    lote = key_segment(variaveis.get("numero_lote_tiss"))
    prestador = key_segment(variaveis.get("prestador_id"))
    if not (tenant and lote and prestador):
        return ""
    return f"PAGTO-{tenant}-{lote}-{prestador}"


def classificar_ordem(ordem: Mapping[str, Any]) -> dict[str, Any]:
    """Uma linha do relatorio para UMA ordem PAGTO do historico."""
    variaveis: Mapping[str, Any] = ordem.get("variables") or {}
    chave = str(ordem.get("businessKey") or "")
    tenant = key_segment(variaveis.get("tenant_id"))
    prestador = key_segment(variaveis.get("prestador_id"))
    guia = key_segment(variaveis.get("numero_guia_tiss"))
    conta = key_segment(variaveis.get("numero_conta"))
    linha: dict[str, Any] = {
        "instance_id": ordem.get("id"),
        "business_key": chave,
        "state": ordem.get("state"),
        "chave_por_conta": None,
    }
    if tenant and chave.startswith(f"PAGTO-{tenant}-C-"):
        return {**linha, "disposicao": JA_POR_CONTA}
    if not chave or chave != _chave_lote(variaveis):
        return {**linha, "disposicao": FORA_DE_ESCOPO}
    if "numero_conta" not in variaveis:
        return {**linha, "disposicao": INDETERMINADA}
    if not contas_adjudica_por_conta(numero_guia_tiss=guia, numero_conta=conta):
        return {**linha, "disposicao": FORMA_LOTE}
    try:
        alvo = pagto_business_key_por_conta(
            tenant_id=tenant, prestador_id=prestador, numero_guia_tiss=guia, numero_conta=conta
        )
    except BusinessKeyComponentError as exc:
        # O compositor confere o prestador ANTES da guia; o motivo exato vai em `motivo`.
        bloqueio = ALIAS_BLOQUEADO_PRESTADOR_COM_HIFEN if "-" in prestador else ALIAS_BLOQUEADO_GUIA_COM_HIFEN
        return {**linha, "disposicao": bloqueio, "motivo": str(exc)}
    return {**linha, "disposicao": ALIAS_POR_CONTA, "chave_por_conta": alvo}


def contas_sem_ordem(
    contas: Sequence[Mapping[str, Any]], ordens: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Contas guia/conta cujo handoff disse "ja existe" para uma ordem que nao e' comprovadamente delas."""
    por_instancia = {str(o.get("id")): (o.get("variables") or {}) for o in ordens}
    perdidas: list[dict[str, Any]] = []
    for instancia in contas:
        variaveis: Mapping[str, Any] = instancia.get("variables") or {}
        prestador = key_segment(variaveis.get("prestador_id"))
        guia = key_segment(variaveis.get("numero_guia_tiss"))
        conta = key_segment(variaveis.get("numero_conta"))
        if not contas_adjudica_por_conta(numero_guia_tiss=guia, numero_conta=conta):
            continue
        if variaveis.get("pagto_already_existed") is not True:
            continue
        ordem_id = str(variaveis.get("pagto_instance_id") or "")
        da_ordem = por_instancia.get(ordem_id)
        if da_ordem is None:
            motivo = "ordem_fora_do_export"
        elif "prestador_id" in variaveis and key_segment(da_ordem.get("prestador_id")) != prestador:
            # DEC-30 D13: a identidade da conta inclui o prestador.
            motivo = "ordem_de_outro_prestador"
        elif key_segment(da_ordem.get("numero_guia_tiss")) != guia:
            motivo = "ordem_de_outra_guia"
        elif "numero_conta" not in da_ordem:
            motivo = "ordem_sem_conta_semeada"
        elif key_segment(da_ordem.get("numero_conta")) != conta:
            motivo = "ordem_de_outra_conta"
        else:
            continue
        perdidas.append(
            {
                "contas_instance_id": instancia.get("id"),
                "pagto_instance_id": ordem_id or None,
                "motivo": motivo,
            }
        )
    return perdidas


def inventariar(
    ordens: Sequence[Mapping[str, Any]], contas: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    linhas = [classificar_ordem(o) for o in ordens]
    contagem: dict[str, int] = {}
    for linha in linhas:
        contagem[linha["disposicao"]] = contagem.get(linha["disposicao"], 0) + 1
    perdidas = contas_sem_ordem(contas, ordens)
    return {
        "somente_leitura": True,
        "ordens": linhas,
        "contagem": dict(sorted(contagem.items())),
        "contas_sem_ordem": perdidas,
        "exige_humano": sum(contagem.get(d, 0) for d in EXIGE_HUMANO) + len(perdidas),
    }


def _ler_lista(caminho: Path) -> list[dict[str, Any]]:
    dado = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(dado, list):
        raise SystemExit(f"{caminho}: esperado uma lista JSON de instancias")
    return dado


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pagto", type=Path, required=True)
    parser.add_argument("--contas", type=Path)
    parser.add_argument("--estrito", action="store_true")
    args = parser.parse_args(argv)
    relatorio = inventariar(_ler_lista(args.pagto), _ler_lista(args.contas) if args.contas else [])
    json.dump(relatorio, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 2 if args.estrito and relatorio["exige_humano"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
