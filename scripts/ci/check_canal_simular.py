#!/usr/bin/env python3
"""Cerca de CI: a rota `/receptor/simular` do Canal de Teste so' pode existir em dev, e as
tres cercas que a contem nao podem desaparecer.

Por que esta cerca existe
--------------------------
`src/maezo/platform/testchannel/server.py::_simular_whatsapp` assina um envelope sintetico de
WhatsApp com `WHATSAPP_APP_SECRET` e o encaminha ao receptor. A assinatura que ela produz e'
INDISTINGUIVEL da da Meta — e' o mesmo HMAC sobre os mesmos bytes. Quem alcanca o canal
consegue, portanto, fabricar uma mensagem de beneficiario.

Isso e' aceitavel em `dev-sa-east-1`, atras do Cloudflare Access, com telefone restrito a faixa
de teste, porque o alternativo e' o time nao conseguir testar a triagem sem CLI. Nao e'
aceitavel em nenhum outro ambiente, e a decisao de onde ligar nao pode depender de alguem
lembrar: e' isto que a cerca mecaniza.

O que ela reprova
------------------
  1. `CANAL_SIMULAR_RECEPTOR` ligado ("1") em qualquer `deploy/**` que nao seja o ambiente
     `dev-sa-east-1`. Ligar em staging, prod ou DR e' erro de CI, nao conversa de review.
  2. `WHATSAPP_APP_SECRET` entregue ao container do CANAL fora de dev — o segredo e' a metade
     que transforma a rota de inerte em capaz de assinar.
  3. A remocao de qualquer uma das tres cercas do proprio `server.py`: o portao
     `SIMULAR_LIGADO`, a faixa `FAIXA_TESTE` ancorada nas duas pontas, e o uso dessas duas
     dentro de `_simular_whatsapp`. Uma cerca que pode ser apagada num refactor silencioso nao
     e' uma cerca.

Ela NAO tenta decidir se a rota deve existir. Essa decisao e' do dono e esta registrada no PR
que a introduziu; aqui so' se cobra o perimetro que aquela decisao assumiu.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVIDOR = REPO_ROOT / "src" / "maezo" / "platform" / "testchannel" / "server.py"
DEPLOY = REPO_ROOT / "deploy"

#: O UNICO ambiente onde a rota pode ser ligada. Um caminho, nao um padrao: um padrao como
#: `dev*` passaria a aceitar `dev-us-east-1` amanha sem ninguem decidir isso.
AMBIENTE_PERMITIDO = "dev-sa-east-1"

#: Nome do container do canal nas task definitions. A cerca 2 so' olha o segredo entregue a
#: ELE — o mesmo segredo no container do RECEPTOR e' legitimo em todo ambiente, porque lá ele
#: serve para VERIFICAR a assinatura da Meta, nao para produzir uma.
CONTAINER_CANAL = "canal-teste"

NOMES_EXIGIDOS = ("SIMULAR_LIGADO", "FAIXA_TESTE")


def _arquivos_deploy() -> list[Path]:
    return sorted(p for p in DEPLOY.rglob("*.tf") if p.is_file())


def _ambiente_de(caminho: Path) -> str:
    """Nome do diretorio de ambiente, ou "" quando o arquivo nao mora em `envs/<nome>/`."""
    partes = caminho.relative_to(REPO_ROOT).parts
    return partes[partes.index("envs") + 1] if "envs" in partes else ""


def checar_ligacao_fora_de_dev() -> list[str]:
    """Cerca 1: `CANAL_SIMULAR_RECEPTOR = "1"` so' em `dev-sa-east-1`."""
    achados: list[str] = []
    # Casa a forma como o Terraform declara env de container: um objeto com `name` e `value`
    # na mesma expressao. Tolerante a espaco e a ordem dos campos.
    padrao = re.compile(
        r'name\s*=\s*"CANAL_SIMULAR_RECEPTOR"\s*,\s*value\s*=\s*"([^"]*)"'
        r'|value\s*=\s*"([^"]*)"\s*,\s*name\s*=\s*"CANAL_SIMULAR_RECEPTOR"'
    )
    for arquivo in _arquivos_deploy():
        texto = arquivo.read_text(encoding="utf-8")
        if "CANAL_SIMULAR_RECEPTOR" not in texto:
            continue
        ambiente = _ambiente_de(arquivo)
        for casamento in padrao.finditer(texto):
            valor = casamento.group(1) if casamento.group(1) is not None else casamento.group(2)
            if valor != "1":
                continue  # desligada explicitamente: e' exatamente o que se quer ver fora de dev
            if ambiente != AMBIENTE_PERMITIDO:
                achados.append(
                    f"{arquivo.relative_to(REPO_ROOT)}: CANAL_SIMULAR_RECEPTOR=\"1\" no ambiente "
                    f"{ambiente or '<fora de envs/>'} — a rota que assina mensagem sintetica so' "
                    f"pode ser ligada em {AMBIENTE_PERMITIDO}."
                )
    return achados


