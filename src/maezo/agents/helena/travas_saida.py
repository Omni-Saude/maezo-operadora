"""TRAVAS DE SAIDA da Helena (ADR-0067 §5): regras fixas sobre o texto que vai sair, depois de tudo.

`sem_links` — trava `sem_link` (dono, 18/10/2026 e Plano de ajustes v2, etapa 10): a Helena NUNCA manda link
externo. Qualquer endereco (http/https, www. ou dominio como gov.br) sai do texto no ultimo passo antes do
envio, venha de onde vier (base de saude, redacao do modelo, texto antigo). A fonte continua citada pelo
NOME ("Fonte: Saude de A a Z (Ministerio da Saude)"); o rotulo que so' apresentava o link ("Para ler mais:",
"Link:") sai junto.

`sem_contagem` — trava `sem_contagem` (avaliacao de 25/10/2026): o modelo obedeceu a uma regra de tamanho do
prompt ESCREVENDO a contagem na resposta ("Entendi que o medico passou o exame. (8 palavras)"). Contagem
entre parenteses ou colchetes ("(8 palavras)", "(12 words)", "(3 frases)", "[40 caracteres]") sai do texto.

`sem_oferta_de_midia` — trava `so_texto` (teste do dono de 25/10/2026): a Helena so' le' TEXTO, mas a
redacao do modelo respondeu "voce pode enviar a foto do pedido medico por aqui". A frase que convida a mandar
foto, imagem, print, audio, video, arquivo ou documento e' trocada pelo aviso de que aqui so' se le' texto.

`sem_prazo_da_ans` — trava `sem_prazo_da_ans` (decisao do dono, 25/10/2026): nenhum texto da Helena cita prazo
de atendimento da ANS ("em ate 7 dias uteis pela ANS", "pelas regras da ANS ... deve ser oferecida em ate 14
dias uteis"). O trecho entre parenteses com o prazo sai, e a frase inteira que traz o prazo (ANS + prazo, ou
"N dias uteis" numa frase de regra/oferta de consulta) sai do texto. O numero de registro da ANS no cadastro
do plano e as outras frases ficam.

`na_saida` — as quatro juntas, no ultimo passo de TODO envio da Helena (resposta do turno, aviso de identidade
e retomada da equipe). Nada a tirar: o MESMO texto, byte a byte (os textos fixos do pacote saem intactos).
"""

from __future__ import annotations

import re
from typing import Final

import structlog

from .textos import LINK

logger = structlog.get_logger(__name__)

#: Rotulo que so' apresentava um link (sem link depois dele, nao diz nada).
_ROTULO_DE_LINK: Final[re.Pattern[str]] = re.compile(
    r"(?im)^[ \t]*(?:para (?:ler|saber) mais|leia mais|saiba mais|link|acesse|veja|site)\s*:[ \t]*$"
)
_ESPACOS: Final[re.Pattern[str]] = re.compile(r"[ \t]{2,}")
_ESPACO_ANTES_DE_PONTO: Final[re.Pattern[str]] = re.compile(r"[ \t]+([.,;:!?)])")
_PARENTESES_VAZIOS: Final[re.Pattern[str]] = re.compile(r"\(\s*\)")
_LINHAS_VAZIAS: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


def sem_links(texto: str) -> str:
    """O texto sem nenhum link (o nome da fonte fica). Sem link no texto: o MESMO texto, byte a byte."""
    if not LINK.search(texto):
        return texto
    limpo = LINK.sub("", texto)
    limpo = _ROTULO_DE_LINK.sub("", limpo)
    limpo = _PARENTESES_VAZIOS.sub("", limpo)
    limpo = _ESPACO_ANTES_DE_PONTO.sub(r"\1", limpo)
    limpo = _ESPACOS.sub(" ", limpo)
    limpo = "\n".join(linha.rstrip() for linha in limpo.split("\n"))
    limpo = _LINHAS_VAZIAS.sub("\n\n", limpo).strip()
    logger.warning("helena_link_retirado_na_saida")
    return limpo


