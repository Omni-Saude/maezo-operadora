"""Habilidade `plano`: a pergunta "o meu plano esta' apto / posso usar?" com a resposta DIRETA (teste do dono
de 25/10/2026).

O DEFEITO MEDIDO. "Voce nao consegue saber se meu plano esta' apto pra eu utilizar?" e "Eu quero saber se, ao
procurar um medico, meu plano estara' apto" chegaram como `elegibilidade`; o rascunho do modelo ("pode usar",
"esta' liberado") caiu na cerca de promessa (`recusa=promessa_de_cobertura`) e a resposta deterministica
despejou o cadastro inteiro (plano; segmentacao; acomodacao; vigencia; situacao; atendimento liberado) — duas
vezes seguidas, igual.

A REGRA. Quem pergunta se o plano esta' apto recebe a conclusao: "Sim. Pelo cadastro, o seu plano esta' ativo
e liberado para atendimento (situacao: Apto)." com o mesmo fecho dos fatos do plano; nao apto (suspenso,
cancelado, inapto) recebe o texto honesto equivalente com a central e o aplicativo. A pergunta REPETIDA na
conversa recebe so' a conclusao curta (sem o fecho de novo). O cadastro inteiro fica para quem pede os dados.

Modulo PURO (sem rede, sem estado do grafo): quem le' a fonte e' `consulta.py`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

from maezo.agents.helena.consultas_plano import (
    _FECHO,
    RESPOSTA_SEM_VINCULO_ATIVO,
    pergunta_do_tipo_do_plano,
    vinculo_ativo,
)
from maezo.agents.helena.habilidades.conversa.leitura_curta import normalizar
from maezo.agents.helena.historico import MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO
from maezo.ports.tina import ElegibilidadeView, VinculoPlano

#: "esta' apto?", "posso usar o plano?", "o plano esta' liberado?", "posso ser atendido?". "Meu plano esta'
#: ativo?" (sem "apto"/"usar") continua com o rascunho do modelo sobre os fatos (DL-0154, etapa 8).
_PERGUNTA_DE_APTO: Final[re.Pattern[str]] = re.compile(
    r"\bapt[oa]s?\b"
    r"|\b(?:posso|consigo|podemos|da (?:pra|para)|daria (?:pra|para)|vou poder|poderia|eu posso)"
    r" (?:ja )?(?:usar|utilizar)\b"
    r"|\b(?:usar|utilizar) (?:o |meu |o meu )?(?:plano|convenio)\b"
    r"|\b(?:plano|convenio)\b[^.?!]{0,30}\b(?:esta|ta|estara|esta mesmo|segue|continua|ficou)"
    r" (?:liberad[oa]|valendo|funcionando|habilitad[oa]|apto)\b"
    r"|\bposso ser atendid[oa]\b|\bliberad[oa] (?:pra|para) (?:usar|uso|atendimento|consultas?)\b"
)
#: Quem pede os DADOS do plano (o cadastro inteiro continua para estes).
_PEDE_OS_DADOS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:dados|informac\w+|detalhes|vigencia|desde quando|qual (?:e )?o (?:nome|numero)|carteirinha"
    r"|carencias?|dependentes?|autoriza\w*|guias?)\b"
)

#: As frases (texto do dono, 25/10/2026). `{situacao}` = o rotulo do cadastro, entre parenteses.
TEXTO_APTO: Final[str] = "Sim. Pelo cadastro, o seu plano está ativo e liberado para atendimento{situacao}."
TEXTO_NAO_APTO: Final[str] = (
    "Pelo cadastro, o seu plano não está liberado para atendimento agora{situacao}. Para entender o motivo "
    "ou regularizar, fale com a central de atendimento do plano ou veja no aplicativo Austa Clínicas."
)
#: A mesma pergunta de novo na conversa ("E3"): so' a conclusao, curta.
TEXTO_APTO_REPETIDO: Final[str] = (
    "Como eu disse, pelo cadastro o seu plano está liberado para atendimento{situacao}."
)
TEXTO_NAO_APTO_REPETIDO: Final[str] = (
    "Como eu disse, pelo cadastro o seu plano não está liberado para atendimento agora{situacao}. Quem "
    "resolve isso é a central de atendimento do plano."
)
#: "Nao entendi" depois da resposta de apto: a mesma conclusao em palavras simples (`reexplicar.py`).
TEXTO_APTO_SIMPLES: Final[str] = (
    "o seu cadastro mostra que o plano está valendo e pode ser usado para atendimento{situacao}. Isso não "
    "confirma a cobertura de um procedimento específico."
)
TEXTO_NAO_APTO_SIMPLES: Final[str] = (
    "o seu cadastro mostra que, hoje, o plano não pode ser usado para atendimento{situacao}. Quem explica o "
    "motivo e ajuda a resolver é a central de atendimento do plano."
)


def pergunta_se_esta_apto(mensagem: object) -> bool:
    """A mensagem pergunta se o plano esta' apto/liberado/pode ser usado (e NAO pede os dados do plano)?"""
    if not isinstance(mensagem, str):
        return False
    plano = normalizar(mensagem)
    if not _PERGUNTA_DE_APTO.search(plano):
        return False
    return not _PEDE_OS_DADOS.search(plano) and not pergunta_do_tipo_do_plano(mensagem)