def checar_segredo_no_canal() -> list[str]:
    """Cerca 2: `WHATSAPP_APP_SECRET` no container do canal so' em `dev-sa-east-1`."""
    achados: list[str] = []
    for arquivo in _arquivos_deploy():
        texto = arquivo.read_text(encoding="utf-8")
        if "WHATSAPP_APP_SECRET" not in texto or f'"{CONTAINER_CANAL}"' not in texto:
            continue
        ambiente = _ambiente_de(arquivo)
        if ambiente != AMBIENTE_PERMITIDO:
            achados.append(
                f"{arquivo.relative_to(REPO_ROOT)}: WHATSAPP_APP_SECRET entregue ao container "
                f"{CONTAINER_CANAL!r} no ambiente {ambiente or '<fora de envs/>'} — e' a metade "
                f"que torna a rota capaz de assinar."
            )
    return achados


def checar_cercas_no_codigo() -> list[str]:
    """Cerca 3: os portoes continuam existindo e continuam sendo USADOS na rota."""
    achados: list[str] = []
    if not SERVIDOR.is_file():
        return [f"{SERVIDOR.relative_to(REPO_ROOT)} nao existe — a cerca perdeu o alvo."]
    fonte = SERVIDOR.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    atribuidos = {
        alvo.id
        for no in ast.walk(arvore)
        if isinstance(no, ast.Assign)
        for alvo in no.targets
        if isinstance(alvo, ast.Name)
    }
    for nome in NOMES_EXIGIDOS:
        if nome not in atribuidos:
            achados.append(
                f"server.py: a constante {nome} desapareceu — era uma das tres cercas da rota."
            )

    # A faixa tem de estar ancorada nas DUAS pontas. Sem o `$`, `55119000000` seguido de um
    # numero real mais longo passaria; sem o `^`, qualquer sufixo passaria.
    faixa = re.search(r'FAIXA_TESTE\s*=\s*re\.compile\(\s*r?"([^"]*)"', fonte)
    if faixa and not (faixa.group(1).startswith("^") and faixa.group(1).endswith("$")):
        achados.append(
            f"server.py: FAIXA_TESTE={faixa.group(1)!r} nao esta ancorada em ^...$ — a cerca do "
            f"telefone deixa de valer para numero mais longo."
        )

    rota = next(
        (
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.FunctionDef) and no.name == "_simular_whatsapp"
        ),
        None,
    )
    if rota is None:
        # A rota sumir e' legitimo — e' a alternativa que o proprio dono ofereceu. O que nao
        # pode e' ela existir sem as cercas, entao aqui nao ha achado.
        return achados
    usados = {n.id for n in ast.walk(rota) if isinstance(n, ast.Name)}
    for nome in NOMES_EXIGIDOS:
        if nome in atribuidos and nome not in usados:
            achados.append(
                f"server.py: _simular_whatsapp nao usa mais {nome} — a constante existe mas a "
                f"cerca nao esta no caminho da requisicao."
            )
    return achados


def main() -> int:
    achados = checar_ligacao_fora_de_dev() + checar_segredo_no_canal() + checar_cercas_no_codigo()
    if achados:
        print("check_canal_simular: REPROVADO", file=sys.stderr)
        for a in achados:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print(
        "check_canal_simular: ok — rota ligada so' em "
        f"{AMBIENTE_PERMITIDO}, segredo so' la', e as tres cercas estao no caminho da requisicao."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
