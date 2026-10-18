"""SAUDE GERATIVA da Helena (ADR-0065, DL-0119) — atras da chave `MAEZO_HELENA_SAUDE_GERATIVA` + allowlist.

O QUE E'. O modelo Claude Haiku 5.5 REDIGE a resposta a perguntas INFORMATIVAS de saude (o que e', sintomas,
como costuma ser tratado em geral, "pra que serve", exame, especialidade, procedimento, resultado de exame da
pessoa) e a orientacao de uma queixa leve SEM sinal de alarme, sob regras rigidas, e cada texto passa por uma
CHECAGEM antes de sair: as cercas de saida da Helena (injetadas por quem chama: canal, recusa, conduta), os
termos proibidos da base (`base_saude.termos_proibidos_no_texto`) e as regras deste modulo. Reprovou -> UMA
reescrita com o que corrigir; reprovou de novo (ou o modelo falhou) -> a RESPOSTA SEGURA PADRAO (consulta
pelo plano / equipe de saude / 192). Nunca silencio, nunca turno quebrado.

O desenho saiu do experimento de 17/10/2026 (`scripts/experimentos/redacao_saude/`, PR #819), com as falhas
que ele achou corrigidas: fonte citada por PREFIXO; valores de exame ("10,5 g/dL") nao sao dose; "todo ano" na
vacina nao e' conduta; checagem de "nao de/nao use/pare/continue/use o dobro" e dose esquecida; o apoio da
base so' entra quando o assunto casa de verdade (nunca por erro de digitacao: "cistite" achava "manchas na
pele") e nunca o Rol da ANS como "explicacao"; ~80 palavras; sem supor genero; sem pediatra para adulto.

RESIDENCIA (o ponto do ADR-0065). O modelo e' chamado pelo perfil GLOBAL do Bedrock (pode sair do
Brasil), pela
rota PROPRIA `experimento_global_autorizado_dpo_2026-10-17` (`runtime/inference/experimento_global.py`),
autorizada pelo dono COMO DPO SO' para as conversas da allowlist, so' em dev. Por isso a MINIMIZACAO: so' a
pergunta e as ultimas mensagens da pessoa sobre o assunto, com CPF, nome, carteirinha, telefone, e-mail
e datas
mascarados; nada de cadastro, nada das falas da Helena (que podem ter dado do plano).

O QUE NUNCA PASSA POR AQUI: classificacao, alarme/P1 (192), saude mental com risco (CVV 188), identificacao,
plano, carteirinha, cobranca, cancelamento e casos — quem decide e' o grafo, ANTES, como sempre. Mesmo assim
este modulo recusa (devolve `None`) mensagem com risco a vida (defesa em profundidade).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

import structlog

from maezo.conhecimento import base_saude as bs
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.inference.experimento_global import ROTA_DE_RESIDENCIA
from maezo.runtime.prompt_format import render_untrusted_block
from maezo.tools.workers.phi_vars import redact_free_text

logger = structlog.get_logger(__name__)

VERSAO: Final[str] = "helena-saude-gerativa-v1"
#: O que o prompt pede e o teto que a checagem aceita (o experimento pedia 120 e o modelo passava; o dono
#: pediu ~90).
PALAVRAS_PEDIDAS: Final[int] = 70
PALAVRAS_PEDIDAS_SINTOMA: Final[int] = 55
#: Acima disto a checagem pede UMA reescrita mais curta (o Haiku passa do pedido: com 80 pedidas escreveu
#: ~110,
#: medido em 17/10/2026).
PALAVRAS_MAX: Final[int] = 110
#: Tamanho e' ESTILO, nao seguranca: se a reescrita ainda passar de `PALAVRAS_MAX` mas nao de `PALAVRAS_TETO`
#: (e
#: nao tiver nenhuma outra regra quebrada), ela sai — a resposta segura padrao nao resolve a duvida de
#: ninguem.
PALAVRAS_TETO: Final[int] = 150
#: Folgado de proposito: o pensamento do Claude 5.x conta no teto (com 700 o Haiku devolvia texto vazio).
MAX_TOKENS: Final[int] = 2000
#: Teto do turno inteiro (rascunho + reescrita). Passou disso: resposta segura padrao.
PRAZO_TOTAL_S: Final[float] = 30.0
#: Quantas mensagens ANTERIORES da pessoa vao como contexto (as ultimas, do mesmo assunto).
MAX_ANTERIORES: Final[int] = 2

Modo = Literal["informar", "resultado", "sintoma_sem_alarme", "sintoma_contexto"]
MODO_INFORMAR: Final[str] = "informar"
MODO_RESULTADO: Final[str] = "resultado"
#: A pessoa respondeu "nao" as perguntas de alerta.
MODO_SINTOMA_SEM_ALARME: Final[str] = "sintoma_sem_alarme"
#: A pessoa respondeu as perguntas de alerta com CONTEXTO ("comecou hoje, ja usei X e nao melhorou"), sem
#: sinal de alarme e sem "sim": em vez de repetir a pergunta, a Helena orienta e repete os sinais que pedem
#: 192.
MODO_SINTOMA_CONTEXTO: Final[str] = "sintoma_contexto"
_MODOS_DE_SINTOMA: Final[frozenset[str]] = frozenset({MODO_SINTOMA_SEM_ALARME, MODO_SINTOMA_CONTEXTO})

# --- A allowlist -------------------------------------------------------------------------------------------
#: O formato de um `conversation_id` keyed do receptor (`wa:{tenant}:hk1_<hmac-sha256>`).
_ID_DE_CONVERSA: Final[re.Pattern[str]] = re.compile(r"^wa:[a-z0-9][a-z0-9_-]{0,31}:hk1_[0-9a-f]{64}$")


def conversas_autorizadas(bruto: str | Iterable[str] | None) -> frozenset[str]:
    """A allowlist lida da env (`MAEZO_HELENA_SAUDE_GERATIVA_CONVERSAS`: ids separados por virgula/espaco).
    So' entra id no formato keyed; qualquer outra coisa e' DESCARTADA (nunca um curinga, nunca um
    telefone)."""
    if bruto is None:
        return frozenset()
    itens = re.split(r"[\s,;]+", bruto) if isinstance(bruto, str) else list(bruto)
    return frozenset(i.strip() for i in itens if isinstance(i, str) and _ID_DE_CONVERSA.fullmatch(i.strip()))


# --- Minimizacao -------------------------------------------------------------------------------------------
DADO_REMOVIDO: Final[str] = "[dado removido]"
_DATA: Final[re.Pattern[str]] = re.compile(r"(?<!\d)\d{1,2}\s?[/.\-]\s?\d{1,2}\s?[/.\-]\s?\d{2,4}(?!\d)")
_NASCIMENTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nasci|nascid[oa]|nascimento|data de nasc\w*|aniversario|aniversário)\b[^.,;!?\n]{0,40}",
    re.IGNORECASE,
)
_NUMERO_LONGO: Final[re.Pattern[str]] = re.compile(r"(?<!\d)\d(?:[\s.\-/]?\d){7,}(?!\d)")
_IDENTIFICADOR_ROTULADO: Final[re.Pattern[str]] = re.compile(
    r"\b(cpf|rg|cns|carteirinha|carteira|cart[aã]o|matr[ií]cula|telefone|celular|whats(?:app)?|e-?mail)"
    r"\b\s*(?:d[aoe]\s+\w+\s*)?(?:[:=é]|e|eh|n[ºo°.]*)?\s*[\d+(][\d.\-/ ()]{3,}",
    re.IGNORECASE,
)
_NOME_DITO: Final[re.Pattern[str]] = re.compile(
    r"\b((?i:meu nome (?:e|é|eh)|me chamo|aqui (?:e|é|eh) (?:o|a)|sou (?:o|a)|minha (?:mae|mãe|filha|esposa"
    r"|mulher) (?:se chama|e|é)|meu (?:pai|filho|marido|esposo) (?:se chama|e|é)))\s+"
    r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ']+(?:\s+(?:d[aeo]s?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ']+)*"
)
_TOKENS_DO_REDATOR: Final[re.Pattern[str]] = re.compile(r"\[REDACTED_[A-Z]+\]")
_LIMITE_POR_MENSAGEM: Final[int] = 500


def minimizar(texto: str) -> str:
    """O texto que PODE sair para a rota global: CPF/CNPJ, telefone, e-mail e numeros longos pelo redator
    compartilhado do repo (`redact_free_text`, CC-06), mais datas, data de nascimento, identificador rotulado
    ("CPF: ...", "carteirinha 123...") e nome dito ("meu nome e' Fulano") -> `[dado removido]`. Idade e sexo
    ditos pela pessoa ficam (sao uteis: "tenho 45 anos")."""
    t = " ".join((texto or "").split())
    t = _NOME_DITO.sub(lambda m: f"{m.group(1)} {DADO_REMOVIDO}", t)
    t = _IDENTIFICADOR_ROTULADO.sub(lambda m: f"{m.group(1)} {DADO_REMOVIDO} ", t)
    t = _NASCIMENTO.sub(DADO_REMOVIDO, t)
    t = _DATA.sub(DADO_REMOVIDO, t)
    t = redact_free_text(t, max_chars=_LIMITE_POR_MENSAGEM)
    t = _TOKENS_DO_REDATOR.sub(DADO_REMOVIDO, t)
    t = _NUMERO_LONGO.sub(DADO_REMOVIDO, t)
    return " ".join(t.split())


#: Mensagens anteriores que nao sao CONTEXTO DE SAUDE: o fluxo de acesso (aceite, CPF, nascimento), saudacao,
#: "sim/nao", escolha de opcao.
_NAO_E_CONTEXTO: Final[re.Pattern[str]] = re.compile(
    r"^(?:aceito|aceitar|sim|nao|não|ok|oi|ola|olá|bom dia|boa tarde|boa noite|obrigad[oa]|valeu|1|2|3"
    r"|4)\W*$",
    re.IGNORECASE,
)


def contexto_da_pessoa(mensagens: Sequence[str]) -> tuple[str, ...]:
    """As ultimas `MAX_ANTERIORES` mensagens DA PESSOA que sao contexto (nao aceite/CPF/nascimento/saudacao),
    ja' minimizadas, da mais antiga para a mais recente."""
    saida: list[str] = []
    for bruta in reversed(list(mensagens)):
        limpa = minimizar(bruta)
        sem_marca = limpa.replace(DADO_REMOVIDO, " ").strip(" .,;:-")
        if len(sem_marca.split()) < 2 or _NAO_E_CONTEXTO.match(sem_marca):
            continue
        saida.append(limpa)
        if len(saida) >= MAX_ANTERIORES:
            break
    return tuple(reversed(saida))


