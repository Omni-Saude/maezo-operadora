"""JORNADAS DO PLANO — o passo pratico de como usar o plano, depois da informacao de saude (melhoria inspirada
na TINA; decisao do dono de 18/10/2026; so' na allowlist da saude gerativa).

O QUE E'. Um conjunto PEQUENO e FECHADO de passos curtos, TEXTO DA OPERADORA em RASCUNHO para revisao (dono,
juridico e operacao antes de producao): fazer um exame, consultar um especialista, cirurgia ou procedimento
eletivo, internacao e urgencia/emergencia. Reembolso fica de fora: o repo nao tem a regra de reembolso do
plano.

COMO ENTRA. O grafo escolhe NO MAXIMO UMA jornada (pela pergunta e pela categoria do assunto da base) e a
frase
vai, como esta', no fim da resposta redigida — deterministica, sem modelo: nenhum nome de medico, clinica ou
laboratorio pode ser inventado, e a Helena nunca diz que pesquisa nem agenda (DL-0052; o roteiro `helena_v4`
exige isso). Nenhuma frase promete cobertura: a cobertura de um procedimento segue pelo Rol (DL-0122), com o
aviso.
"""

from __future__ import annotations

import re
from typing import Final

#: O mesmo prazo da Helena v4 (`helena_v4.PRAZO_ESPECIALISTA_DIAS_UTEIS`, RN 566/2022; a confirmar pelo
#: juridico)
#: — repetido aqui para o pacote `conhecimento` nao importar o agente (o teste amarra os dois).
PRAZO_ESPECIALISTA_DIAS_UTEIS: Final[int] = 14

JORNADA_EXAME: Final[str] = "exame"
JORNADA_ESPECIALISTA: Final[str] = "especialista"
JORNADA_CIRURGIA: Final[str] = "cirurgia"
JORNADA_INTERNACAO: Final[str] = "internacao"
JORNADA_URGENCIA: Final[str] = "urgencia"

#: Os textos (rascunho da operadora). Frases de ate' 20 palavras; sem nome de prestador; sem prometer
#: cobertura.
TEXTOS: Final[dict[str, str]] = {
    JORNADA_EXAME: (
        "Para fazer pelo plano: passe em consulta e peça o pedido médico assinado, carimbado e datado. "
        "Depois, leve o pedido e a carteirinha a um laboratório credenciado. "
        "A lista está no aplicativo Austa Clínicas ou no portal do plano."
    ),
    JORNADA_ESPECIALISTA: (
        "Você marca o especialista pelo aplicativo Austa Clínicas, pelo portal do plano ou pela "
        "central de atendimento do plano. Pela regra da ANS, a consulta deve ser oferecida em até "
        f"{PRAZO_ESPECIALISTA_DIAS_UTEIS} dias úteis."
    ),
    JORNADA_CIRURGIA: (
        "Para uma cirurgia ou procedimento eletivo, o pedido médico passa pela autorização prévia do plano. "
        "Acompanhe o pedido pelo aplicativo Austa Clínicas ou pela central de atendimento do plano."
    ),
    JORNADA_INTERNACAO: (
        "Para uma internação programada, o pedido médico passa pela autorização prévia do plano. "
        "Em urgência, vá direto ao pronto-socorro."
    ),
    JORNADA_URGENCIA: (
        "Em urgência ou emergência, vá ao pronto-socorro mais próximo ou ligue 192: o atendimento de "
        "emergência "
        "não espera autorização prévia."
    ),
}

#: A pergunta pede o CAMINHO pelo plano (ou o assunto e' de exame/procedimento/especialista).
_COMO_FAZER: Final[re.Pattern[str]] = re.compile(
    r"\b(?:pelo plano|no plano|como (?:faco|fazer|marco|marcar|consigo|agendo)|onde (?:faco|fazer|marco)"
    r"|preciso (?:fazer|de um|de uma)|quero fazer|vou fazer|tenho que fazer)\b"
)
_PALAVRAS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (JORNADA_URGENCIA, re.compile(r"\b(?:urgencia|emergencia|pronto[ -]socorro|pronto atendimento)\b")),
    (JORNADA_INTERNACAO, re.compile(r"\binterna\w*\b")),
    (JORNADA_CIRURGIA, re.compile(r"\b(?:cirurgi\w*|operar|operacao|procedimento|procedimentos)\b")),
    (
        JORNADA_EXAME,
        re.compile(r"\b(?:exames?|ultrassom|ressonancia|tomografia|raio x|colonoscopia|mamografia)\b"),
    ),
    (JORNADA_ESPECIALISTA, re.compile(r"\b(?:especialista|consulta com|medico que|qual medico)\b")),
)
#: A categoria do assunto da base (apoio) -> a jornada.
_POR_CATEGORIA: Final[dict[str, str]] = {
    "exame": JORNADA_EXAME,
    "cirurgia": JORNADA_CIRURGIA,
    "procedimento": JORNADA_CIRURGIA,
    "especialidade": JORNADA_ESPECIALISTA,
}


def jornada_da_pergunta(plano: str, categoria_do_apoio: str | None) -> str | None:
    """A jornada (no maximo UMA) que faz sentido para a pergunta NORMALIZADA, ou `None`. Precisa de um sinal
    de
    caminho ("pelo plano", "como faco", "preciso fazer") OU de um assunto de
    exame/procedimento/especialidade."""
    pede_caminho = bool(_COMO_FAZER.search(plano))
    for jornada, padrao in _PALAVRAS:
        if padrao.search(plano) and (pede_caminho or jornada in (JORNADA_URGENCIA,)):
            return jornada
    if categoria_do_apoio in _POR_CATEGORIA and pede_caminho:
        return _POR_CATEGORIA[categoria_do_apoio]
    if categoria_do_apoio in ("exame", "cirurgia", "procedimento"):
        return _POR_CATEGORIA[categoria_do_apoio]
    return None


__all__ = [
    "JORNADA_CIRURGIA",
    "JORNADA_ESPECIALISTA",
    "JORNADA_EXAME",
    "JORNADA_INTERNACAO",
    "JORNADA_URGENCIA",
    "TEXTOS",
    "jornada_da_pergunta",
]
