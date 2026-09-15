"""A tabela de suficiencia existe como DMN, e casa com o codigo (Frente 2.2, passo 2 de 4).

O QUE ESTE ARQUIVO PROVA, e o que ele explicitamente NAO prova.

PROVA — a parte que e' de engenharia:
  1. o arquivo existe, parseia, e a chave da decisao e' EXATAMENTE `SUFFICIENCY_DMN_KEY` — o nome
     pelo qual `classify` a consulta. Um nome divergente produz `DmnNoResultError` em producao, que
     vira `falha_tecnica`: todo turno com sintoma indo para humano por causa de uma letra;
  2. IDENTIDADE DE CONJUNTO entre os vereditos da tabela e o vocabulario fechado do codigo, NAS
     DUAS DIRECOES. Um veredito na tabela que o codigo nao conhece cai no ramo "fora do
     vocabulario" (`falha_tecnica`); um veredito no codigo que a tabela nunca emite e' codigo morto
     que da' a impressao de estar coberto;
  3. as ENTRADAS da tabela sao exatamente as que `classify` monta — localizadas pelo
     `inputExpression`, NUNCA pelo `label`. E' a licao de 13/09: o `label` engana (ele enganou a
     pagina do canal de teste), e a expressao e' o que o motor avalia;
  4. toda regra tem uma entrada por coluna. Uma regra curta e' aceita pelo XML e avaliada com
     colunas deslocadas — silenciosamente errada;
  5. a tabela TEM FIM e TEM CATCH-ALL. Sem a primeira, um beneficiario entra num interrogatorio;
     sem a segunda, uma tabela FIRST devolve vazio, `first_row` levanta, e um turno administrativo
     vira tarefa humana por falta de uma linha.

NAO PROVA — e a distincao e' o ponto:
  o CONTEUDO CLINICO. Se perguntar a idade gestacional antes da idade pediatrica, se duas rodadas
  bastam, se adulto sem idade e' mesmo suficiente — nada disso e' decidivel por teste. Esta' marcado
  no proprio DMN como aberto, e a tabela NAO esta ratificada. Uma cerca que afirmasse correcao
  clinica seria a quinta casca vazia deste projeto.

E O ARQUIVO EXISTIR NAO LIGA A COLETA. `coleta_enabled` continua `False`, e a ordem obrigatoria e'
ratificar -> arquivo -> publicar no motor -> so' entao ligar. Ha teste para isso abaixo, porque a
tentacao de "ja que a tabela existe" e' exatamente o atalho que o documento proibe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

import pytest

from maezo.agents.helena.graph import (
    COLETA_MAX_RODADAS,
    COLETA_VEREDITO_ESCALAR,
    COLETA_VEREDITO_SUFICIENTE,
    COLETA_VEREDITOS_PERGUNTA,
    SUFFICIENCY_DMN_KEY,
)

_DMN: Final[Path] = (
    Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn" / "triage_sufficiency.dmn"
)
_NS: Final[dict[str, str]] = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}

#: As entradas que `classify` monta em `_avaliar_suficiencia`. Repetidas aqui e conferidas contra a
#: tabela; a fonte de verdade do CODIGO e' o dicionario `entrada` daquele metodo.
_ENTRADAS_ESPERADAS: Final[tuple[str, ...]] = (
    "intent",
    "population",
    "sintoma_reconhecido",
    "intensidade_informada",
    "campo_populacao_disponivel",
    "rodadas",
)


def _tabela() -> ET.Element:
    raiz = ET.parse(_DMN).getroot()
    decisao = raiz.find(f".//d:decision[@id='{SUFFICIENCY_DMN_KEY}']", _NS)
    assert decisao is not None, (
        f"nenhuma `decision` com id {SUFFICIENCY_DMN_KEY!r} em {_DMN.name} — e' por este nome que "
        f"`classify` a consulta; divergir produz DmnNoResultError, que vira `falha_tecnica` e manda "
        f"TODO turno com sintoma para humano"
    )
    tabela = decisao.find("d:decisionTable", _NS)
    assert tabela is not None, "a decisao nao tem `decisionTable`"
    return tabela


def _entradas() -> list[str]:
    """As EXPRESSOES das colunas, na ordem.

    `Element` tem valor-verdade falso quando nao tem filhos, entao um `x or y` sobre o resultado de
    `find` mente — e mentiria aqui devolvendo string vazia para toda coluna, com o teste passando
    por comparar vazio com vazio. Comparacao explicita com `None`.
    """
    expressoes: list[str] = []
    for entrada in _tabela().findall("d:input", _NS):
        texto = entrada.find("d:inputExpression/d:text", _NS)
        expressoes.append("" if texto is None or texto.text is None else texto.text.strip())
    return expressoes


def _regras() -> list[ET.Element]:
    return _tabela().findall("d:rule", _NS)


def _veredito_de(regra: ET.Element) -> str:
    saida = regra.find("d:outputEntry/d:text", _NS)
    assert saida is not None and saida.text, f"regra {regra.get('id')!r} sem saida"
    return saida.text.strip().strip('"')


# =================================================================================================
# 1. Forma
# =================================================================================================


def test_a_tabela_existe_e_a_chave_e_a_que_o_codigo_consulta() -> None:
    assert _DMN.exists(), f"{_DMN} nao existe"
    assert _tabela() is not None


def test_a_politica_e_first_porque_a_ordem_das_linhas_e_a_regra() -> None:
    """Com `FIRST`, "tem fim" na primeira linha e o catch-all na ultima NAO sao estilo — sao a
    semantica. Trocar para `UNIQUE` ou `COLLECT` mudaria o comportamento de toda a tabela sem
    mudar linha nenhuma."""
    assert _tabela().get("hitPolicy") == "FIRST"


def test_as_entradas_sao_as_que_o_classify_monta_localizadas_pela_expressao() -> None:
    """Pelo `inputExpression`, NUNCA pelo `label`.

    O label e' texto para humano e ja enganou uma leitura neste projeto (a pagina do canal de
    teste, 13/09). A expressao e' o que o motor avalia.
    """
    assert _entradas() == list(_ENTRADAS_ESPERADAS), (
        f"entradas da tabela {_entradas()} != as que `classify` monta {list(_ENTRADAS_ESPERADAS)}"
    )


def test_toda_regra_tem_uma_entrada_por_coluna() -> None:
    """Uma regra curta e' XML valido e e' avaliada com as colunas DESLOCADAS.

    O motor nao reclama; a decisao sai errada em silencio. E' o mesmo defeito que
    `check_ledger_row_cell_count.py` existe para pegar noutro artefato deste repo.
    """
    colunas = len(_entradas())
    curtas = {
        regra.get("id"): len(regra.findall("d:inputEntry", _NS))
        for regra in _regras()
        if len(regra.findall("d:inputEntry", _NS)) != colunas
    }
    assert not curtas, f"regra(s) com numero de entradas != {colunas}: {curtas}"


def test_toda_regra_tem_descricao() -> None:
    """A tabela vai ser LIDA por um medico no editor dele. Uma regra sem descricao e' uma linha de
    condicoes que so' quem escreveu entende — e ratificar o que nao se entende e' assinar em
    branco."""
    sem = [r.get("id") for r in _regras() if (r.find("d:description", _NS) is None)]
    assert not sem, f"regra(s) sem `description`: {sem}"


# =================================================================================================
# 2. Identidade de conjunto com o codigo — nas duas direcoes
# =================================================================================================


def test_os_vereditos_da_tabela_e_do_codigo_sao_o_mesmo_conjunto() -> None:
    """A cerca que a Frente 6 ensinou: comparar as DUAS FONTES, nunca cada uma contra um literal.

    Um veredito na tabela que o codigo nao conhece cai no ramo "fora do vocabulario" e vira
    `falha_tecnica` — o turno vai para humano e ninguem sabe por que. Um veredito no codigo que a
    tabela nunca emite e' codigo morto que da' a impressao de estar coberto.
    """
    da_tabela = {_veredito_de(r) for r in _regras()}
    do_codigo = {COLETA_VEREDITO_SUFICIENTE, COLETA_VEREDITO_ESCALAR} | set(COLETA_VEREDITOS_PERGUNTA)
    assert da_tabela == do_codigo, (
        f"so' na tabela={sorted(da_tabela - do_codigo)}, so' no codigo={sorted(do_codigo - da_tabela)}"
    )


# =================================================================================================
# 3. As duas linhas estruturais
# =================================================================================================


def test_a_tabela_tem_fim_e_o_limite_e_o_do_codigo() -> None:
    """Sem a primeira linha, o beneficiario entra num interrogatorio — e quem esta em sofrimento e'
    justamente quem menos consegue responder a terceira pergunta.

    O limite e' REDUNDANTE com `classify` de proposito; o que este teste impede e' a redundancia
    sumir de um dos dois lados com o outro achando que esta coberto.
    """
    primeira = _regras()[0]
    assert _veredito_de(primeira) == COLETA_VEREDITO_ESCALAR
    entradas = [(e.find("d:text", _NS).text or "").strip() for e in primeira.findall("d:inputEntry", _NS)]
    rodadas = entradas[_ENTRADAS_ESPERADAS.index("rodadas")]
    assert rodadas == f">= {COLETA_MAX_RODADAS}", (
        f"a regra de fim usa {rodadas!r} mas o codigo escala em COLETA_MAX_RODADAS="
        f"{COLETA_MAX_RODADAS} — os dois limites tem de ser o mesmo numero"
    )


def test_a_ultima_regra_e_catch_all() -> None:
    """Uma tabela FIRST sem linha final devolve VAZIO, `first_row` levanta `DmnNoResultError`, e o
    codigo trata isso como `falha_tecnica`.

    Resultado: um turno administrativo virando tarefa humana por falta de uma linha — a tabela
    inundando a fila com o que ela nem deveria julgar.
    """
    ultima = _regras()[-1]
    entradas = [(e.find("d:text", _NS).text or "").strip() for e in ultima.findall("d:inputEntry", _NS)]
    # `-` e' a celula "qualquer valor" na convencao deste repo, nao a string vazia — e' o que
    # `test_dmn_dead_inputs_fence` cobra das quatro tabelas de red flag. Uma celula vazia e' XML
    # valido e o motor a trata igual, mas quebra a leitura visual no editor do medico, que e'
    # justamente onde esta tabela vai ser ratificada.
    assert all(entrada == "-" for entrada in entradas), f"a ultima regra nao e' catch-all: {entradas}"
    assert _veredito_de(ultima) == COLETA_VEREDITO_SUFICIENTE


def test_as_cinco_regras_de_pergunta_vem_antes_do_catch_all() -> None:
    """Com FIRST, uma regra de pergunta depois do catch-all e' inalcancavel — e inalcancavel nao
    levanta erro nenhum: a tabela simplesmente para de perguntar."""
    vereditos = [_veredito_de(r) for r in _regras()]
    indice_catchall = len(vereditos) - 1
    perguntas_depois = [
        i for i, v in enumerate(vereditos) if v in COLETA_VEREDITOS_PERGUNTA and i > indice_catchall
    ]
    assert not perguntas_depois, f"regra(s) de pergunta inalcancavel(is): {perguntas_depois}"


@pytest.mark.parametrize("veredito", sorted(COLETA_VEREDITOS_PERGUNTA))
def test_cada_veredito_de_pergunta_tem_pelo_menos_uma_regra_que_o_emite(veredito: str) -> None:
    """Um veredito que nenhuma regra emite e' um caminho de codigo que nunca roda em producao —
    testado em unidade, morto na pratica."""
    assert veredito in {_veredito_de(r) for r in _regras()}


# =================================================================================================
# 4. O arquivo existir NAO liga a coleta
# =================================================================================================


def test_a_coleta_continua_desligada_por_default() -> None:
    """A tentacao "ja que a tabela existe" e' exatamente o atalho que o documento proibe.

    A ordem e' (1) ratificar o conteudo, (2) o arquivo, (3) publicar no motor, (4) so' entao
    `coleta_enabled=true`. Ligar antes do passo 3 faz o codigo falhar FECHADO: DMN indisponivel
    vira `falha_tecnica` e TODO turno vai para humano. Seguro, e inutil.
    """
    import inspect

    from maezo.agents.helena.graph import HelenaGraph, build

    assinatura = inspect.signature(HelenaGraph.__init__)
    assert assinatura.parameters["coleta_enabled"].default is False
    fonte = inspect.getsource(build)
    assert 'cfg.get("coleta_enabled", False) is True' in fonte, (
        "a fabrica deixou de exigir `coleta_enabled` EXPLICITO — ligar a coleta tem de ser um ato "
        "de quem monta o runtime, com a tabela ratificada no motor, nunca um default"
    )


def test_o_dmn_diz_em_texto_que_nao_esta_ratificado() -> None:
    """Quem abrir o arquivo tem de ver isso antes de qualquer coisa.

    Um artefato que parece pronto e' tratado como pronto — e este e' um artefato que decide se uma
    pergunta e' feita a alguem com sintoma.
    """
    texto = _DMN.read_text(encoding="utf-8")
    assert "NAO RATIFICADO" in texto
    assert "coleta_enabled" in texto, "o arquivo nao diz que existir nao liga a coleta"