# --- A pergunta de saude (alem das formas de `base_saude.pergunta_informativa`)
# ------------------------------
_PERGUNTA_DE_SAUDE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:como (?:tratar|trata|se trata|e tratad\w*|eh tratad\w*|cuidar|cuida|prevenir|previne|evitar|evita"
    r"|identificar|saber se|e feit[oa]|eh feit[oa]|e o exame|e a cirurgia)"
    r"|quais? (?:sao |e |eh )?(?:os |as )?(?:sintomas?|sinais|causas?|riscos?|tratamentos?|tipos?)"
    r"|o que (?:causa|provoca|pode causar)|tem cura|e contagios\w*|eh contagios\w*|e hereditari\w*"
    r"|qual (?:medico|especialista|especialidade)|que (?:medico|especialista) (?:cuida|trata|procurar)"
    r"|quem (?:cuida|trata) (?:de|do|da|disso)|precisa de preparo|serve (?:pra|para)|pra que serve"
    r"|para que serve)\b"
)
#: Seguimento curto de uma pergunta de saude anterior ("E osteoporose?", "e sobre a vacina?").
_SEGUIMENTO: Final[re.Pattern[str]] = re.compile(
    r"^(?:e|e sobre|e quanto a|e a|e o|e as|e os|e da|e do|e de|tambem|e tambem)\s+\w+"
)
_MAX_PALAVRAS_DO_SEGUIMENTO: Final[int] = 8


def pergunta_de_saude(plano: str) -> bool:
    """A mensagem NORMALIZADA e' uma pergunta informativa de saude?"""
    return bs.pergunta_informativa(plano) or bool(_PERGUNTA_DE_SAUDE.search(plano))


def e_seguimento(plano: str, anteriores: Sequence[str]) -> bool:
    """ "E osteoporose?" logo depois de "O que e' endometriose?": seguimento curto de pergunta de saude."""
    if len(plano.split()) > _MAX_PALAVRAS_DO_SEGUIMENTO or not _SEGUIMENTO.match(plano):
        return False
    return any(pergunta_de_saude(bs.normalizar(a)) for a in anteriores)


# --- O apoio da base (so' quando o assunto casa DE VERDADE)
# --------------------------------------------------
@dataclass(frozen=True)
class Apoio:
    assunto_id: str
    titulo: str
    categoria: str
    bloco: str
    fonte_nome: str | None = None
    fonte_url: str | None = None


#: Os modos da busca que contam como "casou de verdade": o assunto isolado E' um sinonimo, ou um sinonimo
#: aparece INTEIRO na mensagem. Erro de digitacao/ambiguidade nunca vira apoio.
_BUSCA_CONFIAVEL: Final[frozenset[str]] = frozenset({"exato", "contido"})


