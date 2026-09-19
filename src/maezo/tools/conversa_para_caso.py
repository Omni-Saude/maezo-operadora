"""Transforma uma conversa real em caso de teste — Frente 8.1.

O DEFEITO QUE ISTO FECHA. Nao havia caminho nenhum pelo qual uma conversa do ambiente virasse caso
de teste. Todo defeito das duas semanas de setembro foi achado por alguem testando a mao, lendo a
tela — o bebe triado como adulto, a promessa de contato humano com zero processos abertos, a
negativa clinica, o P1 aberto por dor de cabeca comum. Cada um deles levou horas de leitura humana
e, depois de corrigido, levou mais trabalho manual para virar teste.

O documento e' explicito sobre a forma: **UM COMANDO, nao um procedimento manual** — senao ninguem
faz. Entao:

    python -m maezo.tools.conversa_para_caso transcricao.json --saida casos-novos.json

A DECISAO MAIS IMPORTANTE DESTE MODULO, e ela e' contraintuitiva:

    O ROTULO GERADO NASCE VAZIO, NUNCA COM O QUE O MODELO DISSE.

Parece desperdicio — a extracao observada esta' ali, bastaria copia-la. Mas a extracao observada e'
EXATAMENTE O QUE PODE ESTAR ERRADO. Se este comando rotulasse o caso com a saida de producao, o
corpus passaria a afirmar que "dor de cabeca" E' `cefaleia_subita_intensa`, porque foi isso que o
modelo fez naquele dia — e a medicao daria nota alta para o defeito. Um conjunto de testes gerado a
partir do comportamento observado nao mede correcao: mede estabilidade, e abencoa o erro no dia em
que ele acontece.

O que o comando faz e' o trabalho CHATO (extrair, pseudonimizar, formatar, deduplicar contra o
corpus existente) e deixa para a pessoa o trabalho que so' ela pode fazer: dizer o que era certo.
A extracao observada vai junto, num campo separado (`observado`), porque a diferenca entre ela e o
rotulo e' o que se quer discutir com quem assina a regua.

PSEUDONIMIZACAO. O texto do beneficiario e' PHI. Passa pelo `redact_free_text`, a MESMA rede que
protege o `resumo_contexto` no start de processo — e nao uma copia, porque duas redes divergem e a
que fica para tras e' sempre a menos usada.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Final

from maezo.tools.workers.phi_vars import redact_free_text

#: O corpus contra o qual se deduplica. Um caso que ja existe nao e' um caso novo — e o modo de
#: falha aqui e' silencioso: sem esta checagem, rodar o comando duas vezes sobre a mesma bateria
#: dobraria o peso daquelas mensagens na nota sem ninguem perceber.
CORPUS_PADRAO: Final[Path] = Path("tests/evals/extracao/casos.json")

#: O rotulo NASCE VAZIO — ver o docstring do modulo. `null` aqui significa "ninguem decidiu ainda",
#: e e' diferente de um `null` decidido (que e' o que a regra R2 manda em caso ambiguo). Por isso o
#: caso gerado carrega `precisa_de_rotulo: true` ate' alguem editar.
_ESQUELETO_DO_ROTULO: Final[dict[str, Any]] = {
    "intent": None,
    "population": None,
    "psychosocial_risk": None,
    "sintoma_codigo": None,
    "intensidade": None,
    "idade_anos": None,
    "idade_meses": None,
    "idade_gestacional_semanas": None,
}

_CAMPOS_OBSERVAVEIS: Final[tuple[str, ...]] = tuple(_ESQUELETO_DO_ROTULO)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _identificador(mensagem: str, prefixo: str) -> str:
    """Um id legivel derivado da propria mensagem.

    Legivel, e nao um hash, porque quem abre o corpus para rotular precisa saber do que o caso
    trata sem ler o JSON inteiro. O sufixo numerico resolve colisao.
    """
    base = _sem_acento(mensagem.lower())
    palavras = [p for p in re.split(r"[^a-z0-9]+", base) if len(p) > 2][:4]
    return f"{prefixo}-{'-'.join(palavras) or 'sem-texto'}"[:60]


def _normalizar(texto: str) -> str:
    """A chave de deduplicacao: sem acento, sem caixa, sem pontuacao, sem espaco duplo.

    Duas transcricoes da mesma conversa quase nunca sao byte a byte iguais — uma veio do log, outra
    da tela. Deduplicar por igualdade exata deixaria passar a duplicata que importa.
    """
    base = _sem_acento(texto.lower())
    return " ".join(re.split(r"[^a-z0-9]+", base)).strip()


def carregar_transcricao(caminho: Path) -> list[dict[str, Any]]:
    """Le uma transcricao e devolve os TURNOS DO BENEFICIARIO.

    Aceita as duas formas que este repo produz de verdade, para nao obrigar ninguem a converter
    nada a mao antes de usar o comando:
      * uma lista de turnos `[{"mensagem": ..., "resposta": ..., ...}]`;
      * o eco da rota `/receptor/simular` (`{"resposta": ..., "conversation_id": ...}`), um por
        item ou dentro de `{"turnos": [...]}`.
    """
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    itens = bruto.get("turnos", bruto) if isinstance(bruto, dict) else bruto
    if not isinstance(itens, list):
        raise SystemExit(f"{caminho}: esperava uma lista de turnos (ou {{'turnos': [...]}})")

    turnos: list[dict[str, Any]] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        # O texto do BENEFICIARIO, sob os nomes que as duas formas usam. `resposta`/`response_text`
        # e' o que a Helena disse e NAO entra como mensagem: rotular a propria saida do sistema
        # seria medir o modelo contra ele mesmo.
        mensagem = item.get("mensagem") or item.get("message_body") or item.get("texto")
        if not mensagem or not str(mensagem).strip():
            continue
        turnos.append({"mensagem": str(mensagem).strip(), "item": item})
    return turnos


def observado_de(item: dict[str, Any]) -> dict[str, Any]:
    """A extracao que o sistema PRODUZIU naquele turno, quando a transcricao a carrega.

    Vai para um campo proprio (`observado`), nunca para o rotulo. A diferenca entre os dois e' a
    conversa que se quer ter com quem assina a regua — e igualar os dois apagaria justamente essa
    diferenca.
    """
    return {campo: item.get(campo) for campo in _CAMPOS_OBSERVAVEIS if campo in item}


def gerar_casos(
    turnos: list[dict[str, Any]], *, origem: str, ja_existentes: set[str]
) -> tuple[list[dict[str, Any]], int]:
    """Os casos novos, mais quantos foram descartados por ja existirem no corpus."""
    casos: list[dict[str, Any]] = []
    vistos = set(ja_existentes)
    descartados = 0

    for indice, turno in enumerate(turnos, start=1):
        # PSEUDONIMIZACAO ANTES DE QUALQUER OUTRA COISA: o texto cru do beneficiario nao chega nem
        # ao id, nem ao arquivo de saida, nem a memoria por mais tempo que o necessario.
        mensagem = redact_free_text(turno["mensagem"])
        chave = _normalizar(mensagem)
        if not chave or chave in vistos:
            descartados += 1
            continue
        vistos.add(chave)

        observado = observado_de(turno["item"])
        casos.append(
            {
                "id": f"{_identificador(mensagem, origem)}-{indice:02d}",
                "mensagem": mensagem,
                "esperado": dict(_ESQUELETO_DO_ROTULO),
                # O CAMPO QUE IMPEDE ESTE CASO DE ENTRAR NA MEDICAO SEM ROTULO HUMANO. A cerca do
                # corpus recusa `esperado` com `null` fora do vocabulario, entao um caso gerado e
                # nao rotulado deixa a suite VERMELHA em vez de entrar mudo.
                "precisa_de_rotulo": True,
                "observado": observado,
                "porque": (
                    "GERADO de conversa real por `maezo.tools.conversa_para_caso`. O rotulo nasce "
                    "VAZIO de proposito: a extracao observada e' o que pode estar errado, e "
                    "copia-la faria o corpus abencoar o defeito. Preencha `esperado` segundo a "
                    "regua (docs/design/regua-de-extracao.md), compare com `observado`, e "
                    "descreva aqui a decisao."
                ),
                "origem": origem,
                "regras": [],
            }
        )
    return casos, descartados


def chaves_do_corpus(caminho: Path) -> set[str]:
    if not caminho.exists():
        return set()
    corpus = json.loads(caminho.read_text(encoding="utf-8"))
    return {_normalizar(caso["mensagem"]) for caso in corpus.get("casos", [])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maezo.tools.conversa_para_caso",
        description=(
            "Transforma uma conversa real em casos de teste de extracao, pseudonimizados e SEM "
            "rotulo — o rotulo e' trabalho humano (Frente 8.1)."
        ),
    )
    parser.add_argument("transcricao", type=Path, help="JSON com os turnos da conversa")
    parser.add_argument(
        "--origem",
        default="conversa-real",
        help="prefixo do id e valor do campo `origem` (ex.: `bateria-20-09`)",
    )
    parser.add_argument("--saida", type=Path, help="arquivo de saida (default: stdout)")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS_PADRAO,
        help=f"corpus contra o qual deduplicar (default: {CORPUS_PADRAO})",
    )
    args = parser.parse_args(argv)

    turnos = carregar_transcricao(args.transcricao)
    if not turnos:
        print(f"{args.transcricao}: nenhum turno de beneficiario encontrado", file=sys.stderr)
        return 1

    casos, descartados = gerar_casos(turnos, origem=args.origem, ja_existentes=chaves_do_corpus(args.corpus))
    saida = json.dumps({"casos": casos}, ensure_ascii=False, indent=2) + "\n"
    if args.saida:
        args.saida.write_text(saida, encoding="utf-8", newline="\n")
        print(
            f"{len(casos)} caso(s) novo(s) em {args.saida}"
            f"{f' ({descartados} ja existiam no corpus)' if descartados else ''}.\n"
            f"PROXIMO PASSO, e ele e' humano: preencha `esperado` em cada caso segundo "
            f"docs/design/regua-de-extracao.md, compare com `observado`, apague "
            f"`precisa_de_rotulo`, e so entao mova os casos para {args.corpus}.",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(saida)
    return 0


if __name__ == "__main__":  # pragma: no cover - entrada de CLI
    raise SystemExit(main())
