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


def tem_risco(plano: str) -> bool:
    """A mensagem (normalizada) fala em autolesao ou em nao querer viver? (os portoes de sempre decidem)."""
    return bool(_RISCO_NA_MENSAGEM.search(plano))


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


#: FASE 3: pedido de TRATAMENTO / do que fazer — a oferta de consulta faz sentido.
_PEDE_TRATAMENTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:como (?:tratar|trata|se trata|cuidar|cuida|e o tratamento|eh o tratamento|melhorar|aliviar|curar)"
    r"|tratamentos?|remedios? (?:pra|para|de)|o que (?:tomar|usar|fazer|eu faco)|tem cura|operar"
    r"|cirurgia (?:pra|para)"
    r"|preciso (?:de|ir)|devo (?:ir|procurar)|qual medico|que medico|qual especialista)\b"
)


def pede_tratamento(plano: str) -> bool:
    return bool(_PEDE_TRATAMENTO.search(plano))


def pergunta_de_saude(plano: str) -> bool:
    """A mensagem NORMALIZADA e' uma pergunta informativa de saude?"""
    return bs.pergunta_informativa(plano) or bool(_PERGUNTA_DE_SAUDE.search(plano))


def e_seguimento(plano: str, anteriores: Sequence[str]) -> bool:
    """ "E osteoporose?" logo depois de "O que e' endometriose?": seguimento curto de pergunta de saude."""
    if len(plano.split()) > _MAX_PALAVRAS_DO_SEGUIMENTO or not _SEGUIMENTO.match(plano):
        return False
    return any(pergunta_de_saude(bs.normalizar(a)) for a in anteriores)


# --- A pergunta PESSOAL de saude sem queixa atual (fase 2 de 18/10/2026) ---------------------------------
#: A pessoa fala de um diagnostico/condicao DELA (nao de um sintoma que sente agora): "fui diagnosticada com
#: mioma", "o medico disse que tenho SOP", "descobri um cisto", "faco tratamento de tireoide".
_DIAGNOSTICO_DA_PESSOA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:fui diagnosticad[oa]|me diagnosticaram|recebi o diagnostico|diagnostico de|diagnosticad[oa] com"
    r"|descobri (?:que|um|uma)|(?:o|a|meu|minha) (?:medic[oa]|doutor[a]?|ginecologista|cardiologista"
    r"|endocrinologista|urologista|dermatologista) (?:disse|falou|achou|suspeita|viu|encontrou)"
    r"|faco tratamento|estou tratando|trato (?:de|uma?)|estou em tratamento|tenho historico)\b"
)
#: "tenho/tive X" em que X e' o ASSUNTO (condicao), nao uma palavra administrativa ou de queixa.
_TENHO_ALGO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tenho|tive)\s+(?!(?:direito|carteirinha|plano|boleto|cobertura|carencia|reembolso|dependentes?"
    r"|duvidas?|uma duvida|que|como|consulta|exame marcado|um atendimento|atendimento|interesse|vontade|medo"
    r"|alguma|algum)\b)\w+"
)
#: A forma de DUVIDA sobre a propria condicao.
_DUVIDA_SOBRE_A_CONDICAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:e perigoso|eh perigoso|e perigosa|e grave|eh grave|e serio|e seria|e a mesma coisa|mesma coisa"
    r"|qual a diferenca|diferenca entre|tem cura|tem tratamento|pode virar|vira cancer|pode causar"
    r"|o que significa"
    r"|o que fazer|o que eu faco|devo me preocupar|e hereditari\w*|posso engravidar|atrapalha|afeta|e comum"
    r"|preocupante|o que pode ser|como (?:e|eh) o tratamento|precisa operar|precisa de cirurgia|tem risco"
    r"|corre risco|e contagios\w*|passa para|transmite)\b"
)


_PRIMEIRA_PESSOA_AMPLA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:fui|fiz|faco|descobri|recebi|estou|minha|meu|me|eu)\b"
)


