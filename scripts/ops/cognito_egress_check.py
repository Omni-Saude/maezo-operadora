#!/usr/bin/env python3
"""Confere o drift entre os `/32` do Cognito pinados no tfvars do portal e a resolucao real.

O SG das tasks do portal libera 443 para uma lista EXATA de `/32` (`https_egress_ipv4_cidrs`
em `deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars`). 13 deles sao enderecos PUBLICOS do
Cognito — `cognito-idp.sa-east-1.amazonaws.com` (JWKS do issuer) e
`amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com` (POST /oauth2/token) — e a AWS os rotaciona
sem aviso: as rodadas de 25/09 e 28/09/2026 foram quebras de login/callback consertadas A POS o
fato. Este script compara a resolucao de hoje com a lista pinada e aponta o drift ANTES da
proxima quebra:

- MISSING — IP resolvido hoje e NAO pinado: e o candidato a ser o proximo outlier de rota;
  quando o Cognito passar a responder por ele, o SG derruba o handshake. Sai 1.
- STALE   — pinado e que nao resolve mais: cruft (libera a mais, nao quebra). Sai 0.
- EXATO   — nada a fazer. Sai 0.

Os `/32` em `10.x` do mesmo bloco sao ENIs de VPC endpoint (proprietarios, estaticos) e NUNCA
entram na comparacao.

LIMITACAO HONESTA: a resposta de DNS varia por resolver e por regiao. A lista pinada foi medida
DE DENTRO DA VPC (task avulsa no cluster, com handshake TLS confirmado em cada destino — ver o
runbook); desta estacao o Cognito pode devolver OUTRO subconjunto, entao um MISSING daqui pode
ser so artefato do seu resolver. Nesse caso, meca de dentro da VPC e compare com `--from-file`:

    dig +short cognito-idp.sa-east-1.amazonaws.com
    dig +short amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com
    # cole os IPs, um por linha, em ips.txt e rode:
    python scripts/ops/cognito_egress_check.py --from-file ips.txt

Saidas: 0 = nenhum MISSING (STALE so higiene); 1 = drift, com as linhas exatas a colar no
tfvars; 2 = erro operacional (host sem resolucao, tfvars ilegivel ou bloco ausente).

Uso: python scripts/ops/cognito_egress_check.py [--tfvars PORTAL.AUTO.TFVARS] [--from-file IPS.TXT]
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

RUNBOOK = "docs/runbooks/portal-dev-provisionamento.md:210-217"
TFVARS_PADRAO = Path("deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars")

# JWKS do issuer + POST /oauth2/token do BPM dev — os dois destinos publicos do bloco.
HOSTS = (
    "cognito-idp.sa-east-1.amazonaws.com",
    "amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com",
)

_CHAVE = "https_egress_ipv4_cidrs"


def parse_https_egress_cidrs(texto: str) -> tuple[int, list[tuple[int, str]]]:
    """Le SO o bloco `https_egress_ipv4_cidrs` de um .tfvars -> (linha de abertura, entradas).

    Cada entrada e `(numero_da_linha, valor_citado)` — o numero de linha serve para a
    remedicao apontar ONDE colar. Comentarios `//` e `#` sao descartados; o bloco termina na
    primeira `]` (entradas na mesma linha dela contam).
    """
    linhas = texto.splitlines()
    padrao = re.compile(rf"\s*{re.escape(_CHAVE)}\s*=\s*\[")
    abertura = next((n for n, linha in enumerate(linhas, start=1) if padrao.match(linha)), None)
    if abertura is None:
        raise ValueError(f"bloco `{_CHAVE} = [ ... ]` nao encontrado")
    entradas: list[tuple[int, str]] = []
    for numero in range(abertura, len(linhas) + 1):
        corpo = re.sub(r"//.*$|#.*$", "", linhas[numero - 1])
        fechamento = corpo.find("]")
        if fechamento != -1:
            corpo = corpo[:fechamento]
        entradas.extend((numero, valor) for valor in re.findall(r'"([^"]+)"', corpo))
        if fechamento != -1:
            return abertura, entradas
    raise ValueError(f"bloco `{_CHAVE}` sem `]` de fechamento")


def separa_cidrs(cidrs: Iterable[str]) -> tuple[set[str], set[str], list[str]]:
    """Divide o bloco em (pins publicos do Cognito, /32 proprietarios, entradas invalidas).

    `proprietarios` = redes privativas (10.x e companhia): ENIs de VPC endpoint, donas do
    proprio ciclo de vida — o checker nao as julga. `invalidos` = qualquer coisa que nao seja
    IPv4 /32 (reportada, fora do escopo). Os IPs voltam SEM o `/32`, no formato do DNS.
    """
    pins: set[str] = set()
    proprietarios: set[str] = set()
    invalidos: list[str] = []
    for cidr in cidrs:
        try:
            rede = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            invalidos.append(cidr)
            continue
        if rede.version != 4 or rede.prefixlen != 32:
            invalidos.append(cidr)
        elif rede.is_private:
            proprietarios.add(str(rede.network_address))
        else:
            pins.add(str(rede.network_address))
    return pins, proprietarios, invalidos


def classifica(pins: set[str], resolvidos: set[str]) -> tuple[list[str], list[str]]:
    """(MISSING = resolvido e nao pinado, STALE = pinado e nao resolvido), por ordem numerica."""
    return _ordena(resolvidos - pins), _ordena(pins - resolvidos)


def filtra_publicos(ips: set[str]) -> tuple[set[str], set[str]]:
    """(publicos, privativos) — IP privativo nao compete com pin de Cognito publico."""
    publicos = {ip for ip in ips if not ipaddress.ip_address(ip).is_private}
    return publicos, ips - publicos


def carregar_ips_medidos(texto: str) -> tuple[set[str], list[str]]:
    """Le `ips.txt` (1 IP ou `IP/32` por linha; vazio e `#` ignorados) -> (ips, linhas rejeitadas)."""
    ips: set[str] = set()
    rejeitadas: list[str] = []
    for linha in texto.splitlines():
        bruto = linha.split("#", 1)[0].strip()
        if not bruto:
            continue
        try:
            ips.add(str(ipaddress.ip_address(bruto.split("/", 1)[0])))
        except ValueError:
            rejeitadas.append(linha.strip())
    return ips, rejeitadas


def resolver(host: str) -> set[str]:
    """Todos os A records do host via getaddrinfo (dedup, so IPv4)."""
    respostas = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    return {resposta[4][0] for resposta in respostas}


def remedicao(faltando: Sequence[str], tfvars: Path, linha_bloco: int) -> str:
    """Veredicto acionavel: runbook + as linhas EXATAS a colar no tfvars."""
    entradas = "\n".join(f'    "{ip}/32",' for ip in faltando)
    return (
        f"DRIFT DE EGRESSO: {len(faltando)} IP(s) que o DNS devolve HOJE e NAO esta(m) pinado(s) "
        f"em {tfvars}.\n"
        f"{entradas}\n"
        "\n"
        "Remediacao (nunca alargar para 0.0.0.0/0 — `/32` e controle de IP, nao de FQDN):\n"
        "  1. Confirme cada IP medindo DE DENTRO DA VPC (task avulsa no cluster, com handshake\n"
        f"     TLS confirmado), conforme o runbook {RUNBOOK} — o DNS desta maquina pode divergir\n"
        "     do da VPC; se divergir, rode de novo com `--from-file` sobre os IPs medidos la.\n"
        f"  2. Cole a(s) linha(s) acima no bloco `{_CHAVE}` de {tfvars} (abre na linha\n"
        f"     {linha_bloco}), com o comentario de proveniencia `[DD/MM/AAAA] enderecos medidos`.\n"
        "  3. Abra o PR do tfvars; sem isso o proximo outlier de rota derruba JWKS e /oauth2/token."
    )


def _ordena(ips: Iterable[str]) -> list[str]:
    return [str(ip) for ip in sorted(ipaddress.ip_address(ip) for ip in ips)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drift entre os /32 do Cognito pinados no tfvars do portal e a resolucao real.",
    )
    parser.add_argument("--tfvars", type=Path, default=TFVARS_PADRAO, help=f"padrao: {TFVARS_PADRAO}")
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        metavar="IPS.TXT",
        help="compara contra IPs MEDIDOS num arquivo (dig dentro da VPC) em vez de resolver aqui",
    )
    parser.add_argument("--host", action="append", help="hostname publico a conferir; repetivel")
    args = parser.parse_args(argv)

    try:
        entradas = parse_https_egress_cidrs(args.tfvars.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as erro:
        print(f"ERRO: {args.tfvars}: {erro}", file=sys.stderr)
        return 2
    linha_bloco, pares = entradas
    pins, proprietarios, invalidos = separa_cidrs(cidr for _, cidr in pares)

    if args.from_file is None:
        medidos: set[str] = set()
        for host in HOSTS + (tuple(args.host) if args.host else ()):
            try:
                ips = resolver(host)
            except OSError as erro:
                print(f"ERRO: {host} nao resolveu ({erro}).", file=sys.stderr)
                print(
                    "ERRO: sem DNS confiavel nao ha veredicto; rode de dentro da VPC + --from-file.",
                    file=sys.stderr,
                )
                return 2
            if not ips:
                print(f"ERRO: {host} nao devolveu A record nenhum.", file=sys.stderr)
                return 2
            medidos |= ips
            print(f"DNS {host} -> {', '.join(_ordena(ips))}")
        origem = f"DNS vivo desta maquina ({len(HOSTS) + (len(args.host) if args.host else 0)} hosts)"
    else:
        try:
            medidos, rejeitadas = carregar_ips_medidos(args.from_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as erro:
            print(f"ERRO: {args.from_file}: {erro}", file=sys.stderr)
            return 2
        for linha in rejeitadas:
            print(f"AVISO: linha ignorada em {args.from_file}: {linha!r}", file=sys.stderr)
        if not medidos:
            print(f"ERRO: {args.from_file} nao tem IP valido nenhum.", file=sys.stderr)
            return 2
        origem = f"arquivo medido {args.from_file}"

    medidos, privativos = filtra_publicos(medidos)
    if privativos:
        fora = ", ".join(_ordena(privativos))
        print(f"NOTA: IP(s) privativo(s) fora da comparacao (VPCE/proprietario): {fora}")

    faltando, obsoletos = classifica(pins, medidos)
    detalhe = f" (+{len(proprietarios)} proprietarios privativos, ex. ENIs 10.x, fora do escopo)"
    if invalidos:
        detalhe += f", {len(invalidos)} entrada(s) invalida(s): {', '.join(invalidos)}"
    print(f"\npins: {len(pins)} /32 publicos de Cognito em {args.tfvars}{detalhe}")
    print(f"origem: {origem} — {len(medidos)} IP(s) publico(s)")
    if faltando:
        print(f"MISSING ({len(faltando)}): resolvido(s) HOJE e NAO pinado(s) -> {', '.join(faltando)}")
    if obsoletos:
        cruft = ", ".join(obsoletos)
        print(f"STALE   ({len(obsoletos)}): pinado(s) que nao resolve(m) mais (cruft) -> {cruft}")
    if not faltando and not obsoletos:
        print("EXATO: a resolucao de hoje esta 100% coberta pelos pins do tfvars.")
        return 0
    if faltando:
        print(f"\n{remedicao(faltando, args.tfvars, linha_bloco)}")
        return 1
    print(f"\nHigiene: remova o(s) STALE de {args.tfvars} num PR proprio (nao e' urgente, sai 0).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