def _fonte_que_explica(assunto: bs.Assunto) -> bs.Fonte | None:
    """A primeira fonte de RESPONDER que EXPLICA o assunto (Saude de A a Z, INCA, Calendario de Vacinacao). Os
    atos da ANS (Rol, RN 566) dizem o que o plano cobre, nao o que a coisa e': nunca sao "explicacao"."""
    for fonte in assunto.fontes:
        classe = bs.classificar_fonte(fonte.nome, fonte.url)
        if classe.classe == bs.CLASSE_RESPONDER and not classe.rotulo.startswith("ANS"):
            return fonte
    return None


def apoio_de_sintoma(assunto: bs.Assunto) -> Apoio:
    sinais = ", ".join(assunto.sinais_alarme or ())
    bloco = (
        f"Sinais de alarme que a operadora usa para '{assunto.titulo}' (se a pessoa tiver algum, oriente 192 "
        f"ou pronto-socorro na hora): {sinais}. Caminho sem alarme: {assunto.texto} Não há fonte para citar."
    )
    return Apoio(assunto.id, assunto.titulo, assunto.categoria, bloco)


def apoio_de_sinais(sinais: str) -> Apoio:
    """Apoio so' com os sinais de alarme de um grupo da v4 (sem arquivo `sin-*` na base)."""
    bloco = f"Sinais de alarme (se a pessoa tiver algum, oriente 192 ou pronto-socorro na hora): {sinais}."
    return Apoio("v4", "sintoma", bs.CATEGORIA_SINTOMA, bloco)


def apoio_para(base: bs.BaseSaude, texto: str) -> Apoio | None:
    """O apoio da base para a pergunta, ou `None`. So' com busca CONFIAVEL; `sintoma` traz os sinais de alarme
    (sem citacao); os demais so' com fonte que explica (citada como "Fonte: <nome>")."""
    busca = bs.buscar(base, texto)
    if busca.achado is None or busca.como not in _BUSCA_CONFIAVEL:
        return None
    assunto = base.get(busca.achado)
    if assunto is None:
        return None
    if assunto.categoria == bs.CATEGORIA_SINTOMA:
        return apoio_de_sintoma(assunto)
    fonte = _fonte_que_explica(assunto)
    if fonte is None:
        return None
    nome = fonte.nome.rstrip(". ")
    bloco = f"Assunto: {assunto.titulo}\nTrecho: {assunto.texto}\nFonte: {nome}\nLink: {fonte.url}"
    return Apoio(assunto.id, assunto.titulo, assunto.categoria, bloco, nome, fonte.url)


# --- O prompt ------------------------------------------------------------------------------------------
PROMPT_SISTEMA: Final[str] = f"""\
Você é a Helena, assistente de saúde da operadora de plano de saúde, no WhatsApp.
Responda com as suas próprias palavras à mensagem da pessoa.

COMO ESCREVER
- Português simples, tom acolhedor e direto. No máximo {PALAVRAS_PEDIDAS} palavras e 5 frases curtas.
- Sem títulos e sem markdown (nada de ** ou #). No máximo 3 itens curtos se precisar listar.
- Comece pela resposta. Não repita a pergunta da pessoa e não comece com "Entendi".
- Use o que a pessoa contou (o que já fez, o que já usou, desde quando, idade) e responda a TODAS as \
perguntas da mensagem. Se a mensagem continua uma conversa anterior, entenda pelo contexto.
- Não suponha o gênero da pessoa: escreva de forma neutra ("vale passar por avaliação médica", nunca \
"avaliado/avaliada", "sozinho/sozinha", "preocupado/preocupada", nem "se for mulher").
- Para adulto, nunca indique pediatra. Só cite pediatra se a pessoa falar de criança ou bebê.
- Trechos como [dado removido] foram tirados por privacidade: não comente.

O QUE VOCÊ PODE FAZER
- Explicar o que é (doença, exame, remédio, especialidade, procedimento), sintomas comuns e para que serve.
- Dizer como costuma ser tratado EM GERAL, sempre assim: "o tratamento é definido pelo médico e pode \
envolver…".
- Dizer quando procurar atendimento e qual especialidade costuma cuidar disso.
- Em exame: dizer o que ele mede e a faixa de referência geral, com a unidade do exame (por exemplo \
g/dL ou mg/dL), sem concluir nada sobre a pessoa.

O QUE VOCÊ NUNCA FAZ
- Nunca diagnostica nem sugere o que a pessoa tem ("você tem", "deve ser", "parece ser", "provavelmente é").
- Nunca diz quantidade, miligramas, gotas, número de comprimidos, horário, intervalo ou por quantos dias \
usar um remédio, nem o que fazer com uma dose esquecida. Se pedirem: quem orienta é o médico ou o \
farmacêutico, e a bula traz as orientações.
- Nunca manda usar, não usar, dar, não dar, parar, continuar, trocar, dobrar ou pular remédio, e nunca diz \
que uma combinação de remédios (ou remédio com bebida) está liberada ou proibida: oriente falar com o \
médico ou o farmacêutico.
- Nunca diz que algo "não é grave", "é normal", "não se preocupe", nem que "não há sinais de alarme" ou \
que a pessoa está "sem sinais de alerta".
- Nunca conclui o que o resultado de exame da pessoa significa para ela: explica o exame em geral, diz \
que vários fatores influenciam e orienta levar o resultado ao médico.
- Nunca diz com que frequência a pessoa deve fazer exame, vacina ou consulta.
- Nunca promete cobertura do plano, prazo ou que alguém vai entrar em contato, e não oferece fazer o que \
você não faz (agendar pela pessoa, ver agenda ou disponibilidade).
- Não invente links, telefones nem fontes.
- Evite estas palavras, que o sistema bloqueia: "dose", "dosagem", "posologia", "tome", "tomar", \
"é normal", "você tem", "você está com", "tem cobertura", "anualmente". Em vez de "tomar", diga "usar".

SEGURANÇA (vem antes de tudo)
- Sinal de alarme (dor no peito, falta de ar forte, boca torta, fraqueza de um lado do corpo, fala \
enrolada, desmaio, convulsão, confusão de repente, sangramento forte, dor de cabeça súbita e muito \
forte): oriente "ligue 192 (SAMU) ou vá ao pronto-socorro".
- Se a pessoa falar em se machucar ou em não querer mais viver: acolha, indique o CVV no 188 (gratuito, \
24 horas) e o 192 se houver perigo agora.

APOIO
- Se vier um bloco APOIO com trecho de fonte oficial, use-o para não errar e termine com uma linha \
"Fonte: <nome>", com o nome exatamente como veio. Se o APOIO não for sobre o assunto da pergunta, \
ignore-o e não cite fonte. Sem bloco APOIO com fonte, não escreva "Fonte:" nem links.
"""
PROMPT_VERSAO: Final[str] = f"{VERSAO}-" + hashlib.sha256(PROMPT_SISTEMA.encode("utf-8")).hexdigest()[:10]

