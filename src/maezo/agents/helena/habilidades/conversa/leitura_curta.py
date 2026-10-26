"""Leitura das falas CURTAS da conversa (cortesia, cumprimento, sim/nao curto, recusa, reclamacao).

Movido SEM MUDANCA de `cobertura_assistida.py` (Plano de ajustes da Helena v2, etapa 5): a cobertura saiu da
Helena (decisao do dono de 23/10/2026, DL-0154), mas estes leitores nunca foram da cobertura — a conversa, o
pedido de pessoa e o encerramento de caso os usam. Na etapa 7 (contrato v7 do roteador) o modelo passa a
ler o "sim", a recusa e a cortesia no contexto, e o que o roteador substituir sai daqui (ADR-0066 F5).

Modulo PURO (sem rede, sem estado).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final


def normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento).split())


_SIM: Final[re.Pattern[str]] = re.compile(
    r"^(?:sim|s|pode|pode sim|quero|quero sim|ok|claro|por favor|pode passar|passa|pode ser|aceito|isso|"
    r"sim por favor|sim pode|pode passar sim|1 sim)$"
)
_NAO: Final[re.Pattern[str]] = re.compile(
    r"^(?:nao|n|nao obrigad[oa]|agora nao|nao precisa|deixa|deixa pra la)$"
)


#: Palavras de cortesia que nao mudam o sentido da resposta curta ("quero sim, por favor, obrigada").
_CORTESIA: Final[frozenset[str]] = frozenset(
    {"por", "favor", "obrigado", "obrigada", "obg", "brigado", "brigada", "entao", "ta", "bom", "bem", "ai",
     "moca", "helena", "viu", "tudo", "sim", "certo"}
)  # fmt: skip
_COMECO_SIM: Final[re.Pattern[str]] = re.compile(
    r"^(?:sim|s|ss|quero|quero sim|pode|pode sim|pode ser|pode passar|passa|passe|claro|com certeza|ok$|okay$"
    r"|ok pode|ok sim|aceito|isso|isso mesmo|exato|exatamente|correto|perfeito|beleza|blz|uhum|aham|yes"
    r"|sim quero|e isso"
    r"|e esse|esse mesmo|essa mesma|esse ai|essa ai|isso ai|por favor|faz isso|manda)\b"
)
_COMECO_NAO: Final[re.Pattern[str]] = re.compile(r"^(?:nao|n|nem|negativo|agora nao|nao quero)\b")
#: Encerrar o assunto ("deixa pra la", "esquece") — encerra com educacao, nunca vira busca.
_ENCERRAR_ASSUNTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:deixa (?:pra|para) la|deixa quieto|deixa isso|deixa assim|deixa pra depois|esquece|esqueca"
    r"|esquecer|desis(?:to|ti)\b|nao precisa mais|nao preciso mais|nao quero mais|tanto faz"
    r"|mudei de ideia|outra hora"
    # Avaliacao r4 de 22/10/2026: abrir mao de um pedido ou de um caso ("pode cancelar", "ja resolvi").
    r"|pode cancelar|cancela(?: isso| tudo| o pedido| a solicitacao)?$|ja resolvi|ja consegui resolver"
    r"|nao precisa (?:me )?encaminhar|nao precisa (?:me )?(?:ligar|chamar|retornar)"
    r"|depois eu (?:vejo"
    r"|pergunto|resolvo|volto)|depois (?:vejo|pergunto|resolvo)|nao precisa nao|so isso mesmo)\b"
)
#: Nao sabe o nome / nao tem o pedido — conta como tentativa, nunca vira busca.
_NAO_SEI: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nao sei|nao lembro|nao me lembro|nao faco (?:a menor )?ideia|sei la|esqueci (?:o nome|como)"
    r"|nao tenho (?:o |a )?(?:pedido|nome|guia|receita)|nao sei (?:o nome|escrever|direito|qual)"
    r"|nao entendi o nome|nao consigo ler)\b"
)


#: So' agradecimento/conversa ("valeu", "obrigada, era isso", "beleza, entendi"): nao e' pedido novo.
_VOCABULARIO_DE_CORTESIA: Final[frozenset[str]] = frozenset(
    {"obrigado", "obrigada", "obg", "brigado", "brigada", "muito", "muitissimo", "valeu", "vlw", "beleza",
     "blz", "entendi", "entendido", "ok", "okay", "certo", "perfeito", "show", "otimo", "ta", "bom", "tudo",
     "bem", "era", "isso", "so", "mesmo", "maravilha", "top", "legal", "combinado", "tchau", "ate", "mais",
     "logo", "abraco", "abracos", "grato", "grata", "agradeco", "de", "nada", "e", "entao", "ai", "boa",
     "noite", "dia", "tarde", "por", "enquanto", "hoje", "a", "o", "esclarecido", "esclarecida", "ajudou",
     "me", "voce", "otima", "sim", "ah", "hum", "humm", "aham", "uhum"}
)  # fmt: skip


def e_cortesia(texto: str) -> bool:
    if so_emoji_de_acordo(texto):
        return True
    palavras = normalizar(texto).split()
    return bool(palavras) and len(palavras) <= 10 and all(p in _VOCABULARIO_DE_CORTESIA for p in palavras)


#: As palavras que cabem numa resposta curta de sim/nao (alem da cortesia).
_VOCABULARIO_DA_RESPOSTA: Final[frozenset[str]] = frozenset(
    {"sim", "s", "ss", "quero", "pode", "ser", "passar", "passa", "passe", "claro", "com", "certeza", "ok",
     "okay", "aceito", "isso", "mesmo", "exato", "exatamente", "correto", "perfeito", "beleza", "blz", "uhum",
     "aham", "yes", "e", "esse", "essa", "ai", "faz", "manda", "para", "pra", "a", "o", "equipe", "nao", "n",
     "nem", "negativo", "agora", "precisa", "obrigado", "obrigada", "valeu", "quer", "pfv", "pf",
     "encaminhar", "encaminha", "encaminhe", "encaminhem"}
)  # fmt: skip


def ler_resposta(texto: str) -> str | None:
    """A resposta CURTA a uma pergunta da Helena: "encerrar", "nao_sei", "nao", "sim" ou `None` (nao e'
    resposta: pode ser um pedido novo). Regra geral, sem termo de procedimento."""
    if so_emoji_de_acordo(texto):
        return "sim"
    plano = normalizar(texto)
    if not plano:
        return None
    palavras = plano.split()
    if _ENCERRAR_ASSUNTO.search(plano) and len(palavras) <= 10:
        return "encerrar"
    if _NAO_SEI.search(plano) and len(palavras) <= 12:
        return "nao_sei"
    if len(palavras) > 7:
        return None
    resto = [p for p in palavras if p not in _CORTESIA]
    # DL-0131: o resto da frase so' pode ter palavras de resposta e cortesia ("quero sim, por favor");
    # "quero saber da carencia" e' pedido novo, nunca "sim".
    so_resposta = all(p in _VOCABULARIO_DA_RESPOSTA or p in _CORTESIA for p in palavras)
    if _COMECO_NAO.match(plano) and not _COMECO_SIM.match(plano) and so_resposta:
        return "nao"
    if _COMECO_SIM.match(plano) and "nao" not in palavras and so_resposta:
        return "sim"
    if not resto and ("sim" in palavras or "certo" in palavras):
        return "sim"
    return None


def e_sim(texto: str) -> bool:
    return bool(_SIM.match(normalizar(texto))) or ler_resposta(texto) == "sim"


def e_nao(texto: str) -> bool:
    return bool(_NAO.match(normalizar(texto))) or ler_resposta(texto) == "nao"


#: DL-0131: irritacao ou "nao entendi" nunca abre caso sem a pessoa concordar: desculpa curta e de novo.
TEXTO_DESCULPA: Final[str] = "Desculpe a confusão."
TEXTO_DESCULPA_SEM_ASSUNTO: Final[str] = (
    "Desculpe a confusão. Me diga de novo o que você precisa, que eu tento outra vez."
)


#: Avaliacao de 21/10/2026: fala curta depois de uma resposta ("ok", "valeu", "blz").
TEXTO_DE_NADA: Final[str] = "Por nada! Se precisar, é só chamar."
TEXTO_COMBINADO: Final[str] = "Combinado! Se precisar, é só chamar."
_AGRADECE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:obrigad[oa]|obg|valeu|vlw|brigad[oa]|grat[oa]|agradeco)\b"
)


#: Avaliacao r4 de 22/10/2026: "bom dia" era respondido como despedida ("Combinado! Se precisar...").
#: Cumprimento SEM agradecimento nem despedida recebe o cumprimento curto de sempre (o mesmo texto do
#: `graph.RESPOSTA_SAUDACAO_CURTA`).
TEXTO_SAUDACAO: Final[str] = "Olá! Em que posso ajudar? 😊"
_CUMPRIMENTO: Final[re.Pattern[str]] = re.compile(r"^(?:oi|ola|bom dia|boa tarde|boa noite)\b")
_DESPEDIDA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tchau|ate (?:mais|logo|amanha|breve|a proxima)|abracos?|fui|bjs?|beijos?|falou)\b"
)
#: O polegar e afins sozinhos: cortesia, e "sim" quando ha' uma pergunta de sim/nao pendente.
_EMOJI_DE_ACORDO: Final[frozenset[str]] = frozenset({"👍", "👌", "✅", "🙏", "👏"})


def so_emoji_de_acordo(texto: str) -> bool:
    limpo = "".join(c for c in (texto or "") if not c.isspace() and unicodedata.category(c) != "Sk")
    limpo = limpo.replace("\ufe0f", "")
    return bool(limpo) and all(c in _EMOJI_DE_ACORDO for c in limpo)


def e_cumprimento(texto: str) -> bool:
    """So' cumprimento ("bom dia", "oi, tudo bem?"), sem agradecimento nem despedida."""
    plano = normalizar(texto)
    return (
        len(plano.split()) <= 5
        and bool(_CUMPRIMENTO.match(plano))
        and not _AGRADECE.search(plano)
        and not _DESPEDIDA.search(plano)
    )