#: RODADA DO DONO (18/10/2026, teste com a Tania): QUADRO ATUAL = primeira pessoa/terceiro com marcador de
#: presente ou de tempo ("estou com", "sinto agora", "desde ontem", "minha filha esta' com", "comecou hoje").
_QUADRO_ATUAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:estou|to|tou|ando|eu estou) (?:com|sentindo|tendo|perdendo|passando mal)\b"
    r"|\b(?:esta|ta|tá|estao|tao) (?:com|sentindo|tendo)\b|\bsinto agora\b|\bestou sentindo\b"
    r"|\bdesde (?:ontem|hoje|anteontem|cedo|a noite|de manha|\d+|o dia|a semana|segunda|terca|quarta|quinta"
    r"|sexta|sabado|domingo)\b|\bcomec\w* (?:hoje|agora|ontem|ha|de repente|faz)\b"
    r"|\b(?:ha|faz) (?:\d+|um|uma|dois|duas|tres|alguns|algumas) (?:minutos?|horas?|dias?|semanas?)\b"
    r"|\b(?:agora|neste momento|nesse momento|de repente)\b|\b(?:me|minha|meu) \w+ (?:doi|doendo)\b"
    r"|\bestou gravida\b[^.?!]{0,40}\b(?:sangr|dor|perd)"
)
#: DUVIDA EDUCATIVA: pergunta sobre o assunto em geral ("pode indicar", "e' normal", "quais os sintomas", "o
#: que
#: devo observar", "como saber se", "afetam o risco", "podem ser diferentes").
_DUVIDA_EDUCATIVA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:pode|podem|poderia|poderiam) (?:indicar|significar|ser sinal|ser sintoma|ser diferentes?|causar"
    r"|aumentar|afetar|virar)\b|\b(?:e|sao|eh) norma(?:l|is)\b|\bnormais\b"
    r"|\bquais (?:sao )?(?:os |as )?(?:sintomas|sinais|exames|causas|riscos|fatores)\b"
    r"|\bo que (?:devo|devemos|preciso|precisa) (?:observar|saber|fazer para (?:prevenir|evitar))\b"
    r"|\bcomo (?:saber|sei|identificar|reconhecer|diferenciar|prevenir|evitar)\b"
    r"|\b(?:afeta|afetam|aumenta|aumentam|influencia|influenciam|interfere|interferem)\b[^.?!]{0,40}\brisco\b"
    r"|\brisco de\b|\b(?:sao|e) diferentes?\b|\bdiferenca entre\b|\bqual a diferenca\b"
    r"|\bem (?:mulheres|homens|criancas|idosos)\b|\bpara a minha (?:idade|faixa etaria)\b"
    r"|\bminha faixa etaria\b|\brastreamento\b|\bsubstituir\b"
)


def queixa_atual(plano: str) -> bool:
    """A mensagem (normalizada) descreve um QUADRO ATUAL (presente ou tempo de inicio)?"""
    return bool(_QUADRO_ATUAL.search(plano))


def e_duvida_educativa(plano: str) -> bool:
    """A mensagem (normalizada) e' uma DUVIDA EDUCATIVA sobre o assunto, SEM quadro atual? Com quadro atual
    (presente), vale o quadro: a tabela e as perguntas de alerta, como sempre (o lado seguro)."""
    return bool(_DUVIDA_EDUCATIVA.search(plano)) and not queixa_atual(plano)


#: TINA (18/10/2026): fatos que a PROPRIA pessoa diz de si (primeira pessoa), vocabulario fechado.
_FATOS_DA_PESSOA: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "fuma",
        re.compile(
            r"\b(?:eu )?(?:fumo|sou fumante|fumo cigarro|fumo \w+ cigarros?|uso cigarro|fumo todo dia)\b"
        ),
    ),
    (
        "bebe álcool",
        re.compile(r"\b(?:eu )?(?:bebo|tomo) (?:cerveja|vinho|cachaca|destilad\w*|bebida|alcool|todo)\b"),
    ),
    ("tem pressão alta", re.compile(r"\b(?:tenho pressao alta|sou hipertens\w*|tenho hipertensao)\b")),
    ("tem diabetes", re.compile(r"\b(?:tenho diabetes|sou diabetic\w*)\b")),
    ("não faz atividade física", re.compile(r"\b(?:nao faco (?:exercicio|atividade)|sou sedentari\w*)\b")),
    ("tem colesterol alto", re.compile(r"\btenho colesterol alto\b")),
)