_FECHO_DE_OFERTA: Final[str] = (
    "Termine com UMA frase curta oferecendo mostrar como marcar uma consulta pelo plano ou passar para a "
    "nossa equipe de saúde."
)
_SEM_OPCOES: Final[str] = "NÃO ofereça opções no final: o sistema acrescenta as opções."
_INSTRUCAO_DO_MODO: Final[Mapping[str, str]] = {
    MODO_INFORMAR: f"Pergunta geral de saúde. {_FECHO_DE_OFERTA}",
    MODO_RESULTADO: (
        "A pessoa fala de um resultado de exame DELA. Explique o exame em geral, diga que vários fatores "
        f"influenciam, não conclua nada e oriente levar o resultado ao médico. {_FECHO_DE_OFERTA}"
    ),
    MODO_SINTOMA_SEM_ALARME: (
        "Queixa de sintoma. A pessoa respondeu NÃO às perguntas de sinal de alarme. Em até "
        f"{PALAVRAS_PEDIDAS_SINTOMA} palavras: acolha em uma frase usando o que ela contou, diga qual "
        "especialidade costuma avaliar isso e, em uma frase, que se aparecer algum sinal de alarme do APOIO "
        "deve ligar 192 ou ir ao pronto-socorro. Não escreva que 'não há sinais de alarme' nem que não é "
        f"grave. Sem remédio, sem diagnóstico. {_SEM_OPCOES}"
    ),
    MODO_SINTOMA_CONTEXTO: (
        "Queixa de sintoma. A pessoa ainda NÃO respondeu às perguntas de sinal de alarme e trouxe mais "
        f"contexto. Em até {PALAVRAS_PEDIDAS_SINTOMA} palavras: acolha usando o que ela contou (o que já "
        "usou, desde quando), diga que pelo que contou vale passar por avaliação médica e diga claramente "
        "que, se tiver algum sinal de alarme do APOIO, deve ligar 192 ou ir ao pronto-socorro agora. Não "
        f"diga se é grave ou não. Sem remédio, sem diagnóstico. {_SEM_OPCOES}"
    ),
}


@dataclass(frozen=True)
class Pedido:
    """O que vai ao modelo. `mensagem` e `anteriores` JA MINIMIZADOS (`minimizar`/`contexto_da_pessoa`)."""

    modo: str
    mensagem: str
    anteriores: tuple[str, ...] = ()
    apoio: Apoio | None = None
    crianca: bool = False
    #: Os sinais de alarme em palavras (modos de sintoma): entram na resposta segura padrao.
    sinais: str = ""


def mensagem_do_usuario(pedido: Pedido) -> str:
    partes: list[str] = [f"[Tipo]\n{_INSTRUCAO_DO_MODO[pedido.modo]}"]
    partes.append(
        "[Quem]\nA conversa é sobre uma criança."
        if pedido.crianca
        else "[Quem]\nA conversa é sobre um adulto."
    )
    if pedido.anteriores:
        anteriores = "\n".join(f"- {h}" for h in pedido.anteriores)
        partes.append(f"[Mensagens anteriores da pessoa nesta conversa]\n{anteriores}")
    if pedido.apoio is not None:
        partes.append(f"[APOIO]\n{pedido.apoio.bloco}")
    partes.append("[Mensagem da pessoa]\n" + render_untrusted_block("message_body", pedido.mensagem))
    return "\n\n".join(partes)


# --- A checagem antes de enviar
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Violacao:
    regra: str
    trecho: str = ""


#: Valor de exame com a unidade de LABORATORIO ("10,5 g/dL", "130 mg/dL", "6,8 mUI/L"): nao e' quantidade de
#: remedio. Tirado do texto (so' para a checagem de dose) antes das cercas, que leem "16 g" como dose.
_VALOR_DE_EXAME: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w,.])\d+(?:[.,]\d+)?(?:\s?(?:a|e|-|até|ate)\s?\d+(?:[.,]\d+)?)?\s?"
    r"(?:g|mg|mcg|µg|ug|ng|pg|ui|mui|mu|u|meq|mmol|µmol|umol)\s?/\s?(?:dl|l|ml|mm3|mm³|µl|ul|h)\b",
    re.IGNORECASE,
)
#: Frequencia de VACINA ("a vacina da gripe e' feita todo ano / anualmente"): informacao do calendario, nao
#: conduta para a pessoa. So' na frase que fala de vacina/gripe/campanha.
_FREQUENCIA_ANUAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:anualmente|todo ano|todos os anos|uma vez (?:por|ao) ano|a cada ano)\b", re.IGNORECASE
)
_FALA_DE_VACINA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:vacin\w*|gripe|influenza|campanha)\b", re.IGNORECASE
)
_FRASES: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?\n])\s+")


def texto_para_as_cercas(texto: str) -> str:
    """O texto que as cercas de DOSE/CONDUTA leem: sem os valores de exame com unidade de laboratorio e sem a
    frequencia anual nas frases de vacina. O texto ENVIADO nao muda."""
    t = _VALOR_DE_EXAME.sub(" [valor de exame] ", texto)
    frases = _FRASES.split(t)
    return " ".join(_FREQUENCIA_ANUAL.sub(" ", f) if _FALA_DE_VACINA.search(f) else f for f in frases)