#: Contagem interna vazada ("(8 palavras)", "(~12 words)", "[3 frases]", "(palavras: 8)").
_UNIDADES_DE_CONTAGEM: Final[str] = (
    r"(?:palavras?|words?|frases?|senten[cç]as?|sentences?|caracteres?|characters?|chars?|tokens?)"
)
_CONTAGEM: Final[re.Pattern[str]] = re.compile(
    r"[ \t]*[(\[]\s*(?:"
    rf"(?:~|±|cerca de\s+|aprox\.?\s*|aproximadamente\s+)?\d+\s*{_UNIDADES_DE_CONTAGEM}"
    rf"|{_UNIDADES_DE_CONTAGEM}\s*[:=]\s*\d+"
    r")\s*[)\]]",
    re.IGNORECASE,
)


def _arrumar(texto: str) -> str:
    limpo = _ESPACO_ANTES_DE_PONTO.sub(r"\1", texto)
    limpo = _ESPACOS.sub(" ", limpo)
    limpo = "\n".join(linha.rstrip() for linha in limpo.split("\n"))
    return _LINHAS_VAZIAS.sub("\n\n", limpo).strip()


def sem_contagem(texto: str) -> str:
    """O texto sem contagem de palavras/frases/caracteres entre parenteses ou colchetes. Sem contagem: o
    MESMO texto, byte a byte."""
    if not _CONTAGEM.search(texto):
        return texto
    limpo = _arrumar(_CONTAGEM.sub("", texto))
    logger.warning("helena_contagem_retirada_na_saida")
    return limpo


#: O aviso que entra no lugar da frase que convidava a mandar midia (o mesmo sentido do aviso que a pessoa
#: recebe quando manda audio/foto: aqui so' se le' texto).
AVISO_SO_TEXTO: Final[str] = (
    "Por aqui eu consigo ler só mensagens de texto: pode escrever aqui o que precisa."
)
_VERBO_DE_ENVIO: Final[str] = (
    r"(?:envi(?:ar|e|a|ando|em)|mand(?:ar|e|a|ando|em)|anex(?:ar|e|a|ando)|encaminh(?:ar|e|a)"
    r"|compartilh(?:ar|e|a))"
)
#: "exame de imagem" nao e' midia (e' o exame): `imagem` so' conta sem o "de " na frente.
_MIDIA: Final[str] = (
    r"(?:fotos?|fotografias?|(?<!de )image(?:m|ns)|prints?|capturas? de tela|screenshots?|[áa]udios?"
    r"|v[íi]deos?|arquivos?|anexos?|documentos?|pdfs?|digitaliza\w*)"
)
_OFERTA_DE_MIDIA: Final[re.Pattern[str]] = re.compile(
    rf"\b{_VERBO_DE_ENVIO}\b(?:\W+\w+){{0,6}}?\W+{_MIDIA}\b"
    rf"|\b{_MIDIA}\b(?:\W+\w+){{0,6}}?\W+{_VERBO_DE_ENVIO}\b",
    re.IGNORECASE,
)
#: A frase que NEGA o envio ("nao precisa mandar foto"), que ja' e' o aviso de so' texto, ou que manda a
#: midia para OUTRO canal ("envie o pedido pelo aplicativo") fica: a trava e' sobre mandar AQUI.
_NEGA_OU_AVISA: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:n[ãa]o|nunca|nem)\s+(?:\w+\s+){{0,3}}?{_VERBO_DE_ENVIO}\b"
    r"|\b(?:s[óo]|somente|apenas)\b(?:\W+\w+){0,4}?\W+(?:texto|escrit)"
    r"|\b(?:aplicativo|app|portal|e-?mail|central|site|unidade|presencialmente)\b",
    re.IGNORECASE,
)
_SEPARADOR_DE_FRASE: Final[re.Pattern[str]] = re.compile(r"((?<=[.!?…])[ \t]+|\n+)")
_SO_SIMBOLOS: Final[re.Pattern[str]] = re.compile(r"^[\W_]+$")


