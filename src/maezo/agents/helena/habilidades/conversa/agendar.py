"""AGENDAMENTO (teste do dono de 25/10/2026, caso d). Modulo FOLHA (sem import do grafo).

"Consigo agendar uma consulta por aqui?" foi lida como pergunta do plano (o #984 mandava "como marcar" para
`plano`) e recebeu o cadastro inteiro. A Helena NAO agenda: a resposta e' a orientacao dos canais (aplicativo
Austa Clinicas, portal do plano, central de atendimento do plano), sem consultar o plano. Quando a mesma
mensagem traz uma queixa ("estou com dor nas costas, como marco consulta?"), a orientacao leva tambem a oferta
de dizer qual especialista procurar. Os dois textos moram no pacote (`agendar_orientacao`,
`agendar_oferta_especialista`); a decisao de qual vale e' deste modulo, em codigo (sem modelo).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from maezo.agents.helena.habilidades.conversa.leitura_curta import normalizar

CHAVE_AGENDAR_ORIENTACAO: Final[str] = "agendar_orientacao"
CHAVE_AGENDAR_OFERTA: Final[str] = "agendar_oferta_especialista"
TEXTO_AGENDAR_ORIENTACAO: Final[str] = (
    "Por aqui eu não agendo. Você marca pelo aplicativo Austa Clínicas, pelo portal do plano ou pela "
    "central de atendimento do plano."
)
TEXTO_AGENDAR_OFERTA: Final[str] = "Se me disser o que está sentindo, eu digo qual especialista procurar."
PADRAO: Final[dict[str, str]] = {
    CHAVE_AGENDAR_ORIENTACAO: TEXTO_AGENDAR_ORIENTACAO,
    CHAVE_AGENDAR_OFERTA: TEXTO_AGENDAR_OFERTA,
}

_VERBO: Final[str] = r"(?:marc(?:ar|o|a|ando|amos)|agend(?:ar|o|a|ando|amos|amento)|reserv(?:ar|o))"
_OBJETO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:consultas?|exames?|cirurgias?|procedimentos?|horarios?|atendimento|medic[oa]s?|especialista|"
    r"retorno|dentista|pediatra|ginecologista|cardiologista|ortopedista|dermatologista|psicolog[oa]|"
    r"psiquiatra|terapia)\b"
)
_INTENCAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:quero|queria|quer|gostaria|preciso|precisava|precisaria|como|consigo|consegue|posso|pode|"
    r"da pra|dar pra|onde|quando|possivel|tem como|voce|vc|ajuda|ajudar|tem que|vou)\b"
)
_VERBO_RX: Final[re.Pattern[str]] = re.compile(rf"\b{_VERBO}\b")
#: Assunto de OUTRA habilidade na mesma mensagem: quem decide e' o roteador (autorizacao, rede, reembolso...).
_OUTRO_ASSUNTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:autoriza\w*|guias?|carencias?|coparticipac\w*|cobre|cobrem|cobertura|credenciad\w*|rede|reembolso\w*|"
    r"cancel\w*|remarc\w*|desmarc\w*|boletos?|mensalidades?|dependentes?|carteirinhas?|resultados?|"
    r"onde fazer)\b"
)
_QUEIXA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:dor|dores|doi|doendo|dolorid[oa]|sinto|sentindo|sentir|febre|tosse|enjoo|enjoad[oa]|vomit\w*|"
    r"tontura|tonto|tonta|inchad[oa]|inchaco|coceira|machuc\w*|alergia|cansaco|ansiedade|insonia|sangr\w*|"
    r"mancha|caroco|nodulo|queda|caiu|torci\w*|incomoda\w*|mal estar|diarreia|azia|gastrite|cansad[oa])\b"
)


def pergunta_de_agendamento(texto: object) -> bool:
    """A mensagem quer saber como/onde marcar ou agendar (ou pede que a Helena marque)? Assunto de outra
    habilidade na mesma frase (autorizacao, rede credenciada, reembolso, cancelamento...) nao conta."""
    if not isinstance(texto, str):
        return False
    plano = normalizar(texto)
    if not _VERBO_RX.search(plano) or _OUTRO_ASSUNTO.search(plano):
        return False
    tem_objeto = bool(_OBJETO.search(plano))
    if re.search(r"\bmarca\b", plano) and not tem_objeto:
        return False
    return tem_objeto or bool(_INTENCAO.search(plano))


def com_queixa(texto: object) -> bool:
    """A mensagem traz uma queixa de saude (para a oferta do especialista)?"""
    return isinstance(texto, str) and bool(_QUEIXA.search(normalizar(texto)))


def texto_do_agendamento(textos: Mapping[str, str], mensagem: object) -> str:
    """A orientacao dos canais e, com queixa na mensagem, a oferta de dizer o especialista."""
    base = textos[CHAVE_AGENDAR_ORIENTACAO]
    return f"{base} {textos[CHAVE_AGENDAR_OFERTA]}" if com_queixa(mensagem) else base


__all__ = [
    "CHAVE_AGENDAR_OFERTA",
    "CHAVE_AGENDAR_ORIENTACAO",
    "PADRAO",
    "TEXTO_AGENDAR_OFERTA",
    "TEXTO_AGENDAR_ORIENTACAO",
    "com_queixa",
    "pergunta_de_agendamento",
    "texto_do_agendamento",
]