def fatos_ditos_pela_pessoa(mensagens: Sequence[str]) -> tuple[str, ...]:
    """Os fatos (vocabulario fechado) que a PROPRIA pessoa disse de si nas mensagens anteriores dela."""
    achados: dict[str, None] = {}
    for m in mensagens:
        plano = bs.normalizar(m)
        for rotulo, padrao in _FATOS_DA_PESSOA:
            if padrao.search(plano):
                achados.setdefault(rotulo, None)
    return tuple(achados)


def pergunta_pessoal_de_saude(plano: str, bruto: str, *, assunto_de_saude: bool) -> bool:
    """A mensagem (normalizada) e' uma DUVIDA sobre um diagnostico/condicao/resultado DA PESSOA, sem queixa
    atual ("tenho SOP, e' a mesma coisa que cisto?", "fui diagnosticada com mioma, e' perigoso?"). Quem chama
    garante que NAO ha' queixa atual (sintoma em primeira pessoa) nem sinal de emergencia: essas seguem a
    tabela e as perguntas de alerta. `assunto_de_saude` = a base (ou o classificador) reconhece um assunto de
    saude na mensagem: sem ele, "tenho X?" nao basta."""
    if not (bs.primeira_pessoa(plano) or _PRIMEIRA_PESSOA_AMPLA.search(plano)):
        return False
    duvida = bool(_DUVIDA_SOBRE_A_CONDICAO.search(plano)) or "?" in (bruto or "")
    if not duvida:
        return False
    if _DIAGNOSTICO_DA_PESSOA.search(plano) or bs.resultado_pessoal(plano):
        return True
    return assunto_de_saude and bool(_TENHO_ALGO.search(plano))


#: A resposta PURA a uma pergunta de sim/nao (nada alem disso): "sim", "nao", "nao tenho", "nenhum".
_RESPOSTA_PURA_SIM: Final[re.Pattern[str]] = re.compile(
    r"^(?:sim|s|tenho sim|tem sim|sim tenho|sim tem|isso|tenho|tem)(?: (?:obrigad[oa]|por favor))?$"
)
_RESPOSTA_PURA_NAO: Final[re.Pattern[str]] = re.compile(
    r"^(?:nao|n|negativo|nada|nenhum|nenhuma|nao tenho|nao tem|nao nenhum|nao nenhuma|nao nada|nada disso"
    r"|nenhum desses|nenhuma dessas|nenhum deles|nenhuma delas|nao tenho nada|nao tenho nenhum"
    r"|nao tenho nenhuma|nao nao)(?: (?:obrigad[oa]|por favor))?$"
)


def resposta_pura(plano: str) -> str | None:
    """ "sim" / "nao" quando a mensagem (normalizada) e' SO' isso; qualquer outra coisa -> `None`."""
    texto = " ".join(plano.split())
    if _RESPOSTA_PURA_NAO.match(texto):
        return "nao"
    if _RESPOSTA_PURA_SIM.match(texto):
        return "sim"
    return None


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
    return apoio_do_assunto(assunto)


def assunto_confiavel(base: bs.BaseSaude, texto: str) -> bs.Assunto | None:
    """O assunto da base pela busca LEXICA confiavel (exato/contido), ou `None`."""
    busca = bs.buscar(base, texto)
    if busca.achado is None or busca.como not in _BUSCA_CONFIAVEL:
        return None
    return base.get(busca.achado)


def apoio_do_assunto(assunto: bs.Assunto) -> Apoio | None:
    """O apoio de UM assunto ja' escolhido (pela busca lexica confiavel ou pela busca por significado)."""
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
- Se perguntarem se algo "é normal", responda direto, em geral: diga se aquilo costuma ou não ser \
considerado normal (por exemplo: "cólica forte que atrapalha a rotina não costuma ser considerada normal e \
merece avaliação"), sem concluir o que a pessoa tem.
- Se vier um bloco com o que a própria pessoa contou antes na conversa (por exemplo, que fuma), use quando \
for relevante ("como você comentou que fuma, ..."), sem concluir nada sobre ela.