def _oferece_midia(frase: str) -> bool:
    return bool(_OFERTA_DE_MIDIA.search(frase)) and not _NEGA_OU_AVISA.search(frase)


def sem_oferta_de_midia(texto: str) -> str:
    """A frase que convida a mandar foto/audio/arquivo vira `AVISO_SO_TEXTO` (uma vez so'). Sem convite: o
    MESMO texto, byte a byte."""
    partes = _SEPARADOR_DE_FRASE.split(texto)
    frases = partes[0::2]
    if not any(_oferece_midia(f) for f in frases):
        return texto
    avisou = AVISO_SO_TEXTO in texto
    saida: list[str] = []
    tirou_a_anterior = False
    for i, parte in enumerate(partes):
        if i % 2:
            saida.append(parte)
            continue
        if _oferece_midia(parte):
            saida.append("" if avisou else AVISO_SO_TEXTO)
            avisou = tirou_a_anterior = True
            continue
        if tirou_a_anterior and _SO_SIMBOLOS.match(parte.strip() or "."):
            # O emoji que fechava a frase trocada ("... por aqui mesmo. 😊") sai com ela.
            saida.append("")
            continue
        tirou_a_anterior = False
        saida.append(parte)
    logger.warning("helena_oferta_de_midia_trocada_na_saida")
    return _arrumar("".join(saida))


_ANS: Final[re.Pattern[str]] = re.compile(r"\bANS\b", re.IGNORECASE)
_DIAS_UTEIS: Final[re.Pattern[str]] = re.compile(r"\bdias?\s+[uú]teis\b", re.IGNORECASE)
_PRAZO: Final[re.Pattern[str]] = re.compile(
    r"\bprazos?\b|\bem\s+at[eé]\s+\d+\s+dias\b|\bdeve(?:m)?\s+ser\s+oferecid\w+|\bdeve\s+oferecer\b",
    re.IGNORECASE,
)
_REGRA_OU_OFERTA: Final[re.Pattern[str]] = re.compile(
    r"\bregras?\b|\boferec\w+|\bconsultas?\b|\batendimento\b|\bnorma\b", re.IGNORECASE
)
_PARENTESES: Final[re.Pattern[str]] = re.compile(r"[ \t]*\([^()]*\)")


def _e_prazo_da_ans(trecho: str) -> bool:
    if _DIAS_UTEIS.search(trecho) and (_ANS.search(trecho) or _REGRA_OU_OFERTA.search(trecho)):
        return True
    return bool(_ANS.search(trecho) and _PRAZO.search(trecho))


def sem_prazo_da_ans(texto: str) -> str:
    """O texto sem o prazo de atendimento da ANS (parenteses e frases). Sem prazo da ANS: o MESMO texto, byte
    a byte."""
    if not (_ANS.search(texto) or _DIAS_UTEIS.search(texto)) or not (
        _DIAS_UTEIS.search(texto) or _PRAZO.search(texto)
    ):
        return texto
    limpo = _PARENTESES.sub(lambda m: "" if _e_prazo_da_ans(m.group(0)) else m.group(0), texto)
    partes = _SEPARADOR_DE_FRASE.split(limpo)
    saida: list[str] = []
    for i in range(0, len(partes), 2):
        frase = partes[i]
        separador = partes[i + 1] if i + 1 < len(partes) else ""
        if _e_prazo_da_ans(frase):
            continue
        saida.append(frase + separador)
    resultado = _arrumar("".join(saida))
    if resultado == texto:
        return texto
    logger.warning("helena_prazo_da_ans_retirado_na_saida")
    return resultado


def na_saida(texto: str) -> str:
    """Todas as travas de saida, na ordem: link, contagem, prazo da ANS, oferta de midia. Nada a tirar: o
    MESMO texto."""
    return sem_oferta_de_midia(sem_prazo_da_ans(sem_contagem(sem_links(texto))))


__all__ = [
    "AVISO_SO_TEXTO",
    "na_saida",
    "sem_contagem",
    "sem_links",
    "sem_oferta_de_midia",
    "sem_prazo_da_ans",
]