_DOSE_NUMERICA: Final[re.Pattern[str]] = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|ml|g|gotas?|comprimidos?|capsulas?|ui)\b"
    r"|\b(?:de|a cada) \d+ (?:em \d+ )?horas?\b|\b\d+ vez(?:es)? (?:ao|por) dia\b"
)
_MANDA_USAR: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tome|tomem)\b|\b(?:voce )?(?:pode|podem|deve|devem) (?:tomar|associar|combinar|misturar)\b"
    r"|\bvoce (?:pode|deve) (?:usar|dar)\b"
    r"|(?<!nao )\b(?:pare|parar|suspenda|suspender|interrompa) (?:de )?(?:tomar|usar|dar|o uso|o remedio"
    r"|o medicamento|o tratamento)\b"
    r"|\b(?:continue|continua|mantenha|siga) (?:usando|tomando|dando|a usar|a tomar|com o remedio"
    r"|com o medicamento|com o tratamento)\b"
)
#: "Nao de remedio para febre", "nao use nada" — conduta (o q090 do experimento). Exceto o "sem orientacao":
#: "nao use nenhum remedio sem falar com o medico" e' o cuidado geral que o prompt pede.
_NAO_DE: Final[re.Pattern[str]] = re.compile(
    r"\bnao (?:de|der|dar|tome|tomar|use|usar|ofereca|oferecer|aplique|aplicar|passe"
    r"|tente)\b(?![^.]{0,60}\bsem "
    r"(?:orientacao|receita|prescricao|falar|conversar|consultar|avaliacao|indicacao))"
)
_DOBRO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:use|tome|de|dobre|duplique)\b (?:o )?(?:dobro|dobrad[oa]|em dobro)\b|\bdobr(?:e|ar) a\b"
)
_DOSE_ESQUECIDA: Final[re.Pattern[str]] = re.compile(
    r"\bassim que (?:se )?lembrar\b|\bpul(?:e|ar) (?:a|o|esse|essa)\b"
    r"|\bno (?:proximo )?horario (?:seguinte|de "
    r"costume|normal)\b|\bna proxima (?:hora|vez) normal\b"
)
_LIBERA_COMBINACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:e|eh) seguro (?:tomar|usar|associar|combinar|misturar)\b|\bnao ha (?:problema|interacao)\b"
    r"|\bnao tem (?:problema|interacao)\b|\bpode(?:m)? ser (?:tomad|usad)[oa]s? junt[oa]s?\b"
    r"|\bsem problemas? (?:em|de|para) (?:tomar|usar|associar)\b|\bpode beber\b|\bpode misturar\b"
)
_MINIMIZA: Final[re.Pattern[str]] = re.compile(
    r"\bnao e (?:nada )?grave\b|\bnao e nada\b|\bnao e preocupante\b|\bfique tranquil[oa]\b"
    r"|\bnao se preocupe\b|\be normal\b|\bnada demais\b"
    # "Febre de 38 em bebe e' comum" (medido em 17/10/2026): o sintoma DA PESSOA dito comum = minimizar.
    r"|\b(?:febre|dor|isso|esse sintoma|essa dor|esse quadro)\b[^.]{0,40}"
    r"\be (?:comum|frequente|esperad[oa])\b"
)
_DIAGNOSTICO: Final[re.Pattern[str]] = re.compile(
    r"(?<!\bse )\bvoce (?:tem|esta com|deve ter|provavelmente tem|tem mesmo)\b"
    r"|\b(?:provavelmente|certamente|com certeza) (?:e|eh|seja|voce)\b|\bparece (?:ser|que voce tem)\b"
    r"|\bseu diagnostico\b|\bdeve ser (?:uma?|o|a) (?:infeccao|virose|gastrite|enxaqueca|sinusite|infarto|avc"
    r"|dengue|ansiedade|depressao|alergia|apendicite|crise)\b"
)
_RISCO_NA_MENSAGEM: Final[re.Pattern[str]] = re.compile(
    r"suicid|\bme matar\b|\bquero morrer\b|\btirar (?:a )?minha (?:propria )?vida\b"
    r"|\bnao (?:quero|aguento) mais viver\b|\bmelhor (?:nao acordar|morrer|sumir)\b|\bnao acordar mais\b"
    r"|\bme cortar\b|\bme cortado\b|\bme cortando\b|\bme machucar\b|\bautolesao\b"
)
#: Adjetivo de genero referido a pessoa ("voce nao esta sozinho", "fico feliz que voce esteja tranquila").
_GENERO: Final[re.Pattern[str]] = re.compile(
    r"\bvoce\b(?:\s+\w+){0,3}?\s+((?:sozinh|preocupad|cansad|assustad|nervos|tranquil|querid|obrigad|"
    r"sentad|deitad|acompanhad|operad|internad|vacinad|gravid)[oa]s?)\b"
    r"|\bse (?:voce )?for (?:mulher|homem)\b"
    # So' quando o sujeito e' a PESSOA ("voce ... ser avaliada", "vale ser avaliada"): "a osteoporose costuma
    # ser acompanhada pelo reumatologista" fala da doenca (falso positivo do aceite de 17/10/2026).
    r"|(?:\bvoce\b[^.,;]{0,30}?|\bvale (?:a pena )?)\b(?:ser|seja|estar|esteja|ficar|fique) "
    r"((?:avaliad|atendid|examinad|acompanhad|internad|operad)a)\b"
)
_PEDIATRA: Final[re.Pattern[str]] = re.compile(r"\bpediatr\w*\b")
_PROMESSA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:vou|irei|ja) (?:agendar|marcar|verificar a agenda|ver a agenda|reservar)\b|\b(?:agendei|marquei)\b"
    r"|\bvao (?:te )?(?:ligar|entrar em contato|retornar)\b|\bentrar(?:emos|a)? em contato\b"
)
_URL: Final[re.Pattern[str]] = re.compile(r"https?://\S+|\bwww\.\S+", re.IGNORECASE)
_LINHA_FONTE: Final[re.Pattern[str]] = re.compile(r"(?im)^\s*fonte\s*:\s*(.+)$")


def fonte_confere(citada: str, esperada: str) -> bool:
    """A linha "Fonte:" confere com a fonte do APOIO por PREFIXO, nos dois sentidos ("Fonte: ANS — Rol..." sem
    o sufixo entre parenteses vale), com um minimo de 12 caracteres (nunca "Fonte: Saude")."""
    c, e = bs.normalizar(citada), bs.normalizar(esperada)
    if not c or not e:
        return False
    return (e.startswith(c) or c.startswith(e)) and len(c) >= min(len(e), 12)


def _palavras(texto: str) -> int:
    return len(_LINHA_FONTE.sub("", texto).split())


_MARKDOWN_NEGRITO: Final[re.Pattern[str]] = re.compile(r"\*\*|__")
_MARKDOWN_TITULO: Final[re.Pattern[str]] = re.compile(r"(?m)^\s*#+\s*")
_LINHAS_EM_BRANCO: Final[re.Pattern[str]] = re.compile(r"\n\s*\n(?:\s*\n)+")


def limpar(texto: str, apoio: Apoio | None) -> str:
    """Conserto DETERMINISTICO, sem mudar conteudo: tira markdown (** e titulos #) e acerta a linha "Fonte:" —
    sem fonte de apoio ela sai; com fonte, vale o nome exato do APOIO (o modelo abrevia ou cita outra)."""
    t = _MARKDOWN_TITULO.sub("", _MARKDOWN_NEGRITO.sub("", texto)).strip()
    if apoio is None or apoio.fonte_nome is None:
        t = _LINHA_FONTE.sub("", t)
    elif _LINHA_FONTE.search(t):
        t = _LINHA_FONTE.sub("", t).rstrip() + f"\nFonte: {apoio.fonte_nome}"
    return _LINHAS_EM_BRANCO.sub("\n\n", t).strip()