def resposta_de_cortesia(texto: str) -> str:
    if e_cumprimento(texto):
        return TEXTO_SAUDACAO
    return TEXTO_DE_NADA if _AGRADECE.search(normalizar(texto)) else TEXTO_COMBINADO


#: Irritacao, reclamacao ou "nao entendi" (DL-0131): pede desculpa e tenta de novo; nunca abre caso sozinha.
_RECLAMACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:ue|uai|como assim|nao entendi|nao entendo|voce acabou de|vc acabou de|acabou de (?:dizer|falar"
    r"|verificar|responder|consultar)|ja (?:te )?(?:disse|falei|respondi|tinha dito)|eu ja (?:disse|falei"
    r"|respondi|tinha dito|tinha falado)|absurdo|ridiculo|que isso|nao foi isso|nao e isso que|errad[oa]"
    r"|de novo isso|pela? (?:segunda|terceira) vez|nao faz sentido|repetindo)\b"
)
_JA_DISSE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:ja (?:te )?(?:disse|falei|respondi|tinha dito|tinha falado)|eu ja (?:disse|falei|respondi|tinha"
    r" dito|tinha falado))\b"
)


def e_reclamacao(texto: str) -> bool:
    return bool(_RECLAMACAO.search(normalizar(texto)))


def ja_disse(texto: str) -> bool:
    """ "Eu ja' disse..." e' lido como RESPOSTA a pergunta pendente (com um pedido de desculpas curto)."""
    return bool(_JA_DISSE.search(normalizar(texto)))


