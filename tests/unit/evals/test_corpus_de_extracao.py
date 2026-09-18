"""A cerca do corpus de extracao — o rotulo nao pode apodrecer nem mentir (Frente 3.2).

POR QUE UM CORPUS PRECISA DE CERCA. Um conjunto de casos rotulados e' uma AFIRMACAO sobre o que a
extracao deve fazer, e afirmacoes envelhecem em silencio. Tres formas, todas ja vistas neste
projeto: o vocabulario muda e o rotulo aponta para um codigo que nao existe mais; alguem acrescenta
um codigo a allowlist e nenhum caso o exercita; e a pior — um caso e' relaxado para fazer a medicao
passar, o que transforma a regua numa fita metrica que se estica.

O QUE ESTE ARQUIVO PROVA (tudo deterministico, sem modelo nenhum):
  1. todo valor rotulado esta no vocabulario FECHADO que o proprio `prompts.py` exporta — nao numa
     copia;
  2. cobertura: as quatro populacoes, os 25 codigos, as cinco intencoes, e as cinco regras da
     regua;
  3. o corpus tem tamanho minimo e nenhum caso duplicado;
  4. cada caso carrega o PORQUE — um rotulo sem justificativa e' um rotulo que ninguem consegue
     discutir com o medico que vai assina-lo;
  5. o corpus contem os casos MEDIDOS, nao so' os inventados — as baterias de 11 e 13/09.

O QUE ELE NAO PROVA: que os rotulos estao clinicamente certos. Isso e' assinatura humana, e o
`docs/design/regua-de-extracao.md` diz explicitamente quais casos estao marcados esperando por ela.
Uma cerca que afirmasse correcao clinica seria a mesma casca vazia que o projeto ja tem quatro.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Final

import pytest

from maezo.agents.helena.prompts import ALLOWED_SINTOMA_CODIGOS, SINTOMA_CODIGOS_BY_POPULATION

_CORPUS: Final[Path] = Path(__file__).resolve().parents[3] / "tests" / "evals" / "extracao" / "casos.json"

#: Minimo que o documento do diretor pede: "pelo menos cem mensagens escritas como gente escreve".
_MINIMO_DE_CASOS: Final[int] = 100

#: Os vocabularios fechados do grafo. Importados de `prompts.py` onde existem; os dois que o grafo
#: declara como `Literal` sao repetidos aqui e CONFERIDOS contra o modulo no teste abaixo, para que
#: uma copia nao possa divergir em silencio.
_INTENTS: Final[frozenset[str]] = frozenset(
    {"symptom", "information", "clinical_question", "human_request", "greeting", "scheduling"}
)
_POPULACOES: Final[frozenset[str]] = frozenset({"adult", "pediatric", "gestante", "mental_health", "none"})
_INTENSIDADES: Final[frozenset[str]] = frozenset({"leve", "moderada", "grave", "desconhecida"})
_REGRAS_DA_REGUA: Final[frozenset[str]] = frozenset({"R1", "R2", "R3", "R4", "R5"})

_CAMPOS_ESPERADOS: Final[frozenset[str]] = frozenset(
    {
        "intent",
        "population",
        "psychosocial_risk",
        "sintoma_codigo",
        "intensidade",
        "idade_anos",
        "idade_meses",
        "idade_gestacional_semanas",
    }
)


def _corpus() -> dict[str, Any]:
    carregado = json.loads(_CORPUS.read_text(encoding="utf-8"))
    assert isinstance(carregado, dict) and carregado.get("casos"), f"{_CORPUS} vazio ou malformado"
    return carregado


def _casos() -> list[dict[str, Any]]:
    return list(_corpus()["casos"])


# =================================================================================================
# 1. O rotulo vive no vocabulario fechado, e o vocabulario vem do modulo
# =================================================================================================


def test_os_vocabularios_deste_arquivo_batem_com_os_do_grafo() -> None:
    """A cerca da cerca.

    Os `Literal`s de `graph.py` nao sao importaveis como conjunto, entao este arquivo mantem uma
    copia — e uma copia que ninguem confere e' exatamente como a versao do prompt derivou por dois
    dias (Frente 6). Aqui a copia e' conferida contra a fonte a cada execucao.
    """
    from maezo.agents.helena import graph as helena_graph

    assert helena_graph._VALID_POPULATIONS == _POPULACOES
    assert helena_graph._VALID_INTENSIDADES == _INTENSIDADES
    assert helena_graph._VALID_INTENTS == _INTENTS


def test_todo_rotulo_usa_apenas_valores_do_vocabulario_fechado() -> None:
    """Um rotulo fora do vocabulario e' um caso que a extracao NUNCA pode acertar.

    Pior que inutil: ele puxa a medicao para baixo para sempre, e o reflexo de quem ve a nota cair
    e' relaxar o limiar — que e' como uma regua vira fita metrica.
    """
    problemas: list[str] = []
    for caso in _casos():
        e = caso["esperado"]
        cid = caso["id"]
        if set(e) != _CAMPOS_ESPERADOS:
            problemas.append(f"{cid}: campos {sorted(set(e) ^ _CAMPOS_ESPERADOS)} fora do esperado")
        if e["intent"] not in _INTENTS:
            problemas.append(f"{cid}: intent {e['intent']!r}")
        if e["population"] not in _POPULACOES:
            problemas.append(f"{cid}: population {e['population']!r}")
        if e["intensidade"] not in _INTENSIDADES:
            problemas.append(f"{cid}: intensidade {e['intensidade']!r}")
        if not isinstance(e["psychosocial_risk"], bool):
            problemas.append(f"{cid}: psychosocial_risk nao e' booleano")
        codigo = e["sintoma_codigo"]
        if codigo is not None and codigo not in ALLOWED_SINTOMA_CODIGOS:
            problemas.append(f"{cid}: sintoma_codigo {codigo!r} fora da allowlist")
        for campo in ("idade_anos", "idade_meses", "idade_gestacional_semanas"):
            valor = e[campo]
            if valor is not None and (isinstance(valor, bool) or not isinstance(valor, int) or valor < 0):
                problemas.append(f"{cid}: {campo}={valor!r} nao e' inteiro nao-negativo")
    assert not problemas, "rotulos fora do vocabulario:\n  " + "\n  ".join(problemas)


def test_o_codigo_rotulado_pertence_a_allowlist_daquela_populacao() -> None:
    """Codigo certo na populacao errada e' uma falha de classificacao, nao um acerto parcial.

    `convulsao` so' existe na allowlist PEDIATRICA; `sangramento_ativo` so' na de adulto. Um rotulo
    que cruze as duas ensinaria a extracao a produzir um codigo que a tabela daquela populacao nao
    casa — e o validador do grafo trata isso como `falha_tecnica`, nao como aproximacao.
    """
    violacoes: list[str] = []
    for caso in _casos():
        e = caso["esperado"]
        codigo, populacao = e["sintoma_codigo"], e["population"]
        if codigo is None or populacao == "none":
            continue
        permitidos = SINTOMA_CODIGOS_BY_POPULATION.get(populacao, ())
        if codigo not in permitidos:
            violacoes.append(f"{caso['id']}: {codigo!r} nao pertence a {populacao!r}")
    assert not violacoes, "codigo fora da allowlist da populacao:\n  " + "\n  ".join(violacoes)


# =================================================================================================
# 2. Cobertura — o corpus exercita o que existe
# =================================================================================================


def test_o_corpus_tem_o_tamanho_que_o_documento_pede() -> None:
    casos = _casos()
    assert len(casos) >= _MINIMO_DE_CASOS, (
        f"{len(casos)} casos — o documento pede pelo menos {_MINIMO_DE_CASOS}. Um corpus pequeno "
        f"da uma nota que oscila com um caso, e uma nota que oscila nao barra entrega nenhuma."
    )


def test_todo_codigo_da_allowlist_tem_pelo_menos_um_caso() -> None:
    """A direcao que pega o codigo NOVO.

    Acrescentar um codigo a allowlist e' mexer no que o modelo pode produzir. Sem um caso, ele
    entra sem nunca ter sido medido — e a nota agregada continua verde porque ele simplesmente nao
    aparece.
    """
    rotulados = {c["esperado"]["sintoma_codigo"] for c in _casos()} - {None}
    faltando = set(ALLOWED_SINTOMA_CODIGOS) - rotulados
    assert not faltando, (
        f"codigo(s) da allowlist sem nenhum caso no corpus: {sorted(faltando)}. "
        f"Um codigo que nenhum caso exercita e' um codigo cuja traducao ninguem mediu."
    )


def test_as_quatro_populacoes_e_as_cinco_intencoes_aparecem() -> None:
    populacoes = {c["esperado"]["population"] for c in _casos()}
    intents = {c["esperado"]["intent"] for c in _casos()}
    assert populacoes >= _POPULACOES, f"populacao sem caso: {sorted(_POPULACOES - populacoes)}"
    assert intents >= _INTENTS, f"intencao sem caso: {sorted(_INTENTS - intents)}"


def test_as_quatro_intensidades_aparecem() -> None:
    """Inclui `desconhecida`, que e' a que a regra R1 produz — e a mais facil de ninguem medir.

    Um corpus so' com intensidades ditas mediria a leitura e nunca a ABSTENCAO, que e' justamente o
    comportamento que a R1 cobra.
    """
    intensidades = {c["esperado"]["intensidade"] for c in _casos()}
    assert intensidades >= _INTENSIDADES, f"sem caso: {sorted(_INTENSIDADES - intensidades)}"


def test_cada_regra_da_regua_tem_casos_que_a_exercitam() -> None:
    """A regua tem cinco regras; um corpus que so' exercita tres mede tres.

    O minimo de tres por regra existe para que uma regra nao fique pendurada num unico exemplo —
    com um caso so', a nota daquela regra e' 0% ou 100%, e nenhum dos dois informa nada.
    """
    contagem = Counter(regra for caso in _casos() for regra in caso["regras"])
    desconhecidas = set(contagem) - _REGRAS_DA_REGUA
    assert not desconhecidas, f"regra citada e inexistente na regua: {sorted(desconhecidas)}"
    magras = {regra: contagem.get(regra, 0) for regra in _REGRAS_DA_REGUA if contagem.get(regra, 0) < 3}
    assert not magras, f"regra(s) com menos de 3 casos: {magras}"


def test_o_corpus_carrega_os_casos_realmente_medidos() -> None:
    """Casos inventados cobrem o vocabulario; casos MEDIDOS cobrem a realidade.

    As baterias de 11 e 13/09 sao as unicas mensagens deste corpus que uma pessoa de verdade
    escreveu contra o sistema de verdade. Um corpus 100% escrito no escritorio mede o que os
    autores imaginam que as pessoas escrevem.
    """
    origens = Counter(c["origem"] for c in _casos())
    medidos = sum(n for origem, n in origens.items() if origem.startswith("bateria"))
    assert medidos >= 8, f"so' {medidos} casos vindos de bateria real (origens: {dict(origens)})"


def test_o_defeito_de_13_09_esta_no_corpus_nos_dois_sentidos() -> None:
    """A prova de nao-vacuidade do corpus inteiro.

    O defeito que motivou a frente foi "dor de cabeca" virando `cefaleia_subita_intensa`. O corpus
    tem de conter o caso que NAO pode casar e o que DEVE casar — so' o primeiro ensinaria a nunca
    produzir o codigo, o que trocaria um falso positivo por um falso negativo numa emergencia real.
    """
    por_id = {c["id"]: c for c in _casos()}
    negativo = por_id.get("r2-cefaleia-comum-01")
    positivo = por_id.get("r2-cefaleia-subita-01")
    assert negativo is not None and positivo is not None, "os dois casos-espelho sumiram do corpus"
    assert negativo["esperado"]["sintoma_codigo"] is None
    assert positivo["esperado"]["sintoma_codigo"] == "cefaleia_subita_intensa"


# =================================================================================================
# 3. Higiene — duplicata e justificativa
# =================================================================================================


def test_nenhum_caso_duplicado() -> None:
    """Duplicata pesa duas vezes na media sem medir nada a mais."""
    ids = Counter(c["id"] for c in _casos())
    mensagens = Counter(c["mensagem"].strip().lower() for c in _casos())
    assert not [i for i, n in ids.items() if n > 1], f"id repetido: {[i for i, n in ids.items() if n > 1]}"
    repetidas = [m for m, n in mensagens.items() if n > 1]
    assert not repetidas, f"mensagem repetida: {repetidas}"


@pytest.mark.parametrize("campo", ["id", "mensagem", "porque", "origem"])
def test_todo_caso_carrega_os_campos_obrigatorios(campo: str) -> None:
    """`porque` e' obrigatorio, e nao e' burocracia.

    Este corpus vai ser lido por quem assina a regua. Um rotulo sem justificativa nao pode ser
    discutido — so' aceito ou recusado —, e o que se quer da assinatura e' exatamente a discussao
    dos casos ambiguos.
    """
    vazios = [c.get("id", "<sem id>") for c in _casos() if not str(c.get(campo, "")).strip()]
    assert not vazios, f"caso(s) sem `{campo}`: {vazios}"


def test_as_mensagens_sao_texto_de_gente_e_nao_gabarito() -> None:
    """Uma checagem grosseira, mas que pega o modo de falha real.

    O jeito facil de encher um corpus e' escrever a mensagem a partir do rotulo — "dor toracica
    grave" para rotular `dor_toracica`/`grave`. Isso mede se o modelo copia, nao se ele traduz. O
    codigo rotulado nao pode aparecer LITERALMENTE na mensagem.
    """
    vazamentos: list[str] = []
    for caso in _casos():
        codigo = caso["esperado"]["sintoma_codigo"]
        # SO' OS CODIGOS COMPOSTOS SAO COBRADOS, e a distincao importa: `febre` e `convulsao` sao
        # os termos que uma pessoa de verdade escreve, entao exigir que a mensagem os evite
        # produziria um corpus artificial — o oposto do que esta cerca quer. Ja
        # `cefaleia_subita_intensa` ou `sinais_desidratacao` sao formulacoes CLINICAS: ninguem manda
        # "estou com sinais desidratacao" no WhatsApp, e ve-las numa mensagem e' sinal de rotulo
        # escrito ao contrario.
        if not codigo or "_" not in codigo:
            continue
        if codigo.replace("_", " ") in caso["mensagem"].lower():
            vazamentos.append(f"{caso['id']}: a mensagem contem o proprio codigo {codigo!r}")
    assert not vazamentos, "mensagem escrita a partir do rotulo:\n  " + "\n  ".join(vazamentos)