O QUE VOCÊ NUNCA FAZ
- Nunca diagnostica nem sugere o que a pessoa tem ("você tem", "deve ser", "parece ser", "provavelmente é").
- Nunca diz quantidade, miligramas, gotas, número de comprimidos, horário, intervalo ou por quantos dias \
usar um remédio, nem o que fazer com uma dose esquecida. Se pedirem: quem orienta é o médico ou o \
farmacêutico, e a bula traz as orientações.
- Nunca manda usar, não usar, dar, não dar, parar, continuar, trocar, dobrar ou pular remédio, e nunca diz \
que uma combinação de remédios (ou remédio com bebida) está liberada ou proibida: oriente falar com o \
médico ou o farmacêutico.
- Nunca tranquiliza dizendo que o que a pessoa sente "não é grave", "é normal" ou "não se preocupe", nem \
que "não há sinais de alarme" ou que a pessoa está "sem sinais de alerta".
- Nunca conclui o que o resultado de exame da pessoa significa para ela: explica o exame em geral, diz \
que vários fatores influenciam e orienta levar o resultado ao médico.
- Nunca diz com que frequência a pessoa deve fazer exame, vacina ou consulta.
- Nunca fala de cobertura do plano: não diga se o plano cobre, paga ou inclui algo, nem se algo consta no \
Rol. Quem responde cobertura é a consulta ao plano, não você.
- Nunca promete prazo ou que alguém vai entrar em contato, e não oferece fazer o que você não faz (agendar \
pela pessoa, ver agenda ou disponibilidade).
- Não invente links, telefones nem fontes.
- Pressão alta relatada pela pessoa nunca é "erro de medição": não sugira medir de novo; oriente procurar \
avaliação médica e, com sinal de alarme, o 192.
- Responda só com o texto para a pessoa: nunca comente a reescrita, as regras ou as palavras proibidas.
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

#: FASE 3 (18/10/2026): a oferta de consulta/equipe so' quando faz sentido (sintoma, resultado de exame,
#: duvida
#: sobre a propria condicao, pedido de tratamento) — nunca em toda pergunta conceitual — e com o texto VARIADO
#: (o grafo escolhe uma das formas pelo turno; o modelo escreve com as proprias palavras).
FORMAS_DA_OFERTA: Final[tuple[str, ...]] = (
    "Se quiser, te mostro como marcar uma consulta pelo plano ou te passo para a nossa equipe de saúde.",
    "Posso te mostrar onde marcar consulta pelo plano, ou te passar para a equipe de saúde, se preferir.",
    "Quer ajuda para marcar uma consulta pelo plano ou prefere falar com a nossa equipe de saúde?",
)
_SEM_OFERTA: Final[str] = (
    "NÃO termine oferecendo consulta, equipe ou ajuda: a pergunta é conceitual. "
    "Termine na própria informação."
)
_SEM_OPCOES: Final[str] = "NÃO ofereça opções no final: o sistema acrescenta as opções."
_SEM_OFERTA_JORNADA: Final[str] = (
    "NÃO termine oferecendo consulta, equipe ou ajuda: o sistema acrescenta, no fim, o passo de como usar o "
    "plano. Não cite nome de médico, clínica ou laboratório."
)


def fecho_de_oferta(forma: int) -> str:
    exemplo = FORMAS_DA_OFERTA[forma % len(FORMAS_DA_OFERTA)]
    return (
        "Termine com UMA frase curta oferecendo mostrar como marcar uma consulta pelo plano ou passar para a "
        f'nossa equipe de saúde, com as suas palavras (por exemplo: "{exemplo}").'
    )