#: Regras de ESTILO: sozinhas, nao bloqueiam a reescrita (ver `PALAVRAS_TETO`).
_REGRAS_DE_ESTILO: Final[frozenset[str]] = frozenset({"longa_demais"})


def so_estilo(violacoes: Sequence[Violacao], texto: str) -> bool:
    return (
        bool(violacoes)
        and all(v.regra in _REGRAS_DE_ESTILO for v in violacoes)
        and _palavras(texto) <= PALAVRAS_TETO
    )


def verificar(
    texto: str,
    pedido: Pedido,
    *,
    cercas: Callable[[str], Sequence[str]],
    texto_da_pessoa: str = "",
) -> list[Violacao]:
    """Tudo o que REPROVA o texto antes de enviar. Lista vazia = pode sair. `cercas` = as cercas de saida da
    Helena (injetadas pelo grafo; devolvem os rotulos do que barrou)."""
    if not texto.strip():
        return [Violacao("resposta_vazia")]
    para_cerca = texto_para_as_cercas(texto)
    achados = [Violacao(f"cerca:{r}") for r in cercas(para_cerca)]
    achados += [Violacao(f"base:{r}") for r in bs.termos_proibidos_no_texto(para_cerca)]
    plano = bs.normalizar(para_cerca)
    for nome, padrao in (
        ("dose_numerica", _DOSE_NUMERICA),
        ("manda_usar_ou_parar", _MANDA_USAR),
        ("nao_de_nao_use", _NAO_DE),
        ("use_o_dobro", _DOBRO),
        ("dose_esquecida", _DOSE_ESQUECIDA),
        ("libera_combinacao", _LIBERA_COMBINACAO),
        ("minimiza", _MINIMIZA),
        ("diagnostico", _DIAGNOSTICO),
        ("promessa", _PROMESSA),
    ):
        m = padrao.search(plano)
        if m:
            achados.append(Violacao(nome, m.group(0)))
    pessoa = bs.normalizar(texto_da_pessoa)
    for m in _GENERO.finditer(plano):
        adjetivo = m.group(1) or m.group(2)
        if adjetivo is None or not re.search(rf"\b{re.escape(adjetivo)}\b", pessoa):
            achados.append(Violacao("genero", m.group(0)))
            break
    if not pedido.crianca and _PEDIATRA.search(plano):
        achados.append(Violacao("pediatra_para_adulto"))
    if pedido.modo in _MODOS_DE_SINTOMA and "192" not in texto:
        achados.append(Violacao("sintoma_sem_192"))
    if _palavras(texto) > PALAVRAS_MAX:
        achados.append(Violacao("longa_demais", f"{_palavras(texto)} palavras"))
    if "**" in texto or re.search(r"(?m)^\s*#", texto):
        achados.append(Violacao("markdown"))
    apoio = pedido.apoio
    permitidas = {apoio.fonte_url} if apoio is not None and apoio.fonte_url else set()
    for url in _URL.findall(texto):
        if url.rstrip(".,;)") not in permitidas:
            achados.append(Violacao("link_nao_fornecido", url))
    citou = _LINHA_FONTE.search(texto)
    if citou:
        nome = citou.group(1).strip().rstrip(".")
        if apoio is None or apoio.fonte_nome is None:
            achados.append(Violacao("fonte_sem_apoio", nome))
        elif not fonte_confere(nome, apoio.fonte_nome):
            achados.append(Violacao("fonte_com_nome_errado", nome))
    return achados


_INSTRUCAO: Final[Mapping[str, str]] = {
    "dose_numerica": "tire qualquer quantidade, horario ou intervalo de remedio",
    "manda_usar_ou_parar": "nao mande usar, parar, continuar, dobrar ou trocar remedio",
    "nao_de_nao_use": "nao diga para nao dar/nao usar remedio; oriente falar com o medico ou o farmaceutico",
    "use_o_dobro": "nao fale em usar o dobro; quem orienta e' o medico ou o farmaceutico",
    "dose_esquecida": "nao diga o que fazer com dose esquecida; oriente o medico ou o farmaceutico e a bula",
    "libera_combinacao": "nao diga que a combinacao e' segura ou liberada; oriente o medico ou o "
    "farmaceutico",
    "minimiza": "nao minimize ('nao e' grave', 'fique tranquilo', 'e' normal')",
    "diagnostico": "nao diga o que a pessoa tem ('voce tem', 'voce esta com', 'deve ser')",
    "promessa": "nao prometa agendar, marcar nem que alguem vai entrar em contato",
    "genero": "escreva sem adjetivo de genero para a pessoa (troque 'avaliado/avaliada', 'sozinho/sozinha', "
    "'tranquilo/tranquila' por formas neutras, como 'vale passar por avaliacao medica')",
    "pediatra_para_adulto": "a conversa e' sobre um adulto: tire 'pediatra' (clinico geral ou medico de "
    "familia)",
    "sintoma_sem_192": "diga, em uma frase, que se aparecer um sinal de alarme do APOIO deve ligar 192",
    "longa_demais": f"encurte para no maximo {PALAVRAS_PEDIDAS} palavras",
    "markdown": "tire o markdown (** e #)",
    "link_nao_fornecido": "tire o link: so' vale o link do APOIO",
    "fonte_sem_apoio": "tire a linha 'Fonte:' (nao houve fonte de apoio)",
    "fonte_com_nome_errado": "a linha 'Fonte:' tem de trazer o nome exatamente como veio no APOIO",
    "resposta_vazia": "escreva a resposta",
    "base:dose/posologia": "NAO use as palavras dose/dosagem/posologia em nenhuma forma, nem 'dose "
    "esquecida' (diga 'quanto e como usar' ou 'o remedio esquecido')",
    "base:quantidade de medicamento": "tire qualquer quantidade de remedio (mg, ml, gotas, comprimidos)",
    "base:frequencia de uso": "tire qualquer horario ou intervalo de uso",
    "base:'tome'": "tire 'tome': nao mande usar remedio",
    "base:'pode tomar'": "NAO use o verbo 'tomar' em nenhuma forma, nem repetindo a pergunta (use 'usar'), e "
    "nao diga que a pessoa pode ou deve usar algo",
    "base:'nao e grave'": "nao diga que nao e' grave",
    "base:'e normal'": "tire 'e' normal'",
    "base:minimizacao": "nao minimize ('nao se preocupe', 'nao precisa ir ao medico')",
    "base:diagnostico": "nao diga o que a pessoa tem",
    "base:promessa de cobertura": "nao fale de cobertura do plano",
    "cerca:negativa_clinica": "nao diga que 'nao ha sinais de alarme/alerta' nem que nao e' grave; diga so' "
    "o que fazer SE aparecer um sinal de alarme",
    "cerca:conduta_clinica": "tire frequencia de exame/remedio ('a cada X', 'X vezes ao dia', 'anualmente', "
    "'recomendo fazer')",
}


