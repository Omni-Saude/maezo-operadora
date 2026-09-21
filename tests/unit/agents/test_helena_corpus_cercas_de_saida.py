"""A CERCA DE NAO-REGRESSAO das tres cercas de saida da Helena (21/09/2026, terceira rodada).

POR QUE ESTE ARQUIVO EXISTE, e o que ele conserta no METODO e nao no codigo.

O achado CRITICO 2 da terceira rodada de review foi uma TROCA SILENCIOSA DE VEREDITO: a rodada 2
relaxou `motivo_de_canal_nao_confirmado` (a referencia de volta) para consertar um falso positivo
real, e no mesmo movimento passou a APROVAR um nome de canal INVENTADO desde que o texto citasse
qualquer canal confirmado em outro lugar. Toda a suite continuou verde, porque nenhum teste fixava
o veredito daquela FAMILIA de textos — os testes fixavam os textos que a rodada 2 tinha em maos.

E' o mesmo defeito de metodo pela terceira vez: a cerca muda, e o que se mede e' o caso novo.

O QUE ESTE ARQUIVO FAZ: fixa, num arquivo VERSIONADO (`corpus_cercas_de_saida.json`), o veredito
das tres cercas para TODOS os textos medidos nas tres rodadas — a bateria do diretor, os
adversariais da rodada 2, os textos do reviewer na rodada 3 e as constantes que vao literalmente
ao beneficiario. O corpus roda inteiro, texto a texto.

A CONSEQUENCIA E' O PONTO: mudar o veredito de um texto passa a exigir EDITAR O CORPUS. Nao ha
como afrouxar uma cerca "de passagem" — a mudanca aparece no diff como uma declaracao caso a caso,
com o `origem` de cada texto do lado, que e' o que faltou nas duas rodadas anteriores. Uma troca de
veredito DELIBERADA continua possivel (e ha' uma nesta rodada: "o portal mostra a mesma lista",
declarada em `test_helena_canais_confirmados.py`); o que deixa de ser possivel e' a troca que
ninguem viu.

O QUE ELE NAO E': nao e' substituto dos arquivos por assunto. Eles explicam POR QUE cada veredito
e' aquele; este so' garante que nenhum deles muda sem que alguem diga que sim.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena import prompts as prompts_module
from maezo.agents.helena.graph import _PERGUNTA_FALLBACK
from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)

_CORPUS = Path(__file__).parent / "corpus_cercas_de_saida.json"
_MODULOS = (graph_module, prompts_module)

#: O minimo declarado. Nao e' uma meta de cobertura: e' o tamanho abaixo do qual o corpus deixaria
#: de conter os textos das quatro rodadas, e portanto deixaria de ser uma cerca de nao-regressao.
TAMANHO_MINIMO = 100

#: Os PREFIXOS de nome que a varredura por reflexao considera "frase que vai ao beneficiario"
#: (21/09/2026, QUARTA RODADA — eram so' `RESPOSTA_`). A cerca por prefixo pega a constante NOVA no
#: minuto em que ela nasce, e um nome fora do prefixo era o buraco obvio: quem escrevesse
#: `MENSAGEM_DE_ESPERA` ou `FRASE_DE_DESCULPA` ficava fora da cerca sem que nada apitasse. Quatro
#: prefixos plausiveis custam nada e nao dependem de ninguem lembrar desta convencao — quem
#: batizar diferente cai na varredura por AST logo abaixo.
PREFIXOS_DE_FRASE = ("RESPOSTA_", "MENSAGEM_", "FRASE_", "TEXTO_")

#: A NAO-VACUIDADE da varredura por AST. Nao e' uma lista de completude — e' um PISO: estas cinco
#: constantes chegam ao beneficiario hoje, e a varredura tem de continuar VENDO as cinco. Se alguem
#: renomear uma delas, este teste fica vermelho e a decisao e' consciente (editar o piso); se a
#: varredura quebrar (um `walk` a menos, uma regra estreitada), ela para de ver as cinco e o teste
#: pega — que e' exatamente o modo de falha de uma cerca de reflexao: ficar verde por nao achar
#: nada.
CONSTANTES_QUE_CHEGAM_AO_BENEFICIARIO = frozenset(
    {
        "RESPOSTA_FALHA_DE_REDACAO",
        "RESPOSTA_FALHA_TECNICA_START",
        "RESPOSTA_HANDOFF_JA_ABERTO",
        "RESPOSTA_HANDOFF_RECUSADA",
        "RESPOSTA_SEM_ENCAMINHAMENTO",
    }
)

#: Os nomes de variavel LOCAL que sao o texto a enviar nos dois modulos. `respond` monta `text`,
#: `_texto_bate_com_o_fato` monta `enviar` e `collect`/`_respond_llm` montam `texto` — atribuir uma
#: constante a um deles e' declarar que ela pode sair.
LOCAIS_QUE_VIRAM_TEXTO_ENVIADO = frozenset({"text", "texto", "enviar"})


def _entradas() -> list[dict[str, Any]]:
    dados = json.loads(_CORPUS.read_text(encoding="utf-8"))
    return list(dados["entradas"])


ENTRADAS = _entradas()


def _id(entrada: dict[str, Any]) -> str:
    return f"{entrada['rota']}/{entrada['start_aconteceu']}/{entrada['texto'][:60]}"


def test_o_corpus_tem_os_textos_das_tres_rodadas() -> None:
    """Nao-vacuidade: um corpus vazio (ou esvaziado) tornaria todo este arquivo verde por nada.

    A contagem por ORIGEM entra na assercao porque as tres rodadas tem de estar representadas —
    um corpus com so' os textos da rodada 3 seria exatamente o defeito de metodo que ele existe
    para consertar.
    """
    assert len(ENTRADAS) >= TAMANHO_MINIMO, f"o corpus encolheu para {len(ENTRADAS)} entradas"

    origens = [str(e["origem"]) for e in ENTRADAS]
    assert any("bateria do diretor" in o for o in origens), "nenhum texto da bateria do diretor"
    assert any("rodada 2" in o for o in origens), "nenhum texto dos adversariais da rodada 2"
    assert any("rodada 3" in o for o in origens), "nenhum texto medido pelo reviewer na rodada 3"
    assert any("rodada 4" in o for o in origens), "nenhum texto medido pelo reviewer na rodada 4"
    assert any("constante" in o for o in origens), "nenhuma constante enviada ao beneficiario"


def test_cada_entrada_do_corpus_e_unica_e_declarada() -> None:
    """Entrada duplicada esconde uma troca de veredito: a segunda copia fica verde pela primeira.

    E `origem` e' OBRIGATORIA — um texto sem procedencia no corpus e' um texto que ninguem sabe
    por que esta' la', e daqui a duas rodadas ele e' apagado por parecer ruido.
    """
    chaves = [(e["texto"], e["rota"], e["start_aconteceu"]) for e in ENTRADAS]
    duplicadas = {c for c in chaves if chaves.count(c) > 1}

    assert not duplicadas, f"entradas duplicadas no corpus: {sorted(str(d) for d in duplicadas)}"

    sem_origem = [e["texto"][:60] for e in ENTRADAS if not str(e.get("origem", "")).strip()]
    assert not sem_origem, f"entradas sem `origem` declarada: {sem_origem}"


@pytest.mark.parametrize("entrada", ENTRADAS, ids=_id)
def test_o_veredito_das_tres_cercas_nao_mudou(entrada: dict[str, Any]) -> None:
    """O corpus inteiro, texto a texto, contra as tres cercas REAIS.

    As tres respostas sao independentes e as tres importam:

      * `motivo_de_recusa` — o texto pode sair nesta rota, com este fato de start?
      * `motivo_de_canal_nao_confirmado` — ele manda alguem para um lugar que existe?
      * `menciona_encaminhamento` — ele AVISA que um humano assumiu? (e' o gatilho dos dois lados
        da cerca TEXTO x FATO, entao um veredito trocado aqui muda dois comportamentos)

    A mensagem de falha carrega o `origem` de proposito: quem quebrar isto precisa saber QUAL
    medicao esta' contradizendo antes de decidir editar o corpus.
    """
    texto = str(entrada["texto"])
    rota = str(entrada["rota"])
    start = entrada["start_aconteceu"]
    esperado = entrada["esperado"]
    contexto = f"origem={entrada['origem']!r} texto={texto[:80]!r}"

    recusa = motivo_de_recusa(texto, rota, start_aconteceu=start)
    grupo_recusa = recusa[0] if recusa is not None else None
    assert grupo_recusa == esperado["recusa"], f"`motivo_de_recusa` mudou de veredito — {contexto}"

    canal = motivo_de_canal_nao_confirmado(texto)
    grupo_canal = canal[0] if canal is not None else None
    assert grupo_canal == esperado["canal"], (
        f"`motivo_de_canal_nao_confirmado` mudou de veredito — {contexto}"
    )

    assert menciona_encaminhamento(texto) is esperado["menciona"], (
        f"`menciona_encaminhamento` mudou de veredito — {contexto}"
    )


def test_toda_constante_enviada_ao_beneficiario_esta_no_corpus() -> None:
    """A DIRECAO QUE PEGA A CONSTANTE NOVA.

    Uma frase que o repositorio envia LITERALMENTE a um beneficiario e' a ultima que pode estar
    fora do corpus: ela nao passa por modelo nenhum, entao um veredito errado nela e' 100% dos
    turnos daquele caminho — e foi assim que a frase de conformidade ditada pelo dono quase saiu
    casando a propria cerca de capacidade (21/09/2026, primeira rodada).

    A varredura e' por REFLEXAO (os prefixos de `PREFIXOS_DE_FRASE` nos dois modulos + as
    perguntas de fallback da coleta), e nao por uma lista a mao, porque uma lista a mao envelhece
    exatamente como o comentario que ela substituiria.

    O PREFIXO ERA SO' `RESPOSTA_` ATE' 21/09/2026 (QUARTA RODADA), e o buraco era obvio de
    enunciar: uma constante batizada `MENSAGEM_*`, `FRASE_*` ou `TEXTO_*` ficava fora da cerca sem
    que nada apitasse. Quatro prefixos nao resolvem quem batiza de outro jeito — para esse caso
    existe a varredura por AST do teste seguinte, que nao depende de convencao de nome nenhuma.
    """
    textos_no_corpus = {str(e["texto"]) for e in ENTRADAS}

    constantes: dict[str, str] = {}
    for modulo in _MODULOS:
        for nome, valor in vars(modulo).items():
            if nome.startswith(PREFIXOS_DE_FRASE) and isinstance(valor, str):
                constantes[f"{modulo.__name__}.{nome}"] = valor
    for chave, pergunta in _PERGUNTA_FALLBACK.items():
        constantes[f"_PERGUNTA_FALLBACK[{chave!r}]"] = pergunta

    assert constantes, "a reflexao nao achou constante nenhuma — a cerca ficaria vacua"

    faltando = sorted(nome for nome, valor in constantes.items() if valor not in textos_no_corpus)
    assert not faltando, (
        "constante enviada ao beneficiario e AUSENTE do corpus de nao-regressao "
        f"(tests/unit/agents/corpus_cercas_de_saida.json): {faltando}"
    )


def _constantes_str_de_modulo(arvore: ast.Module) -> dict[str, str]:
    """Toda constante `str` declarada no NIVEL DE MODULO, com ou sem anotacao de tipo."""
    constantes: dict[str, str] = {}
    for no in arvore.body:
        alvo: str | None = None
        if isinstance(no, ast.Assign) and len(no.targets) == 1 and isinstance(no.targets[0], ast.Name):
            alvo = no.targets[0].id
        elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
            alvo = no.target.id
        valor = getattr(no, "value", None)
        if alvo is not None and isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            constantes[alvo] = valor.value
    return constantes


def _constantes_que_o_codigo_envia(caminho: Path) -> dict[str, str]:
    """As constantes `str` de modulo que ALCANCAM o beneficiario, achadas no proprio codigo.

    QUATRO REGRAS, e cada uma e' um passo real do caminho do texto neste grafo:

      1. argumento de um `send`/`send_message` — o proprio envio;
      2. valor de `response_text` (chave de dict, subscript ou kwarg) — o rascunho que `respond`
        le' do estado;
      3. atribuicao a uma local de `LOCAIS_QUE_VIRAM_TEXTO_ENVIADO` (`text`, `texto`, `enviar`);
      4. `return` de um METODO anotado `-> str` ou `-> tuple[str, ...]`, olhando o PRIMEIRO
         elemento da tupla — que e' `_texto_bate_com_o_fato`, a cerca que substitui o rascunho.

    A regra 4 e' restrita a METODO com essa anotacao de proposito: `return` solto pegava os rotulos
    de grupo (`RECUSA_*`), as chaves de processo e os tokens de erro, que sao constantes `str` e
    nao sao frase nenhuma — uma cerca com sete falsos positivos e' uma cerca que alguem desliga.

    O valor de cada regra e' colhido com `ast.walk` sobre a EXPRESSAO inteira, nao so' sobre um
    `Name` direto: `enviar = CONSTANTE if recusa else f"{CONSTANTE} {texto}"` e' exatamente a forma
    que o `ja_ativo` usa, e uma leitura rasa perderia a constante mais sensivel do modulo.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    constantes = _constantes_str_de_modulo(arvore)
    alcancadas: dict[str, str] = {}

    def colher(expressao: ast.AST) -> None:
        for no in ast.walk(expressao):
            if isinstance(no, ast.Name) and no.id in constantes:
                alcancadas[no.id] = constantes[no.id]

    for classe in (n for n in ast.walk(arvore) if isinstance(n, ast.ClassDef)):
        for metodo in (n for n in ast.walk(classe) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            anotacao = ast.unparse(metodo.returns) if metodo.returns is not None else ""
            if anotacao != "str" and not anotacao.startswith("tuple[str"):
                continue
            for no in ast.walk(metodo):
                if isinstance(no, ast.Return) and no.value is not None:
                    devolvido = no.value
                    if isinstance(devolvido, ast.Tuple) and devolvido.elts:
                        devolvido = devolvido.elts[0]
                    colher(devolvido)

    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            chamada = no.func
            nome = chamada.attr if isinstance(chamada, ast.Attribute) else getattr(chamada, "id", "")
            if nome in ("send", "send_message"):
                for argumento in [*no.args, *(k.value for k in no.keywords)]:
                    colher(argumento)
            for palavra in no.keywords:
                if palavra.arg == "response_text":
                    colher(palavra.value)
        if isinstance(no, ast.Dict):
            for chave, valor in zip(no.keys, no.values, strict=True):
                if isinstance(chave, ast.Constant) and chave.value == "response_text":
                    colher(valor)
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if (
                    isinstance(alvo, ast.Subscript)
                    and isinstance(alvo.slice, ast.Constant)
                    and alvo.slice.value == "response_text"
                ) or (isinstance(alvo, ast.Name) and alvo.id in LOCAIS_QUE_VIRAM_TEXTO_ENVIADO):
                    colher(no.value)

    return alcancadas


def test_toda_constante_que_o_codigo_manda_ao_beneficiario_esta_no_corpus() -> None:
    """A MESMA DIRECAO DO TESTE ACIMA, SEM DEPENDER DO NOME DA CONSTANTE (21/09/2026, quarta
    rodada).

    O teste anterior varre por PREFIXO, e prefixo e' convencao: quem batizar `AVISO_DE_ESPERA` fica
    fora. Este le' o CODIGO e pergunta o que de fato alcanca um `send`, um `response_text` ou a
    local que vira texto enviado — quatro passos reais do caminho, descritos em
    `_constantes_que_o_codigo_envia`.

    O piso de nao-vacuidade (`CONSTANTES_QUE_CHEGAM_AO_BENEFICIARIO`) existe porque o modo de falha
    de uma cerca por reflexao e' ficar verde por nao achar nada. Ele NAO e' uma lista de
    completude: e' o conjunto que a varredura ja' provou ver, e a lista bate com os usos por
    construcao — se a varredura deixar de ver uma delas, este teste fica vermelho antes de o corpus
    ficar incompleto.
    """
    textos_no_corpus = {str(e["texto"]) for e in ENTRADAS}

    alcancadas: dict[str, str] = {}
    for modulo in _MODULOS:
        caminho = Path(str(modulo.__file__))
        for nome, valor in _constantes_que_o_codigo_envia(caminho).items():
            alcancadas[f"{modulo.__name__}.{nome}"] = valor

    nomes_curtos = {nome.rsplit(".", 1)[-1] for nome in alcancadas}
    invisiveis = sorted(CONSTANTES_QUE_CHEGAM_AO_BENEFICIARIO - nomes_curtos)
    assert not invisiveis, (
        "a varredura por AST deixou de ver constante que comprovadamente chega ao beneficiario "
        f"{invisiveis} — ou ela foi renomeada (edite o piso) ou a varredura quebrou (conserte-a); "
        "verde por nao achar nada e' o modo de falha desta cerca"
    )

    faltando = sorted(nome for nome, valor in alcancadas.items() if valor not in textos_no_corpus)
    assert not faltando, (
        "o codigo manda esta constante ao beneficiario e ela esta' AUSENTE do corpus de "
        f"nao-regressao (tests/unit/agents/corpus_cercas_de_saida.json): {faltando}"
    )