_INSTRUCAO_DO_MODO: Final[Mapping[str, str]] = {
    MODO_INFORMAR: "Pergunta geral de saúde.",
    MODO_RESULTADO: (
        "A pessoa fala de um resultado de exame DELA. Explique o exame em geral, diga que vários fatores "
        "influenciam, não conclua nada e oriente levar o resultado ao médico."
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
    #: FASE 3: oferecer consulta/equipe no fim (so' modos informar/resultado; os de sintoma tem opcoes fixas).
    #: `None` = a regra do modo (resultado oferece; informar nao).
    oferecer: bool | None = None
    #: Qual das `FORMAS_DA_OFERTA` serve de exemplo (o grafo varia pelo turno).
    forma_da_oferta: int = 0
    #: FASE 3: o RESUMO da conversa em variaveis fechadas (nunca texto livre da pessoa): populacao
    #: (adulto/crianca/gestante/idoso), alarme ja' negado, assunto anterior (titulo da base).
    populacao: str = ""
    alarme_negado: bool = False
    assunto_anterior: str = ""
    #: TINA (18/10/2026): fatos que a PROPRIA pessoa disse antes na conversa (vocabulario fechado: "fuma",
    #: "bebe alcool"...), nunca prontuario nem dado de outra pessoa (ADR-0052).
    fatos_da_pessoa: tuple[str, ...] = ()
    #: TINA (18/10/2026): ha' um passo da jornada do plano que o SISTEMA acrescenta no fim (o modelo nao
    #: oferece
    #: nada no final).
    com_jornada: bool = False


def _oferece(pedido: Pedido) -> bool:
    if pedido.modo in _MODOS_DE_SINTOMA:
        return False
    if pedido.oferecer is not None:
        return pedido.oferecer
    return pedido.modo == MODO_RESULTADO


_QUEM: Final[Mapping[str, str]] = {
    "crianca": "A conversa é sobre uma criança.",
    "gestante": "A conversa é sobre uma gestante.",
    "idoso": "A conversa é sobre uma pessoa idosa.",
}


def mensagem_do_usuario(pedido: Pedido) -> str:
    instrucao = _INSTRUCAO_DO_MODO[pedido.modo]
    if pedido.modo not in _MODOS_DE_SINTOMA:
        fecho = fecho_de_oferta(pedido.forma_da_oferta) if _oferece(pedido) else _SEM_OFERTA
        if pedido.com_jornada:
            fecho = _SEM_OFERTA_JORNADA
        instrucao = f"{instrucao} {fecho}"
    partes: list[str] = [f"[Tipo]\n{instrucao}"]
    populacao = pedido.populacao or ("crianca" if pedido.crianca else "")
    partes.append(f"[Quem]\n{_QUEM.get(populacao, 'A conversa é sobre um adulto.')}")
    resumo: list[str] = []
    if pedido.alarme_negado:
        resumo.append("a pessoa já respondeu que NÃO tem os sinais de alarme perguntados")
    if pedido.assunto_anterior:
        resumo.append(f"assunto anterior da conversa: {pedido.assunto_anterior}")
    if pedido.fatos_da_pessoa:
        resumo.append("a própria pessoa contou antes nesta conversa que " + ", ".join(pedido.fatos_da_pessoa))
    if resumo:
        partes.append("[Resumo da conversa]\n" + "; ".join(resumo) + ".")
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


#: RODADA DO DONO (18/10/2026): a UNIDADE de laboratorio sozinha ("a glicemia e' medida em mg/dL") nao e'
#: quantidade de remedio (a checagem "quantidade de medicamento" reprovava a resposta sobre exames).
_UNIDADE_DE_EXAME: Final[re.Pattern[str]] = re.compile(
    r"\b(?:g|mg|mcg|ng|pg|ui|mui|meq|mmol|umol)\s?/\s?(?:dl|l|ml|mm3|ul|h)\b", re.IGNORECASE
)


def texto_para_as_cercas(texto: str) -> str:
    """O texto que as cercas de DOSE/CONDUTA leem: sem os valores de exame com unidade de laboratorio e sem a
    frequencia anual nas frases de vacina. O texto ENVIADO nao muda."""
    t = _VALOR_DE_EXAME.sub(" [valor de exame] ", texto)
    t = _UNIDADE_DE_EXAME.sub(" [unidade de exame] ", t)
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
    # Dossie de seguranca (18/10/2026): formas que o classificador deixou passar.
    r"|\bacabar com (?:tudo|a minha vida|minha vida|a vida)\b|\bsumir de vez\b|\bme cortei\b"
    r"|\bme machuquei de proposito\b|\bqueria (?:simplesmente )?desaparecer\b|\bnao quero mais estar aqui\b"
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
#: RODADA 19/10/2026: a redacao de saude NUNCA fala de cobertura do plano (quem responde e' a consulta
#: ao plano da AMH). Qualquer mencao reprova o texto — menos o sentido anatomico ("a membrana que cobre o
#: olho", "coberta por manchas") e "cobertura vacinal".
_FALA_DE_COBERTURA: Final[re.Pattern[str]] = re.compile(
    r"(?<!\bque )\b(?:cobre|cobrem)\b|\bcobertura\b(?! vacinal)|\bcobert[oa]s?\b(?! por\b)"
    r"|\b(?:consta no rol|constam no rol|rol da ans|o plano paga|o plano custeia|o plano inclui)\b"
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


#: FASE 3 (18/10/2026): o dicionario de nomes de remedios (`medicamentos_comuns`, vocabulario ANVISA) pega o
#: que as regras de verbo sozinhas deixam passar: "use dipirona", "pode dar paracetamol", "vale usar o
#: ibuprofeno", o remedio esquecido e a combinacao de dois remedios (ou remedio com bebida) — sem chamada
#: extra
#: de modelo.
_VERBO_DE_USO: Final[re.Pattern[str]] = re.compile(
    r"(?<!\bnao )(?<!\bnunca )\b(?:use|usar|usa|uso de|der|dar|tome|tomar|toma|aplique|aplicar|passe|passar"
    r"|experimente|experimentar|ofereca|oferecer|pode usar|pode dar|vale usar|recomendo|indico|indicado o)"
    r"\b(?:\s+(?:o|a|um|uma|so|apenas|meio|um pouco de|de|do|da|com)){0,3}\s+$"
)
_ESQUECEU: Final[re.Pattern[str]] = re.compile(r"\besquec\w*|\bpulei\b|\bperdi o horario\b|\bnao tomei\b")
_JUNTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:junto|juntos|juntas|junta|com|associar|associado|combinar|combinado|misturar|misturado|e)\b"
)
#: So' quando a pessoa PERGUNTA se pode combinar ("posso tomar X com Y?"): contar o que ja' usou nao e' isso.
_PEDE_PERMISSAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:posso|pode|podem|da para|da pra|e seguro|tem problema|faz mal|tem perigo|misturar|juntar|associar"
    r"|combinar)\b"
)
_BEBIDA: Final[re.Pattern[str]] = re.compile(r"\b(?:alcool|bebida alcoolica|cerveja|vinho|cachaca|beber)\b")
_ORIENTA_PROFISSIONAL: Final[re.Pattern[str]] = re.compile(r"\b(?:medic[oa]|farmaceutic[oa]|bula)\b")