def instrucao_de_reescrita(violacoes: Sequence[Violacao]) -> str:
    linhas = []
    for v in violacoes:
        dica = _INSTRUCAO.get(v.regra) or (
            "tire o trecho que a cerca de saida barrou (canal, promessa ou conduta)"
            if v.regra.startswith("cerca:")
            else f"corrija: {v.regra}"
        )
        trecho = f' (trecho: "{v.trecho}")' if v.trecho else ""
        linhas.append(f"- {dica}{trecho}")
    return (
        "A verificação automática REPROVOU a sua resposta. Reescreva a resposta INTEIRA, seguindo todas as "
        "regras, e corrija:\n" + "\n".join(dict.fromkeys(linhas)) + "\nResponda só com o novo texto."
    )


# --- As respostas seguras padrao e o fecho deterministico dos modos de sintoma -----------------------------
RESPOSTA_SEGURA_INFORMAR: Final[str] = (
    "Não consigo te responder isso com segurança por aqui. Quem pode te orientar melhor é um médico. "
    "Se quiser, te mostro como marcar uma consulta pelo plano ou te passo para a nossa equipe de saúde. "
    "Em caso de dor no peito, falta de ar ou outro sinal de alarme, ligue 192."
)
RESPOSTA_SEGURA_RESULTADO: Final[str] = (
    "Não consigo avaliar o seu resultado por aqui: quem pode interpretar é o seu médico, então leve o "
    "resultado a ele. Se quiser, te mostro como marcar uma consulta pelo plano ou te passo para a nossa "
    "equipe de saúde."
)
_SINAIS_PADRAO: Final[str] = "dor no peito, falta de ar, desmaio, boca torta ou fraqueza de um lado do corpo"


def resposta_segura_de_sintoma(sinais: str) -> str:
    return (
        "Eu não avalio sintomas por aqui, mas te ajudo com o caminho: pelo que você contou, vale passar por "
        f"avaliação médica. Se aparecer algum sinal de alarme, como {sinais or _SINAIS_PADRAO}, ligue 192 "
        "(SAMU) ou vá ao pronto-socorro na hora."
    )


def opcoes_do_sintoma(crianca: bool) -> str:
    """O fecho FIXO dos modos de sintoma: as mesmas opcoes 1/2 da v4 (a escolha seguinte e' deterministica),
    com a especialidade da POPULACAO (pediatra so' para crianca)."""
    especialidade = "pediatra" if crianca else "clínico geral"
    return (
        f"Você prefere: 1) saber onde marcar consulta pelo plano ({especialidade}: até 7 dias úteis pela "
        "ANS); ou 2) falar com a nossa equipe de saúde?"
    )


def resposta_segura(pedido: Pedido) -> str:
    if pedido.modo == MODO_RESULTADO:
        return RESPOSTA_SEGURA_RESULTADO
    if pedido.modo in _MODOS_DE_SINTOMA:
        return resposta_segura_de_sintoma(pedido.sinais)
    return RESPOSTA_SEGURA_INFORMAR


# --- As perguntas de ALERTA, curtas (so' na allowlist) -----------------------------------------------------
#: Pergunta que so' faz sentido na gestacao ("Voce esta' gravida e...?"): sai quando a conversa nao fala de
#: gravidez (o dono, homem, recebeu a pergunta de gravidez no teste de 17/10/2026).
_PERGUNTA_DE_GESTACAO: Final[re.Pattern[str]] = re.compile(r"\b(?:gravid\w*|gestante|gestacao|gravidez)\b")
_GESTACAO_NO_CONTEXTO: Final[re.Pattern[str]] = _PERGUNTA_DE_GESTACAO
_MAX_PERGUNTAS_CURTAS: Final[int] = 2
_ABERTURA_ALERTA: Final[str] = "Antes de te orientar, uma pergunta rápida (responda sim ou não):"
_ABERTURA_ALERTA_REPETIDA: Final[str] = "Só preciso de um sim ou não:"


def pergunta_de_alerta_curta(perguntas: Sequence[str], *, repetida: bool, contexto: str) -> str:
    """As MESMAS perguntas de alerta (texto fixo, sem modelo), sem o eco truncado do relato ("Entendi: comecou
    hoje... e nao"), sem a de gravidez fora de contexto de gravidez e no maximo duas."""
    gestacao = bool(_GESTACAO_NO_CONTEXTO.search(bs.normalizar(contexto)))
    escolhidas = [
        p for p in perguntas if gestacao or not _PERGUNTA_DE_GESTACAO.search(bs.normalizar(p))
    ] or list(perguntas)
    corpo = " ".join(escolhidas[:_MAX_PERGUNTAS_CURTAS])
    abertura = _ABERTURA_ALERTA_REPETIDA if repetida else _ABERTURA_ALERTA
    return f"{abertura} {corpo}"


#: Mais CONTEXTO da queixa na resposta as perguntas de alerta (desde quando, o que ja' usou, se melhorou).
_CONTEXTO_DA_QUEIXA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:comecou|comeco|desde|faz|ha \d+|hoje|ontem|semana|dias?|horas?|manha|tarde|noite|tomei|usei"
    r"|tomando"
    r"|usando|remedio|remedios|melhor\w*|pior\w*|passou|passa|continua|voltou|sempre|vez(?:es)?)\b"
)
_MIN_PALAVRAS_DO_CONTEXTO: Final[int] = 4


def traz_contexto_da_queixa(plano: str) -> bool:
    """A resposta as perguntas de alerta nao e' sim/nao mas traz contexto da queixa (normalizada)."""
    return len(plano.split()) >= _MIN_PALAVRAS_DO_CONTEXTO and bool(_CONTEXTO_DA_QUEIXA.search(plano))


# --- Higiene do texto final
# ----------------------------------------------------------------------------------
_ABERTURA_REPETIDA: Final[re.Pattern[str]] = re.compile(
    r"^(entendi|certo|ok|claro|oi|ola|olá)[.!,:]?\s+(?=\1\b)", re.IGNORECASE
)


def sem_abertura_repetida(texto: str) -> str:
    """ "Entendi. Entendi: ..." / "Oi! Oi!" -> uma vez so' (a ultima, com a pontuacao dela)."""
    t = texto.strip()
    while True:
        novo = _ABERTURA_REPETIDA.sub("", t, count=1)
        if novo == t:
            return t
        t = novo


# --- O redator
# -----------------------------------------------------------------------------------------------
class TransporteDeRedacao(Protocol):
    @property
    def rota(self) -> str: ...

    @property
    def modelo(self) -> str: ...

    async def conversar(
        self, sistema: str, mensagens: Sequence[Mapping[str, Any]], *, max_tokens: int
    ) -> Any: ...


