#!/usr/bin/env python3
"""CI gate: a cadencia de revisao das reguas nao pode vencer em silencio (Frente 8.3).

POR QUE ESTE ARQUIVO EXISTE
---------------------------
`HELENA_EM_PRODUCAO_O_QUE_FALTA.md`, passo 8.3: *"Marcar cadencia: a regua de extracao e as regras
clinicas sao revistas a cada N meses ou a cada N conversas, o que vier primeiro. **Registrar a
cadencia, nao confiar na memoria de ninguem.**"*

Uma cadencia que vive so' num documento e' uma cadencia que ninguem honra. Este projeto ja tem
quatro cascas vazias nomeadas no proprio documento do diretor — a jornada com estados que nao
existem, o porto sem implementacao, a memoria episodica declarada em 11 agentes e usada em zero, a
tabela de suficiencia so' em documento. "Revisamos a cada seis meses" e' o formato exato de uma
quinta.

Entao a cadencia vive em `spec/revisao-das-reguas.yaml`, com DATA, e esta cerca deixa a esteira
vermelha no dia em que a data passa. Mesma disciplina de `check_deviation_expiry.py` e
`check_lifecycle_expected_fail_expiry.py`, que ja existem neste repo pelo mesmo motivo.

NAO HA RENOVACAO SILENCIOSA. A saida e' um PR revisado que ou registra a revisao feita (nova data,
`ultima_revisao` atualizada) ou declara por que ela nao aconteceu. O PR de renovacao e' ele mesmo
verde, porque a cerca le' a arvore sob teste.

O QUE ESTA CERCA VERIFICA
-------------------------
  1. toda regua declarada tem `revisar_ate` no futuro;
  2. os arquivos que cada regua nomeia (`documento`, `corpus`) EXISTEM na arvore — uma cadencia
     apontando para um documento apagado e' pior que nenhuma: ela passa verde sobre o vazio;
  3. o arquivo declara pelo menos as duas reguas que o documento nomeia (extracao e regras
     clinicas), para que apagar uma linha nao seja o jeito mais facil de calar a cerca.

O QUE ELA NAO VERIFICA, e e' deliberado: se a revisao foi BOA. Isso e' leitura humana, e uma cerca
que afirmasse qualidade de revisao clinica seria exatamente a casca vazia que ela existe para
evitar. Ela verifica que a pergunta "ja passou da hora?" tem uma resposta mecanica.

A CONDICAO DE VOLUME (`ou_apos_conversas`) NAO E' CHECADA AQUI, e o motivo e' honesto: o contador
de conversas vive no workspace de metricas (Frente 5), que este script nao alcanca — e inventar
uma leitura seria pior que declarar o limite. Ela esta registrada no YAML para a revisao humana
conferir, e vira cerca no dia em que o coletor estiver no ar com historico.

Uso:
    python scripts/ci/check_revisao_das_reguas.py
    python scripts/ci/check_revisao_das_reguas.py --hoje 2027-01-01   # simular o vencimento
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_CADENCIA: Final[Path] = _REPO_ROOT / "spec" / "revisao-das-reguas.yaml"

#: As reguas que o documento do diretor nomeia. Apagar uma linha do YAML nao pode ser o jeito mais
#: barato de calar a cerca — e' o modo de falha que toda allowlist tem quando ninguem fixa o piso.
_REGUAS_OBRIGATORIAS: Final[frozenset[str]] = frozenset({"extracao", "regras-clinicas"})


def carregar() -> list[dict[str, Any]]:
    if not _CADENCIA.exists():
        raise SystemExit(f"ERRO: {_CADENCIA.relative_to(_REPO_ROOT)} nao existe.")
    dados = yaml.safe_load(_CADENCIA.read_text(encoding="utf-8")) or {}
    reguas = dados.get("reguas")
    if not isinstance(reguas, list) or not reguas:
        raise SystemExit(f"ERRO: {_CADENCIA.relative_to(_REPO_ROOT)} nao declara `reguas`.")
    return reguas


def verificar(reguas: list[dict[str, Any]], *, hoje: date) -> list[str]:
    achados: list[str] = []

    declaradas = {str(r.get("id", "")) for r in reguas}
    faltando = _REGUAS_OBRIGATORIAS - declaradas
    if faltando:
        achados.append(
            f"regua(s) obrigatoria(s) ausente(s) do arquivo: {sorted(faltando)} — apagar a linha "
            f"nao e' uma forma de encerrar a cadencia; encerra-la e' uma decisao registrada."
        )

    for regua in reguas:
        rid = regua.get("id", "<sem id>")

        bruto = regua.get("revisar_ate")
        if not bruto:
            achados.append(f"{rid}: sem `revisar_ate` — cadencia sem data nao e' cadencia.")
        else:
            try:
                prazo = date.fromisoformat(str(bruto))
            except ValueError:
                achados.append(f"{rid}: `revisar_ate={bruto!r}` nao e' uma data ISO (AAAA-MM-DD).")
            else:
                if prazo < hoje:
                    achados.append(
                        f"{rid}: a revisao venceu em {prazo.isoformat()} (hoje e' {hoje.isoformat()}). "
                        f"O caminho e' um PR que registre a revisao feita — atualizando "
                        f"`ultima_revisao` e `revisar_ate` — ou que declare por que ela nao "
                        f"aconteceu. Nunca renovar a data sem a revisao: a data existe para "
                        f"provocar a leitura, e renova-la sozinha e' a normalizacao do alarme."
                    )

        for campo in ("documento", "corpus"):
            caminho = regua.get(campo)
            if not caminho:
                achados.append(f"{rid}: sem `{campo}`.")
                continue
            if not (_REPO_ROOT / str(caminho)).exists():
                achados.append(
                    f"{rid}: `{campo}` aponta para {caminho!r}, que NAO existe na arvore. Uma "
                    f"cadencia sobre um documento apagado passa verde sobre o vazio."
                )

        if not str(regua.get("dono", "")).strip():
            achados.append(f"{rid}: sem `dono` — revisao sem dono e' revisao que nao acontece.")

    return achados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frente 8.3: a cadencia de revisao das reguas.")
    parser.add_argument(
        "--hoje",
        type=date.fromisoformat,
        default=date.today(),
        help="data de referencia (AAAA-MM-DD), para exercitar o vencimento em teste",
    )
    args = parser.parse_args(argv)

    reguas = carregar()
    achados = verificar(reguas, hoje=args.hoje)
    if achados:
        print("check_revisao_das_reguas: cadencia de revisao com pendencia(s):", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1

    print(f"check_revisao_das_reguas: {len(reguas)} regua(s), nenhuma vencida. OK.")
    for regua in reguas:
        print(f"  {regua['id']:20s} revisar ate {regua['revisar_ate']}  (dono: {regua['dono']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