def _violacoes_de_remedio(plano: str, pessoa: str) -> list[Violacao]:
    """As regras que dependem do NOME do remedio (texto redigido `plano`, mensagem da pessoa `pessoa`, os dois
    normalizados)."""
    from maezo.conhecimento.medicamentos_comuns import PADRAO_DE_NOME, nomes_no_texto

    achados: list[Violacao] = []
    for m in PADRAO_DE_NOME.finditer(plano):
        antes = plano[max(0, m.start() - 40) : m.start()]
        if _VERBO_DE_USO.search(antes):
            achados.append(
                Violacao("manda_usar_remedio", f"{antes.split()[-1] if antes.split() else ''} {m.group(0)}")
            )
            break
    nomes_da_pessoa = nomes_no_texto(pessoa)
    sem_profissional = not _ORIENTA_PROFISSIONAL.search(plano)
    if nomes_da_pessoa and _ESQUECEU.search(pessoa) and sem_profissional:
        achados.append(Violacao("remedio_esquecido_sem_orientacao"))
    combinacao = len(nomes_da_pessoa) >= 2 or (nomes_da_pessoa and _BEBIDA.search(pessoa))
    if combinacao and _JUNTO.search(pessoa) and _PEDE_PERMISSAO.search(pessoa) and sem_profissional:
        achados.append(Violacao("combinacao_sem_orientacao"))
    return achados


