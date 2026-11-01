#!/usr/bin/env python3
"""Planilha de revisao clinica PREENCHIDA -> YAMLs de ratificacao por tabela + candidatos ao registro.

GERA, NAO APLICA. Le a planilha que `revisao_clinica.py` gerou e o medico-auditor preencheu, e escreve
numa pasta de saida (que nao pode ter os arquivos ja'):

  * `<tabela>-ratificacao-medico-auditor.yaml`, no formato de
    `spec/processes/dmn/triage-redflag-pediatric-neonatal-ratification.yaml` (`regras_ratificadas`,
    `revisor`, `capacidade`, `ratificado_em`, `attestation_basis`, `tabela_viva.sha256`) — SO' com as
    regras marcadas "Confirmo". As demais ficam listadas em `regras_nao_ratificadas` com o veredito e o
    ajuste pedido, para virar PR de mudanca de regra;
  * `aprovacoes-candidatas.yaml`: as entradas para `aprovados` de `spec/aprovacoes-de-regras.yaml` —
    uma TABELA INTEIRA so' entra se TODAS as regras dela foram "Confirmo".

O QUE REPROVA (nada e' escrito): revisor/data ausentes, data invalida ou futura; tabela cujo arquivo
mudou depois de gerada a planilha (o hash lido pelo medico nao e' o atual — gere e revise de novo);
planilha sem alguma regra da tabela atual, ou com regra que a tabela nao tem; veredito fora da lista.

Quem aplica e' uma PESSOA, num PR revisado: copiar as ratificacoes para `spec/processes/dmn/`, mover as
entradas de `pendentes` para `aprovados` e rodar `make check-aprovacao-de-regras`. Nenhum agente aplica
(ADR-0007: sem auto-certificacao).

Uso (precisa de `openpyxl` e `PyYAML`):

    python scripts/sme/revisao_clinica_para_yaml.py --planilha "<arquivo.xlsx>" --saida "<pasta>"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

_RAIZ: Final[Path] = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:  # rodado como script: `scripts.*` precisa da raiz no caminho
    sys.path.insert(0, str(_RAIZ))

from scripts.sme import revisao_clinica as rc  # noqa: E402

PAPEL_PADRAO: Final[str] = "medico-auditor"
ARQUIVO_CANDIDATOS: Final[str] = "aprovacoes-candidatas.yaml"


class ConversaoRecusadaError(ValueError):
    """A planilha nao permite gerar nada com seguranca; a mensagem lista todos os motivos."""


@dataclass(frozen=True)
class Revisor:
    nome: str
    capacidade: str
    data: str
    papel: str = PAPEL_PADRAO


@dataclass
class Resultado:
    ratificacoes: dict[str, dict[str, Any]] = field(default_factory=dict)
    aprovados: list[dict[str, Any]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def nome_da_ratificacao(decisao: str) -> str:
    return f"{decisao.replace('_', '-')}-ratificacao-medico-auditor.yaml"


def revisor_da_planilha(pendencias: list[rc.Linha]) -> dict[str, str]:
    """Os campos da aba de pendencias, por rotulo (o que a pessoa escreveu; vazio se nao escreveu)."""
    valor = "Valor (preenchido pela pessoa)"
    return {linha.get("Campo", ""): linha.get(valor, "") for linha in pendencias}


def _validar_revisor(revisor: Revisor, hoje: date) -> list[str]:
    erros: list[str] = []
    if not revisor.nome.strip():
        erros.append(f"revisor sem nome (aba '{rc.ABA_PENDENCIAS}', campo '{rc.PEND_REVISOR}', ou --revisor)")
    if not revisor.capacidade.strip():
        erros.append(f"revisor sem capacidade (campo '{rc.PEND_CAPACIDADE}' ou --capacidade)")
    try:
        quando = date.fromisoformat(revisor.data.strip()[:10])
    except ValueError:
        erros.append(f"data da revisao invalida: {revisor.data!r} (AAAA-MM-DD)")
    else:
        if quando > hoje:
            erros.append(f"data da revisao {quando.isoformat()} esta' no futuro")
    return erros


def converter(
    linhas: list[rc.Linha],
    revisor: Revisor,
    *,
    raiz: Path = rc.RAIZ_REPO,
    planilha_nome: str = "planilha de revisao clinica",
    hoje: date | None = None,
) -> Resultado:
    """O nucleo puro: linhas da aba 'Regras de alarme' (como dicionarios) -> o que gerar. Nao escreve."""
    hoje = hoje or date.today()
    erros = _validar_revisor(revisor, hoje)
    data = revisor.data.strip()[:10]
    resultado = Resultado()
    por_arquivo: dict[str, list[rc.Linha]] = {}
    for linha in linhas:
        por_arquivo.setdefault(linha.get(rc.COL_ARQUIVO, ""), []).append(linha)
        veredito = linha.get(rc.COL_VEREDITO, "").strip()
        onde = f"{linha.get(rc.COL_TABELA)}/{linha.get(rc.COL_REGRA)}"
        if veredito and veredito not in rc.VEREDITOS:
            erros.append(f"{onde}: veredito {veredito!r} fora da lista")
        if veredito == "Perigoso":
            resultado.avisos.append(f"PERIGOSO: {onde} — tratar antes de qualquer publicacao")

    for decisao in rc.TABELAS_REVISADAS:
        relativo = f"{rc.DMN_DIR}/{decisao}.dmn"
        da_tabela = por_arquivo.pop(relativo, [])
        if not da_tabela:
            resultado.avisos.append(f"{decisao}: nenhuma linha na planilha — nada gerado")
            continue
        atual = rc.sha256_lf(raiz / relativo)
        lidos = {linha.get(rc.COL_SHA, "") for linha in da_tabela}
        if lidos != {atual}:
            erros.append(
                f"{decisao}: o arquivo mudou depois de gerada a planilha (lido {sorted(lidos)}, atual "
                f"{atual[:12]}…) — gere a planilha de novo e revise; ratificar bytes que ninguem leu nao vale"
            )
            continue
        ordem = [r.id for r in rc.ler_dmn(raiz, relativo).regras]
        ids = [linha.get(rc.COL_REGRA, "") for linha in da_tabela]
        if sorted(ids) != sorted(ordem):
            erros.append(f"{decisao}: regras da planilha {sorted(ids)} != regras da tabela {sorted(ordem)}")
            continue
        vereditos = {linha[rc.COL_REGRA]: linha.get(rc.COL_VEREDITO, "").strip() for linha in da_tabela}
        confirmadas = [r for r in ordem if vereditos[r] == rc.VEREDITO_CONFIRMO]
        if not confirmadas:
            resultado.avisos.append(f"{decisao}: nenhuma regra 'Confirmo' — sem ratificacao")
            continue
        pendentes = [
            {
                "regra": linha[rc.COL_REGRA],
                "veredito": vereditos[linha[rc.COL_REGRA]] or "(nao revisada)",
                "ajuste_proposto": linha.get(rc.COL_AJUSTE, ""),
                "comentario": linha.get(rc.COL_COMENTARIO, ""),
            }
            for regra_id in ordem
            for linha in da_tabela
            if linha[rc.COL_REGRA] == regra_id and vereditos[regra_id] != rc.VEREDITO_CONFIRMO
        ]
        nome = nome_da_ratificacao(decisao)
        resultado.ratificacoes[nome] = {
            "version": 1,
            "alvo": relativo,
            "decisao_alvo": decisao,
            "regras_ratificadas": confirmadas,
            "regras_nao_ratificadas": pendentes,
            "ratificado": True,
            "revisor": revisor.nome.strip(),
            "papel": revisor.papel,
            "capacidade": revisor.capacidade.strip(),
            "ratificado_em": data,
            "attestation_basis": (
                f"veredito 'Confirmo' regra a regra na {planilha_nome}, preenchida pelo revisor; YAML gerado "
                "por scripts/sme/revisao_clinica_para_yaml.py (sem aplicar). Vale SO' para as regras "
                "listadas em regras_ratificadas e SO' para os bytes de tabela_viva.sha256."
            ),
            "tabela_viva": {"path": relativo, "sha256": atual},
        }
        if not pendentes:
            resultado.aprovados.append(
                {
                    "artefato": relativo,
                    "sha256": atual,
                    "aprovado_por": revisor.nome.strip(),
                    "papel": revisor.papel,
                    "aprovado_em": data,
                    "evidencia": f"{planilha_nome}; spec/processes/dmn/{nome}",
                }
            )
        else:
            resultado.avisos.append(
                f"{decisao}: {len(confirmadas)}/{len(ordem)} confirmadas — ratificacao PARCIAL, tabela segue "
                "em `pendentes` do registro"
            )
    codigo = por_arquivo.pop(rc.ALARME_CODIGO, [])
    if codigo:
        confirmadas_cod = [
            linha[rc.COL_REGRA] for linha in codigo if linha.get(rc.COL_VEREDITO) == "Confirmo"
        ]
        resultado.avisos.append(
            f"alarme por texto (codigo, DL-0123): {len(confirmadas_cod)}/{len(codigo)} 'Confirmo' — nao e' "
            "artefato publicavel; registre o veredito no PR que ratifica a DL-0123"
        )
    for resto in por_arquivo:
        erros.append(f"linhas com arquivo desconhecido na planilha: {resto!r}")
    if erros:
        raise ConversaoRecusadaError("\n".join(f"  - {e}" for e in erros))
    return resultado


def aplicar_em_copia(registro: dict[str, Any], aprovados: list[dict[str, Any]]) -> dict[str, Any]:
    """Como o registro FICARIA com os candidatos (tira de `pendentes`, poe em `aprovados`). Devolve uma
    copia; nao escreve arquivo. Serve ao teste e a quem for aplicar conferir o resultado antes."""
    novos = {a["artefato"] for a in aprovados}
    copia = dict(registro)
    copia["pendentes"] = [p for p in (registro.get("pendentes") or []) if p.get("artefato") not in novos]
    copia["aprovados"] = [*(registro.get("aprovados") or []), *aprovados]
    return copia


_CABECALHO_RATIFICACAO: Final[str] = """\
# RATIFICACAO DO MEDICO-AUDITOR — GERADA de uma planilha preenchida, AINDA NAO APLICADA.
# Gerador: scripts/sme/revisao_clinica_para_yaml.py.
# Formato: o de triage-redflag-pediatric-neonatal-ratification.yaml.
# Vale SO' para `regras_ratificadas` e SO' para os bytes de `tabela_viva.sha256` (CRLF->LF).
# Aplicar = copiar para spec/processes/dmn/ num PR revisado pelo proprio revisor. Nao edite a mao.
"""
_CABECALHO_CANDIDATOS: Final[str] = """\
# CANDIDATOS a `aprovados` de spec/aprovacoes-de-regras.yaml — GERADOS, NAO APLICADOS.
# So' entra tabela com TODAS as regras 'Confirmo'. Aplicar = num PR revisado, tirar cada artefato de
# `pendentes` e colar a entrada em `aprovados`; depois `make check-aprovacao-de-regras` tem de ficar verde.
"""


def _dump(dados: Any) -> str:
    return yaml.safe_dump(dados, sort_keys=False, allow_unicode=True, width=110)


def escrever(resultado: Resultado, saida: Path) -> list[Path]:
    """Escreve os YAMLs em `saida`. Recusa se algum arquivo ja existe (nunca sobrescreve)."""
    alvos = {saida / nome: _CABECALHO_RATIFICACAO + _dump(d) for nome, d in resultado.ratificacoes.items()}
    alvos[saida / ARQUIVO_CANDIDATOS] = _CABECALHO_CANDIDATOS + _dump({"aprovados": resultado.aprovados})
    existentes = [p for p in alvos if p.exists()]
    if existentes:
        raise FileExistsError(f"ja existem (nao sobrescrevo): {[str(p) for p in existentes]}")
    saida.mkdir(parents=True, exist_ok=True)
    for caminho, texto in alvos.items():
        caminho.write_text(texto, encoding="utf-8", newline="\n")
    return list(alvos)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Planilha de revisao clinica preenchida -> YAMLs (sem aplicar).")
    p.add_argument("--planilha", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True, help="pasta de saida (fora do repo ou revisada)")
    p.add_argument("--revisor", default=None, help="sobrepoe o nome da aba de pendencias")
    p.add_argument("--capacidade", default=None)
    p.add_argument("--data", default=None, help="AAAA-MM-DD; sobrepoe a aba de pendencias")
    p.add_argument("--papel", default=PAPEL_PADRAO)
    p.add_argument("--raiz", type=Path, default=rc.RAIZ_REPO)
    args = p.parse_args(argv)
    reconfigurar = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigurar):
        reconfigurar(encoding="utf-8")

    campos = revisor_da_planilha(rc.ler_aba(args.planilha, rc.ABA_PENDENCIAS))
    capacidade = args.capacidade or campos.get(rc.PEND_CAPACIDADE, "")
    crm = campos.get(rc.PEND_CRM, "")
    revisor = Revisor(
        nome=args.revisor or campos.get(rc.PEND_REVISOR, ""),
        capacidade=f"{capacidade} ({crm})" if capacidade and crm else capacidade,
        data=args.data or campos.get(rc.PEND_DATA, ""),
        papel=args.papel,
    )
    try:
        resultado = converter(
            rc.ler_aba(args.planilha, rc.ABA_REGRAS),
            revisor,
            raiz=args.raiz,
            planilha_nome=args.planilha.name,
        )
        escritos = escrever(resultado, args.saida)
    except (ConversaoRecusadaError, FileExistsError) as exc:
        print(f"RECUSADO, nada escrito:\n{exc}", file=sys.stderr)
        return 1
    for aviso in resultado.avisos:
        print(f"aviso: {aviso}")
    for caminho in escritos:
        print(f"gerado: {caminho}")
    print("NADA FOI APLICADO. Quem aplica e' uma pessoa, num PR revisado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