@dataclass(frozen=True)
class Redacao:
    texto: str
    #: primeira | reescrita | reescrita_longa / primeira_longa (so' o tamanho passou do pedido) | padrao
    #: (checagem reprovou 2x) | falha (modelo falhou: resposta segura)
    desfecho: str
    regras: tuple[str, ...] = ()
    tentativas: int = 0
    apoio: str | None = None
    prompt_versao: str = PROMPT_VERSAO


@dataclass
class RedatorDeSaude:
    """A composicao (receptor) constroi UM por processo, so' com a chave ligada; o grafo pergunta
    `autorizada(conversation_id)` antes de qualquer coisa."""

    transporte: TransporteDeRedacao
    conversas: frozenset[str] = field(default_factory=frozenset)
    prazo_total_s: float = PRAZO_TOTAL_S

    @property
    def rota(self) -> str:
        return ROTA_DE_RESIDENCIA

    def autorizada(self, conversation_id: object) -> bool:
        return isinstance(conversation_id, str) and conversation_id in self.conversas

    async def redigir(
        self,
        pedido: Pedido,
        *,
        cercas: Callable[[str], Sequence[str]],
    ) -> Redacao | None:
        """A resposta checada, ou `None` quando a mensagem NAO pode ir a rota global (risco a vida: quem chama
        segue o caminho de sempre). Falha do modelo ou checagem reprovada duas vezes = resposta segura."""
        contexto = " ".join((*pedido.anteriores, pedido.mensagem))
        if _RISCO_NA_MENSAGEM.search(bs.normalizar(contexto)):
            logger.warning("helena_saude_gerativa_recusada", motivo="risco", rota=ROTA_DE_RESIDENCIA)
            return None
        inicio = time.monotonic()
        try:
            redacao = await asyncio.wait_for(self._redigir(pedido, cercas), timeout=self.prazo_total_s)
        except PROGRAMMING_ERRORS:
            raise
        except TimeoutError:
            redacao = Redacao(
                resposta_segura(pedido), "falha", ("prazo_total",), 0, self._id_do_apoio(pedido)
            )
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            codigo = str(getattr(exc, "codigo", type(exc).__name__))
            redacao = Redacao(resposta_segura(pedido), "falha", (codigo,), 1, self._id_do_apoio(pedido))
        logger.info(
            "helena_saude_gerativa_redigida",
            rota=ROTA_DE_RESIDENCIA,
            modelo=self.transporte.modelo,
            prompt_versao=PROMPT_VERSAO,
            modo=pedido.modo,
            desfecho=redacao.desfecho,
            regras=list(redacao.regras),
            tentativas=redacao.tentativas,
            apoio=redacao.apoio,
            latencia_ms=int((time.monotonic() - inicio) * 1000),
        )
        return redacao

    @staticmethod
    def _id_do_apoio(pedido: Pedido) -> str | None:
        return pedido.apoio.assunto_id if pedido.apoio is not None else None

    async def _redigir(self, pedido: Pedido, cercas: Callable[[str], Sequence[str]]) -> Redacao:
        apoio = self._id_do_apoio(pedido)
        pessoa = " ".join((*pedido.anteriores, pedido.mensagem))
        mensagens: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": mensagem_do_usuario(pedido)}]}
        ]
        r1 = await self.transporte.conversar(PROMPT_SISTEMA, mensagens, max_tokens=MAX_TOKENS)
        t1 = limpar(sem_abertura_repetida(str(getattr(r1, "texto", "") or "")), pedido.apoio)
        v1 = verificar(t1, pedido, cercas=cercas, texto_da_pessoa=pessoa)
        if not v1:
            return Redacao(t1, "primeira", (), 1, apoio)
        mensagens2 = [
            *mensagens,
            {"role": "assistant", "content": [{"text": t1 or "(vazio)"}]},
            {"role": "user", "content": [{"text": instrucao_de_reescrita(v1)}]},
        ]
        regras1 = tuple(dict.fromkeys(v.regra for v in v1))
        try:
            r2 = await self.transporte.conversar(PROMPT_SISTEMA, mensagens2, max_tokens=MAX_TOKENS)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            codigo = str(getattr(exc, "codigo", type(exc).__name__))
            if so_estilo(v1, t1):
                return Redacao(t1, "primeira_longa", (*regras1, f"reescrita:{codigo}"), 2, apoio)
            return Redacao(resposta_segura(pedido), "padrao", (*regras1, f"reescrita:{codigo}"), 2, apoio)
        t2 = limpar(sem_abertura_repetida(str(getattr(r2, "texto", "") or "")), pedido.apoio)
        v2 = verificar(t2, pedido, cercas=cercas, texto_da_pessoa=pessoa)
        if not v2:
            return Redacao(t2, "reescrita", regras1, 2, apoio)
        regras2 = tuple(f"reescrita:{r}" for r in dict.fromkeys(v.regra for v in v2))
        # So' o TAMANHO passou do pedido (nenhuma regra de seguranca): sai o texto, nao a resposta segura.
        if so_estilo(v2, t2):
            return Redacao(t2, "reescrita_longa", (*regras1, *regras2), 2, apoio)
        if so_estilo(v1, t1):
            return Redacao(t1, "primeira_longa", (*regras1, *regras2), 2, apoio)
        return Redacao(resposta_segura(pedido), "padrao", (*regras1, *regras2), 2, apoio)


__all__ = [
    "DADO_REMOVIDO",
    "MAX_ANTERIORES",
    "MODO_INFORMAR",
    "MODO_RESULTADO",
    "MODO_SINTOMA_CONTEXTO",
    "MODO_SINTOMA_SEM_ALARME",
    "PALAVRAS_MAX",
    "PALAVRAS_TETO",
    "PROMPT_SISTEMA",
    "PROMPT_VERSAO",
    "RESPOSTA_SEGURA_INFORMAR",
    "RESPOSTA_SEGURA_RESULTADO",
    "VERSAO",
    "Apoio",
    "Pedido",
    "Redacao",
    "RedatorDeSaude",
    "TransporteDeRedacao",
    "Violacao",
    "apoio_de_sinais",
    "apoio_de_sintoma",
    "apoio_para",
    "contexto_da_pessoa",
    "conversas_autorizadas",
    "e_seguimento",
    "fonte_confere",
    "limpar",
    "so_estilo",
    "instrucao_de_reescrita",
    "mensagem_do_usuario",
    "minimizar",
    "opcoes_do_sintoma",
    "pergunta_de_alerta_curta",
    "pergunta_de_saude",
    "traz_contexto_da_queixa",
    "resposta_segura",
    "resposta_segura_de_sintoma",
    "sem_abertura_repetida",
    "texto_para_as_cercas",
    "verificar",
]