def _situacao(v: VinculoPlano | None) -> str:
    rotulo = (
        (v.situacao_vinculo or "").strip() if v is not None and isinstance(v.situacao_vinculo, str) else ""
    )
    return f" (situação: {rotulo})" if rotulo else ""


def leitura_de_apto(view: object, hoje: date) -> tuple[bool, str] | None:
    """`(liberado, " (situacao: X)")` pelo cadastro INTEIRO (nao so' os vinculos ativos: a pessoa com o plano
    suspenso precisa ouvir a situacao), ou `None` sem view ou sem vinculo nenhum. PURA.

    Liberado = algum vinculo ATIVO hoje (`vinculo_ativo`) sem `atendimento_liberado` falso. Senao, a situacao
    do vinculo ativo bloqueado ou, sem ele, do vinculo mais recente."""
    if not isinstance(view, ElegibilidadeView) or not view.vinculos:
        return None
    ativos = [v for v in view.vinculos if vinculo_ativo(v, hoje)]
    liberados = [v for v in ativos if v.atendimento_liberado is not False]
    if liberados:
        return True, _situacao(liberados[0])
    if ativos:
        return False, _situacao(ativos[0])
    recente = max(view.vinculos, key=lambda v: str(v.vigencia_inicio or ""))
    return False, _situacao(recente)


def ja_perguntou_apto(historico: object) -> bool:
    """A pessoa JA' perguntou se o plano esta' apto nesta conversa e a Helena respondeu com dados do plano?

    Le' so' o historico curto (DL-0080): a fala da pessoa e a fala seguinte da Helena (o marcador dos dados do
    plano — o texto do plano nunca fica guardado)."""
    if not isinstance(historico, Sequence):
        return False
    itens = [e for e in historico if isinstance(e, Mapping)]
    for atual, seguinte in zip(itens, itens[1:], strict=False):
        if (
            atual.get("papel") == "beneficiario"
            and seguinte.get("papel") == "helena"
            and str(seguinte.get("texto") or "").strip() == MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO
            and pergunta_se_esta_apto(atual.get("texto"))
        ):
            return True
    return False


def resposta_de_apto(view: object, hoje: date, *, repetida: bool) -> str:
    """O texto da pergunta de apto: a conclusao (com o fecho dos fatos do plano), ou a curta na repeticao."""
    leitura = leitura_de_apto(view, hoje)
    if leitura is None:
        return RESPOSTA_SEM_VINCULO_ATIVO
    liberado, situacao = leitura
    if repetida:
        return (TEXTO_APTO_REPETIDO if liberado else TEXTO_NAO_APTO_REPETIDO).format(situacao=situacao)
    corpo = (TEXTO_APTO if liberado else TEXTO_NAO_APTO).format(situacao=situacao)
    return f"{corpo} {_FECHO}"


def resposta_simples_de_apto(view: object, hoje: date) -> str | None:
    """O miolo da reexplicacao ("nao entendi") da resposta de apto, ou `None` sem leitura."""
    leitura = leitura_de_apto(view, hoje)
    if leitura is None:
        return None
    liberado, situacao = leitura
    return (TEXTO_APTO_SIMPLES if liberado else TEXTO_NAO_APTO_SIMPLES).format(situacao=situacao)


def fatos_de_apto(view: object, hoje: date) -> dict[str, Any]:
    """Os literais do cadastro que o texto de apto usa (para a cerca TEXTO x FATO de `respond`)."""
    leitura = leitura_de_apto(view, hoje)
    return {"situacao": leitura[1].strip(" ()").removeprefix("situação: ")} if leitura else {}


__all__ = [
    "TEXTO_APTO",
    "TEXTO_APTO_REPETIDO",
    "TEXTO_NAO_APTO",
    "TEXTO_NAO_APTO_REPETIDO",
    "fatos_de_apto",
    "ja_perguntou_apto",
    "leitura_de_apto",
    "pergunta_se_esta_apto",
    "resposta_de_apto",
    "resposta_simples_de_apto",
]