#: DOSSIE (18/10/2026): o texto fala da PROPRIA redacao ("vou reescrever", "sem essas palavras", "versao
#: corrigida", "a verificacao") — nunca sai para a pessoa.
_METATEXTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:reescrev\w*|reescrit\w*|sem essas palavras|versao (?:corrigida|reescrita|anterior)"
    r"|verificacao automatica|as regras (?:que|do sistema)|o sistema bloqueia|nao posso usar a palavra)\b"
)
#: DOSSIE (18/10/2026): pressao ALTA relatada nunca e' descartada como erro de medicao.
_PRESSAO_NA_PESSOA: Final[re.Pattern[str]] = re.compile(r"\bpressao\b")
_DESCARTA_MEDICAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:erro|falha|engano) (?:de|na|da) (?:medic\w*|afericao|leitura)\b|\bmedi\w* errad\w*\b"
    r"|\baparelho (?:descalibrad\w*|com defeito|errado)\b|\b(?:medir|afira|aferir|confira|conferir) de novo\b"
    r"|\brepet\w* a (?:medicao|afericao)\b|\bpode ter sido (?:erro|engano)\b"
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
        ("metatexto", _METATEXTO),
        ("fala_de_cobertura", _FALA_DE_COBERTURA),
    ):
        m = padrao.search(plano)
        if m:
            achados.append(Violacao(nome, m.group(0)))
    pessoa = bs.normalizar(texto_da_pessoa)
    achados += _violacoes_de_remedio(plano, pessoa)
    if _PRESSAO_NA_PESSOA.search(pessoa) and (m_med := _DESCARTA_MEDICAO.search(plano)):
        achados.append(Violacao("descarta_pressao_alta", m_med.group(0)))
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
    "manda_usar_remedio": "nao mande usar, dar ou aplicar nenhum remedio pelo nome; quem indica e' o "
    "medico ou o "
    "farmaceutico",
    "remedio_esquecido_sem_orientacao": "sobre o remedio esquecido, oriente falar com o medico ou o "
    "farmaceutico "
    "e ler a bula (sem dizer o que fazer)",
    "combinacao_sem_orientacao": "sobre usar remedios juntos (ou com bebida), oriente falar com o "
    "medico ou o "
    "farmaceutico, sem dizer se pode ou nao",
    "dose_esquecida": "nao diga o que fazer com dose esquecida; oriente o medico ou o farmaceutico e a bula",
    "libera_combinacao": "nao diga que a combinacao e' segura ou liberada; oriente o medico ou o "
    "farmaceutico",
    "minimiza": "nao minimize ('nao e' grave', 'fique tranquilo', 'e' normal')",
    "diagnostico": "nao diga o que a pessoa tem ('voce tem', 'voce esta com', 'deve ser')",
    "promessa": "nao prometa agendar, marcar nem que alguem vai entrar em contato",
    "fala_de_cobertura": "tire qualquer mencao a cobertura do plano (cobre, cobertura, coberto, rol, o "
    "plano paga): fale so' de saude",
    "metatexto": "escreva SO' a resposta para a pessoa, sem falar da reescrita, das regras ou das palavras",
    "descarta_pressao_alta": "nunca diga que a pressao pode ser erro de medicao nem peca para medir de novo; "
    "oriente procurar avaliacao medica (e 192 se houver sinal de alarme)",
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


#: DOSSIE DE SEGURANCA (18/10/2026): o PREAMBULO de reescrita que vazou para a pessoa ("Claro, vou reescrever
#: sem essas palavras.", "Aqui esta' a versao reescrita:"). Tirado do comeco; se sobrar metatexto no meio, a
#: checagem (`metatexto`) reprova.
_PREAMBULO: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:claro|certo|ok|entendido|perfeito|sem problemas?)[,.!]?\s*)?"
    r"(?:(?:vou|irei|posso) (?:reescrever|refazer|corrigir|ajustar)[^.:!\n]*[.:!]"
    r"|(?:aqui (?:esta|está|vai)|segue)[^.:!\n]{0,60}"
    r"(?:reescrit|corrigid|nova vers|versao|versão|texto)[^.:!\n]*[.:!]"
    r"|(?:versao|versão) (?:reescrita|corrigida)[^.:!\n]*[.:!])\s*",
    re.IGNORECASE,
)


def sem_preambulo(texto: str) -> str:
    """Tira o preambulo de reescrita do comeco do texto ("Claro, vou reescrever sem essas palavras.")."""
    t = texto.strip()
    for _ in range(2):
        novo = _PREAMBULO.sub("", t, count=1).strip()
        if novo == t:
            break
        t = novo
    return t


def sem_abertura_repetida(texto: str) -> str:
    """ "Entendi. Entendi: ..." / "Oi! Oi!" -> uma vez so' (a ultima, com a pontuacao dela)."""
    t = sem_preambulo(texto)
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
    #: FASE 3 (18/10/2026): a busca por SIGNIFICADO na base (`busca_semantica.BuscaSemantica`), so' com
    #: `MAEZO_HELENA_BUSCA_SEMANTICA`; `None` = o lexico de antes.
    busca: Any = None

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