_PERGUNTA_NOVA: Final[re.Pattern[str]] = re.compile(
    r"^(?:e )?(?:quem|qual|quais|como|quando|onde|porque|por que|quanto|quantos|quantas|o que|voce pode"
    r"|vc pode|pode me|me (?:diga|fala|mostra|passa|explica|informa|ajuda|ajude)|ajuda|ajude|quero|queria"
    r"|gostaria|preciso"
    r"|tem como)\b"
)


def resposta_curta(texto: str) -> bool:
    """DL-0131: SO' a resposta CURTA e' lida contra a pergunta pendente ("1", "sim", "nao", "ok", um nome,
    "eu ja' disse"). Pergunta ou pedido novo ESCAPA para o roteamento normal."""
    plano = normalizar(texto)
    if not plano:
        return False
    if ja_disse(texto) or ler_resposta(texto) is not None or e_cortesia(texto):
        return True
    if "?" in texto or _PERGUNTA_NOVA.search(plano):
        return False
    return len(plano.split()) <= 6


#: Teste do dono (25/10/2026, noite): a RECUSA de contato/atendimento, lida sem o modelo. Depois de um P1
#: falso, "nao quero que ninguem entre em contato", "EU QUERO QUE ENCERRE..." e "nao autorizo" foram lidos
#: como pedido da equipe ("seu pedido ja' esta' com a equipe"). Cada trecho casado e' RECUSA; quem chama
#: confere se sobrou pedido explicito de pessoa fora dele ("..., quero um atendente": o pedido vence).
_RECUSA_DE_CONTATO: Final[re.Pattern[str]] = re.compile(
    # "nao quero que ninguem entre em contato", "nao preciso que me liguem", "nao quero que voces me procurem"
    r"\bnao (?:quero|preciso|precisa|gostaria|queria)(?: mais)?(?: que)?"
    r"(?: (?:ninguem|alguem|voces|vcs|a equipe|ela|eles|elas))?(?: me)?"
    r" (?:entre|entrem|entrar|ligue|liguem|ligar|procure|procurem|procurar|retorne|retornem|retornar"
    r"|contate|contatem|contatar|incomode|incomodem|incomodar)\b(?: em contato)?(?: comigo)?"
    r"|\bninguem (?:precisa |pode |deve )?(?:me )?(?:entrar em contato|ligar|procurar|retornar)\b"
    r"|\bnao (?:quero|preciso)(?: de)?(?: mais)?(?: nenhum| nenhuma)? (?:contato|ligacao|ligacoes|retorno)\b"
    r"|\bnao (?:me )?(?:ligue|liguem|procure|procurem)\b"
    # "para de me mandar mensagem", "chega de mensagem", "me deixa em paz"
    r"|\b(?:para|pare|parem|parar|chega) de (?:me )?(?:mandar|enviar|ligar|incomodar|perturbar)\b"
    r"(?: (?:mensagens?|msg|msgs|isso|nada))?"
    r"|\bchega de (?:mensagens?|msg|ligac\w+)\b"
    r"|\bnao (?:me )?(?:mande|mandem|envie|enviem) mais\b(?: (?:mensagens?|msg|nada))?"
    r"|\bnao quero (?:mais )?receber (?:mensage\w*|msg|ligac\w*|contato)"
    r"|\bme deix(?:a|e|em) em paz\b"
    # "nao autorizo", "nao dou autorizacao", "nao permito" (sozinho ou com o alvo do contato/atendimento)
    r"|\bnao (?:autorizo|autorizei|permito|dou (?:a )?(?:minha )?autorizacao|dou permissao)"
    r"(?=$| (?:nada|isso|ninguem|contato|ligac\w*|o encaminh\w*|encaminh\w*|o atendimento|atendimento"
    r"|a equipe|equipe|voces|que|mais|nao|de jeito)\b)"
)
#: "encerre", "EU QUERO QUE ENCERRE", "pode encerrar isso", "finaliza o atendimento" — sem negacao antes
#: ("nao encerre", "nao quero que encerre" = manter) e sem falar do PLANO (encerrar o plano e' cancelamento,
#: assunto do Lucas).
_PEDE_ENCERRAR: Final[re.Pattern[str]] = re.compile(
    r"\b(?:(?:eu )?(?:quero|queria|preciso) que (?:voce |vc )?|pode |podem |favor |por favor )?"
    r"(?:encerr|finaliz)(?:e|em|a|ar|ando)\b"
    r"(?: (?:o |a |esse |essa |este |esta |meu |minha )?"
    r"(?:atendimento|caso|chamado|conversa|isso|tudo|aqui))?"
    r"|\bfech(?:e|em|a|ar) (?:o |a |esse |essa |este |esta |meu |minha )?"
    r"(?:atendimento|caso|chamado|conversa)\b"
)
_NEGA_ENCERRAR: Final[re.Pattern[str]] = re.compile(r"\bnao (?:\w+ ){0,3}(?:encerr|finaliz|fech)\w*")
#: Fala de OUTRO assunto (plano, cobranca, dados, consulta): "encerrar o plano" e' cancelamento, "para de me
#: mandar boleto" e' cobranca, "nao autorizo o debito" e' do Lucas. Ai' o codigo nao le' recusa: o modelo
#: decide.
_OUTRO_ASSUNTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:plano|contrato|convenio|assinatura|boleto|boletos|fatura|mensalidade|cobranca|debito|pagamento"
    r"|reajuste|cartao|pix|dados|e ?mail|carteirinha|carencia|prazo|horario|expediente|consulta|exame"
    r"|internacao|tratamento|cirurgia|reembolso)\b"
)


def sem_recusa_de_contato(texto: str) -> str:
    """O texto NORMALIZADO sem os trechos de recusa de contato/atendimento ("" quando era so' recusa)."""
    plano = normalizar(texto)
    if _OUTRO_ASSUNTO.search(plano):
        return plano
    sem = _RECUSA_DE_CONTATO.sub(" ", plano)
    if not _NEGA_ENCERRAR.search(plano):
        sem = _PEDE_ENCERRAR.sub(" ", sem)
    return " ".join(sem.split())


def e_recusa_de_contato(texto: str) -> bool:
    """A mensagem RECUSA contato/atendimento? (Sem conferir pedido de pessoa no resto da frase: isso e' de
    `habilidades.atendimento_humano.recusa`.)"""
    return sem_recusa_de_contato(texto) != normalizar(texto)
